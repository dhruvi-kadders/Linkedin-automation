// backend/src/bot/easy-apply.js
// LinkedIn Easy Apply form automation with tracked question capture.

const path = require('path');
const { humanType, humanScroll, sleep, randomInt } = require('../utils/humanize');
const { applications, applicationQuestions } = require('../db');
const { compareJobIdentity } = require('./linkedin-job-utils');

const MODAL_SEL = [
  '.jobs-easy-apply-modal',
  '[data-test-modal-id="easy-apply-modal"]',
  '.artdeco-modal[role="dialog"]',
  '.jobs-easy-apply-content',
].join(', ');

const APPLICATION_ENTRY_TEXT_RE = /easy apply/i;

const EASY_APPLY_BTN_SEL = [
  'button.jobs-apply-button[aria-label*="Easy Apply"]',
  'button[aria-label*="Easy Apply"]',
].join(', ');

const normalize = (value) => String(value || '').trim().toLowerCase();

const roleMatches = (jobRole, scope) => {
  const a = normalize(jobRole);
  const b = normalize(scope);
  if (!b) return true;
  if (!a) return false;
  return a === b || a.includes(b) || b.includes(a);
};

const buildQuestionSignature = (questions) =>
  questions
    .map((q) => q.question_key || `${q.question_text}|${q.field_type}`)
    .sort()
    .join('||');

const getPendingQuestions = (questions) =>
  questions.filter((q) => q.is_required && !q.is_answered);

const getBlockingQuestions = (questions) => {
  const requiredUnanswered = questions.filter((q) => q.is_required && !q.is_answered);
  if (requiredUnanswered.length) return requiredUnanswered;

  const unanswered = questions.filter((q) => !q.is_answered);
  if (unanswered.length) return unanswered;

  return questions;
};

const collapseWhitespace = (value) => String(value || '').replace(/\s+/g, ' ').trim();

const PLACEHOLDER_SELECTION_VALUES = new Set([
  'select',
  'select an option',
  'select one',
  'please select',
  'please make a selection',
  'make a selection',
  'choose an option',
  'choose one',
]);

const normalizeFieldValue = (value) =>
  collapseWhitespace(value)
    .toLowerCase()
    .replace(/^[\s\-:]+|[\s\-:]+$/g, '');

const isPlaceholderSelectionValue = (fieldType, questionText, value) => {
  if (!['select', 'combobox'].includes(normalize(fieldType))) return false;

  const normalizedValue = normalizeFieldValue(value);
  if (!normalizedValue) return false;

  const normalizedQuestion = normalizeFieldValue(questionText);
  return PLACEHOLDER_SELECTION_VALUES.has(normalizedValue) || (!!normalizedQuestion && normalizedValue === normalizedQuestion);
};

const sanitizeCapturedQuestion = (question) => {
  const sanitized = { ...(question || {}) };
  const currentValue = collapseWhitespace(sanitized.currentValue);
  sanitized.currentValue = currentValue;

  if (isPlaceholderSelectionValue(sanitized.field_type, sanitized.question_text, currentValue)) {
    sanitized.currentValue = '';
    sanitized.is_answered = false;
  }

  return sanitized;
};

const truncateForLog = (value, max = 120) => {
  const text = collapseWhitespace(value);
  if (text.length <= max) return text;
  return `${text.slice(0, max - 3)}...`;
};

const looksLikeRepeatedLeadingText = (value) => {
  const text = collapseWhitespace(value);
  if (text.length < 8) return false;

  const maxLen = Math.floor(text.length / 2);
  for (let len = maxLen; len >= 4; len--) {
    const first = text.slice(0, len).trim();
    const second = text.slice(len, len * 2).trim();
    if (first && first.length >= 4 && first === second) {
      return true;
    }
  }

  return false;
};

const buildLabelDebugMessage = (field, stepIndex, fieldIndex) => {
  const debug = field.label_debug || {};
  return [
    `Label debug [step ${stepIndex} field ${fieldIndex + 1}]`,
    `type=${field.field_type || 'unknown'}`,
    `chosen="${truncateForLog(field.question_text)}"`,
    `explicit="${truncateForLog(debug.explicitLabel)}"`,
    `container="${truncateForLog(debug.containerLabel)}"`,
    `groupHeading="${truncateForLog(debug.groupHeading)}"`,
    `groupLabel="${truncateForLog(debug.groupLabel)}"`,
    `aria="${truncateForLog(debug.ariaLabel)}"`,
    `placeholder="${truncateForLog(debug.placeholder)}"`,
    `name="${truncateForLog(debug.name)}"`,
  ].join(' | ');
};

const buildLabelDebugHtmlMessage = (field, stepIndex, fieldIndex) => {
  const debug = field.label_debug || {};
  return [
    `Label debug HTML [step ${stepIndex} field ${fieldIndex + 1}]`,
    `chosen="${truncateForLog(field.question_text)}"`,
    `explicitHtml="${truncateForLog(debug.explicitHtml, 280)}"`,
    `explicitInnerHtml="${truncateForLog(debug.explicitInnerHtml, 280)}"`,
  ].join(' | ');
};

const logFieldLabelDebug = (fields, stepIndex, logger) => {
  if (!logger || !fields?.length) return;

  fields.forEach((field, index) => {
    const debugValues = Object.values(field.label_debug || {});
    const suspicious =
      looksLikeRepeatedLeadingText(field.question_text) ||
      debugValues.some((value) => looksLikeRepeatedLeadingText(value));

    const message = buildLabelDebugMessage(field, stepIndex, index);
    if (suspicious) {
      logger.info(message);
      if (field.label_debug?.explicitHtml) {
        logger.info(buildLabelDebugHtmlMessage(field, stepIndex, index));
      }
      return;
    }

    logger.debug(message);
  });
};

const isApplyFlowUrl = (value) => {
  const text = String(value || '');
  return /\/jobs\/view\/\d+\/apply\/?/i.test(text) || /openSDUIApplyFlow=true/i.test(text);
};

const isApplicationEntryLabel = (value) => APPLICATION_ENTRY_TEXT_RE.test(collapseWhitespace(value));

const findVisibleEasyApplyButton = async (page) => {
  try {
    const roleButtons = page.getByRole('button', { name: APPLICATION_ENTRY_TEXT_RE });
    const count = await roleButtons.count().catch(() => 0);
    for (let i = 0; i < count; i++) {
      const btn = roleButtons.nth(i);
      if (await btn.isVisible().catch(() => false)) return btn;
    }
  } catch {
    // fall through to CSS lookup
  }

  const cssButtons = await page.$$(EASY_APPLY_BTN_SEL).catch(() => []);
  for (const btn of cssButtons) {
    if (!(await btn.isVisible().catch(() => false))) continue;
    const label = `${(await btn.textContent().catch(() => '')) || ''} ${(await btn.getAttribute('aria-label').catch(() => '')) || ''}`;
    if (isApplicationEntryLabel(label)) return btn;
  }

  return null;
};

const waitForApplicationSurface = async (page) => {
  const opened = await page.waitForSelector(MODAL_SEL, { timeout: 8000 }).then(() => true).catch(() => false);
  if (!opened) return false;

  await page.waitForLoadState('domcontentloaded', { timeout: 5000 }).catch(() => {});
  await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});
  await sleep(randomInt(800, 1400));

  return true;
};

const openEasyApplyModal = async (page, logger) => {
  let btn = null;

  for (let attempt = 1; attempt <= 3; attempt++) {
    btn = await findVisibleEasyApplyButton(page);
    if (btn) break;
    await page.waitForLoadState('networkidle', { timeout: 4000 }).catch(() => {});
    await sleep(randomInt(800, 1400));
  }

  if (!btn) {
    logger.warn('Easy Apply button not found on detail pane');
    return false;
  }

  const label = (await btn.getAttribute('aria-label')) || (await btn.textContent()) || '';
  logger.info(`Clicking button: "${label.trim()}"`);
  await btn.scrollIntoViewIfNeeded().catch(() => {});
  await sleep(randomInt(500, 900));

  await btn.click({ timeout: 5000 }).catch(async () => {
    await page.evaluate(() => {
      const controls = [...document.querySelectorAll('button')];
      const easyApply = controls.find((el) => {
        const text = (el.textContent || '').trim();
        const aria = (el.getAttribute('aria-label') || '').trim();
        return (
          /easy apply/i.test(text) ||
          /easy apply/i.test(aria)
        );
      });
      if (easyApply) easyApply.click();
    });
  });

  const surface = await waitForApplicationSurface(page);
  if (surface) {
    logger.info('Easy Apply modal opened');
    return true;
  }

  logger.warn('Easy Apply modal did not open');
  return false;
};

const hasInlineEasyApplyContext = async (page) => {
  const btn = await findVisibleEasyApplyButton(page);
  return !!btn;
};

const readCurrentJobContext = async (page) => {
  return page.evaluate(() => {
    const extractJobId = (value) => {
      const text = String(value || '').trim();
      if (!text) return '';

      const pathMatch = text.match(/\/jobs\/view\/(\d+)/i);
      if (pathMatch?.[1]) return pathMatch[1];

      const queryMatch = text.match(/[?&#](?:currentJobId|jobId)=(\d+)/i);
      if (queryMatch?.[1]) return queryMatch[1];

      const rawIdMatch = text.match(/^\d+$/);
      return rawIdMatch?.[0] || '';
    };

    const readText = (root, selectors) => {
      for (const selector of selectors) {
        const value = root.querySelector(selector)?.textContent?.trim();
        if (value) return value;
      }
      return '';
    };

    const detailRoot =
      document.querySelector('.jobs-search__job-details--container') ||
      document.querySelector('.scaffold-layout__detail') ||
      document.querySelector('.jobs-details') ||
      document.querySelector('.job-view-layout') ||
      document;

    const titleSelectors = [
      'h1.job-details-jobs-unified-top-card__job-title',
      '.jobs-unified-top-card__job-title h1',
      'h1.t-24',
      '.job-view-layout h1',
      'h1',
    ];

    const companySelectors = [
      '.job-details-jobs-unified-top-card__company-name a',
      '.job-details-jobs-unified-top-card__company-name',
      '.jobs-unified-top-card__company-name a',
      '.jobs-unified-top-card__company-name',
    ];

    const locationSelectors = [
      '.job-details-jobs-unified-top-card__primary-description-container .tvm__text',
      '.job-details-jobs-unified-top-card__bullet',
      '.jobs-unified-top-card__bullet',
      '.jobs-unified-top-card__primary-description-container .tvm__text',
    ];

    const title = readText(detailRoot, titleSelectors) || readText(document, titleSelectors);
    const company = readText(detailRoot, companySelectors) || readText(document, companySelectors);
    const location = readText(detailRoot, locationSelectors) || readText(document, locationSelectors);

    const idCandidates = [
      window.location.href,
      detailRoot.querySelector('button.jobs-save-button')?.getAttribute('data-job-id'),
      detailRoot.querySelector('button.jobs-save-button')?.dataset?.jobId,
      detailRoot.querySelector('button.jobs-apply-button')?.getAttribute('data-job-id'),
      detailRoot.querySelector('button.jobs-apply-button')?.dataset?.jobId,
      detailRoot.querySelector('[data-job-id]')?.getAttribute('data-job-id'),
      detailRoot.querySelector('[data-occludable-job-id]')?.getAttribute('data-occludable-job-id'),
      detailRoot.querySelector('a[href*="/jobs/view/"]')?.href,
      document.querySelector('[aria-current="true"] a[href*="/jobs/view/"]')?.href,
    ];

    const jobId = idCandidates.map(extractJobId).find(Boolean) || '';
    return {
      jobId,
      url: jobId ? `https://www.linkedin.com/jobs/view/${jobId}/` : window.location.href,
      title,
      company,
      location,
    };
  });
};

const formatJobLabel = (job) => {
  const title = String(job?.title || '').trim() || 'Unknown title';
  const company = String(job?.company || '').trim() || 'Unknown company';
  return `"${title}" @ "${company}"`;
};

const buildJobMismatchMessage = (expected, actual) => {
  const seenLabel =
    actual?.title || actual?.company
      ? ` Saw ${formatJobLabel(actual)}.`
      : '';

  const actualUrl =
    actual?.url && actual.url !== expected?.url
      ? ` Opened URL: ${actual.url}`
      : '';

  return `LinkedIn opened a different job than expected for ${formatJobLabel(expected)}.${seenLabel}${actualUrl}`.trim();
};

const confirmExpectedJobContext = async (page, job) => {
  const expected = {
    jobId: job.jobId,
    url: job.url,
    title: job.title,
    company: job.company,
  };

  let actual = {};
  let comparison = compareJobIdentity(expected, actual);

  for (let attempt = 1; attempt <= 4; attempt++) {
    actual = await readCurrentJobContext(page).catch(() => ({}));
    comparison = compareJobIdentity(expected, actual);

    if (comparison.matches) {
      return { ok: true, actual, comparison };
    }

    if (attempt < 4) {
      await page.waitForLoadState('networkidle', { timeout: 4000 }).catch(() => {});
      await sleep(randomInt(700, 1200));
    }
  }

  return { ok: false, actual, comparison };
};

const extractFields = async (page) => {
  return page.evaluate((modalSel) => {
    const isApplyPage =
      /\/jobs\/view\/\d+\/apply\/?/i.test(window.location.href) ||
      /openSDUIApplyFlow=true/i.test(window.location.href);

    const cssEscape = (value) => {
      if (typeof CSS !== 'undefined' && CSS.escape) return CSS.escape(value);
      return String(value).replace(/["\\]/g, '\\$&');
    };

    const collapseRepeatedText = (value) => {
      const text = String(value || '').replace(/\s+/g, ' ').trim();
      if (!text) return '';

      const half = text.length / 2;
      if (Number.isInteger(half)) {
        const left = text.slice(0, half).trim();
        const right = text.slice(half).trim();
        if (left && left === right) return left;
      }

      return text;
    };

    const readText = (node) => {
      if (!node) return '';

      const clone = node.cloneNode(true);
      clone
        .querySelectorAll('.visually-hidden, .artdeco-visually-hidden, .screen-reader-text, .sr-only')
        .forEach((el) => el.remove());

      return collapseRepeatedText(clone.textContent || node.textContent || '');
    };

    const isVisible = (el) => {
      if (!el) return false;
      const style = window.getComputedStyle(el);
      if (style.display === 'none' || style.visibility === 'hidden') return false;
      const rect = el.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    };

    const isCandidateField = (el) =>
      !!el &&
      el.type !== 'hidden' &&
      !el.disabled &&
      isVisible(el) &&
      !el.closest('header, nav, [role="search"], .search-global-typeahead, .jobs-search-box');

    const findFirstVisible = (selector, root = document) =>
      [...root.querySelectorAll(selector)].find(isVisible) || null;

    const countCandidateInputs = (root) => {
      if (!root) return 0;

      return [...root.querySelectorAll('input, select, textarea')]
        .filter(isCandidateField)
        .length;
    };

    const detailRoot =
      findFirstVisible('.jobs-search__job-details--container') ||
      findFirstVisible('.scaffold-layout__detail') ||
      findFirstVisible('.jobs-details') ||
      findFirstVisible('.job-view-layout') ||
      null;

    const actionSelectors = [
      'button[aria-label*="Submit application"]',
      'footer button[aria-label*="Submit"]',
      'button[aria-label*="Review your application"]',
      'button[aria-label*="Continue to next step"]',
      'button[aria-label*="Next"]',
      '.jobs-easy-apply-modal footer .artdeco-button--primary',
      '.artdeco-modal__actionbar .artdeco-button--primary',
    ];

    const actionButton = actionSelectors
      .map((selector) => findFirstVisible(selector))
      .find((button) => {
        return !!button && !button.disabled && button.getAttribute('aria-disabled') !== 'true';
      }) || null;

    const actionRoot = actionButton?.closest(
      [
        '[data-live-test-job-apply-page]',
        'form[action*="/apply"]',
        '.jobs-easy-apply-modal',
        '.jobs-easy-apply-content',
        '.artdeco-modal[role="dialog"]',
        '.artdeco-modal',
        '.jobs-search__job-details--container',
        '.scaffold-layout__detail',
        '.jobs-details',
        '.job-view-layout',
        '.scaffold-layout__main',
        'main',
      ].join(', ')
    ) || null;

    const preferredRoots = [
      actionRoot,
      findFirstVisible(modalSel),
      findFirstVisible('[data-live-test-job-apply-page]'),
      findFirstVisible('form[action*="/apply"]'),
      detailRoot,
      isApplyPage ? document.body : null,
    ].filter(Boolean);

    const modal =
      preferredRoots.find((root) => countCandidateInputs(root) > 0) ||
      preferredRoots[0] ||
      null;

    if (!modal) return [];

    const getQuestionContainer = (el) =>
      el.closest('fieldset, .fb-dash-form-element, .jobs-easy-apply-form-section__grouping, .artdeco-form-item');

    const getGroupLabelDetails = (el) => {
      const container = getQuestionContainer(el);
      const result = {
        chosen: '',
        heading: '',
        label: '',
      };

      if (!container) return result;

      const headings = [
        ...container.querySelectorAll(
          'legend, span.fb-dash-form-element__label, .fb-dash-form-element__label-title, h2, h3, h4'
        ),
      ];

      for (const heading of headings) {
        const text = readText(heading);
        if (text) {
          result.heading = text;
          result.chosen = text;
          return result;
        }
      }

      const labels = [...container.querySelectorAll('label')];
      for (const labelEl of labels) {
        const text = readText(labelEl);
        if (!text) continue;

        const forId = labelEl.getAttribute('for');
        result.label = text;
        if (!forId) {
          result.chosen = text;
          return result;
        }

        const target = container.querySelector(`#${cssEscape(forId)}`);
        if (target && (target.type === 'radio' || target.type === 'checkbox')) continue;
        result.chosen = text;
        return result;
      }

      return result;
    };

    const getLabelDetails = (el) => {
      const details = {
        chosen: '',
        explicitLabel: '',
        explicitHtml: '',
        explicitInnerHtml: '',
        containerLabel: '',
        groupHeading: '',
        groupLabel: '',
        ariaLabel: el.getAttribute('aria-label')?.trim() || '',
        placeholder: el.getAttribute('placeholder')?.trim() || '',
        name: el.name || '',
      };

      if (el.type === 'radio') {
        const groupDetails = getGroupLabelDetails(el);
        details.groupHeading = groupDetails.heading;
        details.groupLabel = groupDetails.label;
        if (groupDetails.chosen) {
          details.chosen = groupDetails.chosen;
          return details;
        }
      }

      if (el.id) {
        const lbl = modal.querySelector(`label[for="${cssEscape(el.id)}"]`);
        const text = readText(lbl);
        if (text) {
          details.explicitLabel = text;
          details.explicitHtml = lbl?.outerHTML || '';
          details.explicitInnerHtml = lbl?.innerHTML || '';
          details.chosen = text;
          return details;
        }
      }

      const closest = getQuestionContainer(el);
      if (closest) {
        const lbl = closest.querySelector('label, legend, h3, span.fb-dash-form-element__label');
        const text = readText(lbl);
        if (text) {
          details.containerLabel = text;
          details.chosen = text;
          return details;
        }
      }

      details.chosen = details.ariaLabel || details.placeholder || details.name || '';
      return details;
    };

    const buildSelector = (el) => {
      const tag = el.tagName.toLowerCase();
      if (el.id) return `${tag}#${cssEscape(el.id)}`;
      if (el.name) return `${tag}[name="${cssEscape(el.name)}"]`;
      return null;
    };

    const buildQuestionKey = (parts) => parts.filter(Boolean).join('|').toLowerCase();

    const fields = [];
    const seenRadio = new Set();
    const seenCheckbox = new Set();
    const allInputs = [...modal.querySelectorAll('input, select, textarea')].filter(isCandidateField);

    allInputs.forEach((el) => {
      const labelDetails = getLabelDetails(el);
      const label = labelDetails.chosen;
      const required = !!(
        el.required ||
        el.getAttribute('aria-required') === 'true' ||
        /\*/.test(label)
      );

      if (el.tagName === 'SELECT') {
        const selector = buildSelector(el);
        const selected = el.options[el.selectedIndex];
        const currentValue = selected?.text?.trim() || el.value || '';
        const options = [...el.options].map((o) => o.text.trim()).filter(Boolean);
        fields.push({
          question_text: label,
          question_key: buildQuestionKey([label, 'select', selector]),
          selector,
          field_type: 'select',
          options,
          currentValue,
          is_required: required,
          is_answered: !required || !!el.value,
          label_debug: labelDetails,
        });
        return;
      }

      if (el.type === 'radio') {
        const groupKey = el.name || el.id;
        if (seenRadio.has(groupKey)) return;
        seenRadio.add(groupKey);

        const groupEls = modal.querySelectorAll(`input[type="radio"][name="${cssEscape(el.name)}"]`);
        const selected = [...groupEls].find((r) => r.checked);
        const options = [...groupEls].map((r) => {
          const lbl = modal.querySelector(`label[for="${cssEscape(r.id)}"]`);
          return readText(lbl) || r.value;
        }).filter(Boolean);

        const selectedLabel = selected
          ? (readText(modal.querySelector(`label[for="${cssEscape(selected.id)}"]`)) || selected.value || '')
          : '';

        fields.push({
          question_text: label || el.name || '',
          question_key: buildQuestionKey([label || el.name, 'radio', el.name]),
          selector: el.name ? `input[name="${cssEscape(el.name)}"]` : buildSelector(el),
          field_type: 'radio',
          options,
          currentValue: selectedLabel,
          is_required: required || [...groupEls].some((r) => r.required || r.getAttribute('aria-required') === 'true'),
          is_answered: !!selected,
          label_debug: labelDetails,
        });
        return;
      }

      if (el.type === 'checkbox') {
        const groupDetails = getGroupLabelDetails(el);
        const groupLabel = groupDetails.chosen;
        const container = getQuestionContainer(el);
        let groupEls = [];

        if (el.name) {
          groupEls = [...modal.querySelectorAll(`input[type="checkbox"][name="${cssEscape(el.name)}"]`)];
        }

        if (groupEls.length <= 1 && container) {
          groupEls = [...container.querySelectorAll('input[type="checkbox"]')];
        }

        if (!groupEls.length) groupEls = [el];

        const groupKey =
          el.name ||
          groupLabel ||
          groupEls.map((item) => item.id).filter(Boolean).join('|') ||
          el.id;

        if (seenCheckbox.has(groupKey)) return;
        seenCheckbox.add(groupKey);

        const optionLabels = [...new Set(groupEls.map((item) => {
          const lbl = item.id ? modal.querySelector(`label[for="${cssEscape(item.id)}"]`) : null;
          return readText(lbl) || item.value || '';
        }).filter(Boolean))];

        const selectedLabels = groupEls
          .filter((item) => item.checked)
          .map((item) => {
            const lbl = item.id ? modal.querySelector(`label[for="${cssEscape(item.id)}"]`) : null;
            return readText(lbl) || item.value || '';
          })
          .filter(Boolean);

        const selector =
          groupEls.length > 1
            ? (el.name
                ? `input[type="checkbox"][name="${cssEscape(el.name)}"]`
                : (groupEls.every((item) => item.id)
                    ? groupEls.map((item) => `input#${cssEscape(item.id)}`).join(', ')
                    : buildSelector(el)))
            : buildSelector(el);

        const isRequired =
          required ||
          groupEls.some((item) => item.required || item.getAttribute('aria-required') === 'true');

        fields.push({
          question_text: groupLabel || label || el.name || '',
          question_key: buildQuestionKey([groupLabel || label || el.name, 'checkbox', groupKey]),
          selector,
          field_type: 'checkbox',
          options: optionLabels.length > 1 ? optionLabels : ['Yes'],
          currentValue: selectedLabels.join(', '),
          is_required: isRequired,
          is_answered: selectedLabels.length > 0 || !isRequired,
          label_debug: {
            ...labelDetails,
            groupHeading: groupDetails.heading,
            groupLabel: groupDetails.label,
          },
        });
        return;
      }

      const selector = buildSelector(el);
      const currentValue = String(el.value || '').trim();
      fields.push({
        question_text: label,
        question_key: buildQuestionKey([label, el.type || el.tagName.toLowerCase(), selector]),
        selector,
        field_type: el.tagName === 'TEXTAREA' ? 'textarea' : (el.type || 'text'),
        options: [],
        currentValue,
        is_required: required,
        is_answered: currentValue.length > 0 || !required,
        label_debug: labelDetails,
      });
    });

    return fields.filter((field) => field.question_text);
  }, MODAL_SEL);
};

const findAnswer = (label, qaTemplates, jobRole) => {
  const lower = normalize(label);
  const candidates = qaTemplates
    .filter((template) => normalize(template.answer))
    .filter((template) => lower.includes(normalize(template.question_pattern)))
    .filter((template) => !template.job_title_scope || roleMatches(jobRole, template.job_title_scope))
    .map((template) => ({
      ...template,
      score:
        (parseInt(template.priority, 10) || 0) +
        (template.account_id ? 100 : 0) +
        (template.job_title_scope ? 1000 : 0),
    }))
    .sort((a, b) => b.score - a.score);

  return candidates[0] || null;
};

const dedupeCapturedQuestions = (questions) => {
  const seen = new Map();

  for (const rawQuestion of questions || []) {
    const question = sanitizeCapturedQuestion(rawQuestion);
    if (!question?.question_text) continue;

    const key = collapseWhitespace(
      question.question_key ||
      `${question.question_text}|${question.field_type || 'text'}|${question.selector || ''}`
    ).toLowerCase();

    const existing = seen.get(key);
    if (!existing) {
      seen.set(key, question);
      continue;
    }

    seen.set(key, {
      ...existing,
      ...question,
      options: question.options?.length ? question.options : existing.options,
      currentValue: question.currentValue || existing.currentValue || '',
      is_required: existing.is_required || question.is_required,
      is_answered: existing.is_answered || question.is_answered,
      label_debug: question.label_debug || existing.label_debug,
    });
  }

  return [...seen.values()];
};

const applyTemplateToField = async (page, field, template, logger) => {
  if (field.is_answered && field.currentValue) return true;
  if (!template || !field.selector) return false;

  logger.info(`Filling "${field.question_text}" -> "${template.answer}"`);

  try {
    if (field.field_type === 'select') {
      await page.waitForSelector(field.selector, { timeout: 5000 });
      const matched = await page.evaluate(({ selector, answer }) => {
        const el = document.querySelector(selector);
        if (!el) return false;

        for (const opt of el.options) {
          if (opt.text.toLowerCase().includes(answer.toLowerCase())) {
            el.value = opt.value;
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
          }
        }

        for (const opt of el.options) {
          if (opt.value.toLowerCase().includes(answer.toLowerCase())) {
            el.value = opt.value;
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
          }
        }

        return false;
      }, { selector: field.selector, answer: template.answer });

      await sleep(randomInt(200, 500));
      return matched;
    }

    if (field.field_type === 'radio') {
      const matched = await page.evaluate(({ selector, answer }) => {
        const readLabelText = (node) => {
          if (!node) return '';

          const clone = node.cloneNode(true);
          clone
            .querySelectorAll('.visually-hidden, .artdeco-visually-hidden, .screen-reader-text, .sr-only')
            .forEach((el) => el.remove());

          const text = (clone.textContent || node.textContent || '').replace(/\s+/g, ' ').trim();
          if (!text) return '';

          const half = text.length / 2;
          if (Number.isInteger(half)) {
            const left = text.slice(0, half).trim();
            const right = text.slice(half).trim();
            if (left && left === right) return left;
          }

          return text;
        };

        const nodes = [...document.querySelectorAll(selector)];
        const lower = answer.toLowerCase();

        for (const node of nodes) {
          const label = (readLabelText(document.querySelector(`label[for="${node.id}"]`)) || node.value || '')
            .trim()
            .toLowerCase();
          if (label.includes(lower) || lower.includes(label)) {
            node.click();
            return true;
          }
        }

        return false;
      }, { selector: field.selector, answer: template.answer });

      await sleep(randomInt(200, 400));
      return matched;
    }

    if (field.field_type === 'checkbox') {
      const answer = normalize(template.answer);
      if (answer === 'yes' || answer === 'true' || answer === '1') {
        const el = await page.$(field.selector);
        const checked = await el?.isChecked();
        if (!checked) await el?.click();
      }
      await sleep(randomInt(100, 300));
      return true;
    }

    const el = await page.$(field.selector);
    if (!el) return false;
    await el.scrollIntoViewIfNeeded();
    await el.click({ clickCount: 3 });
    await el.fill('');
    await sleep(randomInt(100, 250));
    await humanType(page, field.selector, template.answer, { clear: false, speed: 'fast' });
    await sleep(randomInt(200, 400));
    return true;
  } catch (err) {
    logger.warn(`Fill error for "${field.question_text}": ${err.message}`);
    return false;
  }
};

const getValidationMessages = async (page) => {
  return page.evaluate((modalSel) => {
    const modal = document.querySelector(modalSel) || document;
    const selectors = [
      '.artdeco-inline-feedback__message',
      '.artdeco-form__error-msg',
      '[aria-live="assertive"]',
      '.fb-dash-form-element__error',
    ];

    const messages = new Set();
    selectors.forEach((selector) => {
      modal.querySelectorAll(selector).forEach((el) => {
        const text = el.textContent?.trim();
        if (text) messages.add(text);
      });
    });

    return [...messages];
  }, MODAL_SEL);
};

const persistStepQuestions = async (appId, account, job, stepIndex, questions) => {
  if (!appId) return;

  const uniqueQuestions = dedupeCapturedQuestions(questions).map((question) => ({
    ...question,
    step_index: stepIndex,
  }));

  await applicationQuestions.upsertMany(
    appId,
    {
      account_id: account.id,
      search_config_id: job.config_id || null,
      job_title_scope: job.job_role || null,
      job_title: job.title || null,
      company_name: job.company || null,
      step_index: stepIndex,
    },
    uniqueQuestions.map((question) => ({
      question_text: question.question_text,
      field_type: question.field_type,
      options: question.options,
      answer: question.currentValue || null,
      is_required: question.is_required,
      is_answered: question.is_answered,
      step_index: stepIndex,
    }))
  );
};

const fillCurrentStep = async (page, qaTemplates, job, stepIndex, logger) => {
  const beforeFields = dedupeCapturedQuestions(await extractFields(page));
  logger.info(`Fields in this step: ${beforeFields.length}`);
  logFieldLabelDebug(beforeFields, stepIndex, logger);

  let filled = 0;

  for (const field of beforeFields) {
    const template = findAnswer(field.question_text, qaTemplates, job.job_role);
    const ok = await applyTemplateToField(page, field, template, logger);
    if (ok) filled++;
  }

  const afterFields = dedupeCapturedQuestions(await extractFields(page));
  const afterMap = new Map(afterFields.map((field) => [field.question_key, field]));

  const questions = dedupeCapturedQuestions(beforeFields.map((field) => {
    const latest = afterMap.get(field.question_key) || field;
    return { ...latest, step_index: stepIndex };
  }));

  const pendingQuestions = getPendingQuestions(questions);
  logger.info(`Filled ${filled}/${beforeFields.length} fields`);

  if (pendingQuestions.length) {
    logger.warn(`Required questions still unanswered: ${pendingQuestions.map((q) => q.question_text).join(' | ')}`);
  }

  return {
    questions,
    pendingQuestions,
    signature: buildQuestionSignature(questions),
  };
};

const handleResumeUpload = async (page, resumePath, logger) => {
  if (!resumePath) return;

  try {
    const fileInput = await page.$('input[type="file"]');
    if (!fileInput) return;

    const uploadedLabel = await page.$('.jobs-document-upload__filename, .jobs-resume-picker__resume-name');
    if (uploadedLabel) {
      logger.info('Resume already present in modal');
      return;
    }

    const abs = path.resolve(resumePath);
    await fileInput.setInputFiles(abs);
    await sleep(randomInt(2000, 3500));
    logger.info('Resume uploaded');
  } catch (err) {
    logger.warn(`Resume upload failed: ${err.message}`);
  }
};

const getFooterAction = async (page) => {
  const checks = [
    { sel: 'button[aria-label*="Submit application"]', type: 'submit' },
    { sel: 'footer button[aria-label*="Submit"]', type: 'submit' },
    { sel: 'button[aria-label*="Review your application"]', type: 'review' },
    { sel: 'button[aria-label*="Continue to next step"]', type: 'next' },
    { sel: 'button[aria-label*="Next"]', type: 'next' },
    { sel: '.jobs-easy-apply-modal footer .artdeco-button--primary', type: 'primary' },
    { sel: '.artdeco-modal__actionbar .artdeco-button--primary', type: 'primary' },
  ];

  for (const { sel, type } of checks) {
    try {
      const btn = await page.$(sel);
      if (!btn) continue;
      const visible = await btn.isVisible();
      const disabled = await btn.isDisabled();
      if (visible && !disabled) return { type, btn };
    } catch {
      // ignore
    }
  }

  return null;
};

const isModalOpen = async (page) => {
  try {
    const el = await page.$(MODAL_SEL);
    if (el && (await el.isVisible())) return true;
  } catch {
    // fall through to apply-page detection
  }

  if (!isApplyFlowUrl(page.url())) return false;

  return page.evaluate(() => {
    return !!(
      document.querySelector('[data-live-test-job-apply-page]') ||
      document.querySelector('form[action*="/apply"]') ||
      document.querySelector('input, select, textarea')
    );
  }).catch(() => false);
};

const isSuccess = async (page) => {
  return page.evaluate(() => {
    const content = document.body.innerText.toLowerCase();
    return (
      content.includes('application was sent') ||
      content.includes('your application was submitted') ||
      content.includes('applied to') ||
      !!document.querySelector('.artdeco-inline-feedback--success') ||
      !!document.querySelector('[data-test-applied-status]') ||
      !!document.querySelector('.jobs-easy-apply-content__confirmation')
    );
  });
};

const findVisibleButton = async (page, selectors) => {
  for (const sel of selectors) {
    const buttons = await page.$$(sel).catch(() => []);
    for (const btn of buttons) {
      if (await btn.isVisible().catch(() => false)) return btn;
    }
  }
  return null;
};

const clickButtonByText = async (page, patterns) => {
  return page.evaluate((rawPatterns) => {
    const regexes = rawPatterns.map((raw) => new RegExp(raw, 'i'));
    const buttons = [...document.querySelectorAll('button')];

    for (const btn of buttons) {
      const style = window.getComputedStyle(btn);
      if (style.display === 'none' || style.visibility === 'hidden') continue;

      const text = `${btn.textContent || ''} ${btn.getAttribute('aria-label') || ''}`.trim();
      if (regexes.some((re) => re.test(text))) {
        btn.click();
        return text;
      }
    }

    return '';
  }, patterns.map((pattern) => pattern.source));
};

const closeModal = async (page, options = {}) => {
  const { preserveApplication = false, logger = null } = options;

  try {
    const dismissSels = [
      'button[aria-label="Dismiss"]',
      '.artdeco-modal__dismiss',
      'button[data-test-modal-close-btn]',
    ];

    for (const sel of dismissSels) {
      const btn = await page.$(sel);
      if (btn && (await btn.isVisible())) {
        await btn.click();
        await sleep(1000);
        break;
      }
    }

    await sleep(800);

    if (!(await isModalOpen(page))) {
      if (preserveApplication && logger) logger.info('Closed pending application without a discard prompt');
      return { preserved: preserveApplication, closed: true };
    }

    if (preserveApplication) {
      const saveSelectors = [
        'button[data-control-name*="save_application"]',
        'button[aria-label*="Save application"]',
        'button[aria-label*="Save"]',
        '.artdeco-modal button.artdeco-button--primary',
      ];

      const saveBtn = await findVisibleButton(page, saveSelectors);
      if (saveBtn) {
        const label = ((await saveBtn.textContent()) || (await saveBtn.getAttribute('aria-label')) || '').trim();
        if (/save|keep/i.test(label) || /save_application/i.test(label)) {
          await saveBtn.click();
          await sleep(1200);
          if (logger) logger.info(`Saved pending application before closing modal: "${label}"`);
          return { preserved: true, closed: true };
        }
      }

      const savedByText = await clickButtonByText(page, [/save application/i, /^save$/i, /keep application/i]);
      if (savedByText) {
        await sleep(1200);
        if (logger) logger.info(`Saved pending application before closing modal: "${savedByText}"`);
        return { preserved: true, closed: true };
      }

      if (logger) logger.warn('Could not confirm a "Save application" action, falling back to discard so the bot can continue');
    }

    const discard = await findVisibleButton(page, [
      'button[data-control-name="discard_application_confirm_btn"]',
      'button[aria-label*="Discard"]',
      'button[aria-label*="Don\\\'t save"]',
    ]);
    if (discard) {
      await discard.click();
      await sleep(800);
    }
    return { preserved: false, closed: true };
  } catch {
    return { preserved: false, closed: false };
  }
};

const moveApplicationToPendingQuestions = async (appId, pendingQuestions, validationMessages, logger) => {
  if (!appId || !pendingQuestions.length) return null;

  const reasonParts = [`Awaiting answers for ${pendingQuestions.length} required question(s)`];
  if (validationMessages?.length) reasonParts.push(validationMessages.join(' | '));

  const reason = reasonParts.join(' - ');
  await applications.updateStatus(appId, 'pending_questions', reason);
  logger.warn(`Stored application under pending questions: ${pendingQuestions.map((q) => q.question_text).join(' | ')}`);
  return reason;
};

const applyToJob = async (page, job, account, qaTemplates, logger) => {
  logger.info(`Applying: "${job.title}" @ "${job.company}"`);

  let appId = job.application_id || null;

  if (appId) {
    await applications.updateStatus(appId, 'pending', null);
  } else {
    const appResult = await applications.create({
      account_id: account.id,
      search_config_id: job.config_id,
      job_url: job.url,
      job_title: job.title,
      company_name: job.company,
      location: job.location,
      is_easy_apply: true,
      status: 'pending',
    });
    appId = appResult.rows[0]?.id || null;
  }

  try {
    const hasInlineContext = await hasInlineEasyApplyContext(page);

    if (!hasInlineContext && !page.url().includes(job.url.replace('https://www.linkedin.com', ''))) {
      await page.goto(job.url, { waitUntil: 'domcontentloaded', timeout: 20000 });
      await sleep(randomInt(1500, 2500));
    }

    const targetCheck = await confirmExpectedJobContext(page, job);
    if (!targetCheck.ok) {
      const reason = buildJobMismatchMessage(job, targetCheck.actual);
      logger.warn(reason);
      if (appId) await applications.updateStatus(appId, 'manual_review', reason);
      return 'skipped';
    }

    const opened = await openEasyApplyModal(page, logger);
    if (!opened) {
      if (appId) await applications.updateStatus(appId, 'manual_review', 'Easy Apply modal did not open');
      return 'skipped';
    }

    let step = 0;
    const maxSteps = 20;

    while (step < maxSteps) {
      step++;
      logger.info(`Step ${step}`);

      await handleResumeUpload(page, account.resume_path, logger);
      const stepResult = await fillCurrentStep(page, qaTemplates, job, step, logger);
      await persistStepQuestions(appId, account, job, step, stepResult.questions);

      await humanScroll(page, 'down', 300);
      await sleep(randomInt(400, 800));

      if (await isSuccess(page)) {
        logger.info(`Applied: "${job.title}"`);
        if (appId) await applications.updateStatus(appId, 'applied');
        await closeModal(page);
        return 'applied';
      }

      const action = await getFooterAction(page);
      if (!action) {
        const validationMessages = await getValidationMessages(page);
        logger.warn('No footer action button found');

        if (await isSuccess(page)) {
          if (appId) await applications.updateStatus(appId, 'applied');
          return 'applied';
        }

        const pendingReason = await moveApplicationToPendingQuestions(
          appId,
          stepResult.pendingQuestions,
          validationMessages,
          logger
        );
        if (pendingReason) {
          const closeResult = await closeModal(page, { preserveApplication: true, logger });
          if (!closeResult.preserved && appId) {
            await applications.updateStatus(
              appId,
              'pending_questions',
              `${pendingReason} - LinkedIn draft could not be saved automatically`
            );
          }
          return 'pending_questions';
        }

        break;
      }

      logger.info(`Action button: ${action.type}`);
      const beforeSignature = stepResult.signature;

      if (action.type === 'submit') {
        await action.btn.click();
        await sleep(randomInt(2000, 3500));

        if (await isSuccess(page)) {
          logger.info(`Applied (post-submit): "${job.title}"`);
          if (appId) await applications.updateStatus(appId, 'applied');
          await closeModal(page);
          return 'applied';
        }

        if (await isModalOpen(page)) {
          const afterSubmitQuestions = dedupeCapturedQuestions((await extractFields(page)).map((question) => ({
            ...question,
            step_index: step,
          })));

          const validationMessages = await getValidationMessages(page);
          const pendingQuestions = getPendingQuestions(afterSubmitQuestions);
          const blockingQuestions = getBlockingQuestions(afterSubmitQuestions);
          if (buildQuestionSignature(afterSubmitQuestions) === beforeSignature) {
            logger.warn(`Application did not advance after ${action.type}`);
            await persistStepQuestions(appId, account, job, step, afterSubmitQuestions);
            const pendingReason = await moveApplicationToPendingQuestions(
              appId,
              blockingQuestions,
              validationMessages,
              logger
            );
            if (pendingReason) {
              const closeResult = await closeModal(page, { preserveApplication: true, logger });
              if (!closeResult.preserved && appId) {
                await applications.updateStatus(
                  appId,
                  'pending_questions',
                  `${pendingReason} - LinkedIn draft could not be saved automatically`
                );
              }
              return 'pending_questions';
            }
            break;
          }
        }

        logger.info('Submit clicked but success not confirmed yet - continuing');
        continue;
      }

      await action.btn.click();
      await sleep(randomInt(1000, 1800));

      if (!(await isModalOpen(page))) {
        if (await isSuccess(page)) {
          logger.info(`Applied (modal closed): "${job.title}"`);
          if (appId) await applications.updateStatus(appId, 'applied');
          return 'applied';
        }
        break;
      }

      const postActionQuestions = dedupeCapturedQuestions((await extractFields(page)).map((question) => ({
        ...question,
        step_index: step,
      })));

      const validationMessages = await getValidationMessages(page);
      const pendingQuestions = getPendingQuestions(postActionQuestions);
      const blockingQuestions = getBlockingQuestions(postActionQuestions);
      const afterSignature = buildQuestionSignature(postActionQuestions);

      if (afterSignature === beforeSignature) {
        logger.warn(`Application did not advance after ${action.type}`);
        await persistStepQuestions(appId, account, job, step, postActionQuestions);
        const pendingReason = await moveApplicationToPendingQuestions(
          appId,
          blockingQuestions,
          validationMessages,
          logger
        );
        if (pendingReason) {
          const closeResult = await closeModal(page, { preserveApplication: true, logger });
          if (!closeResult.preserved && appId) {
            await applications.updateStatus(
              appId,
              'pending_questions',
              `${pendingReason} - LinkedIn draft could not be saved automatically`
            );
          }
          return 'pending_questions';
        }
        break;
      }
    }

    const finalQuestions = (await extractFields(page).catch(() => [])).map((question) => ({
      ...question,
      step_index: step || 1,
    }));

    if (finalQuestions.length) {
      await persistStepQuestions(appId, account, job, step || 1, finalQuestions);
      const pendingQuestions = getPendingQuestions(finalQuestions);
      const validationMessages = await getValidationMessages(page);

      const pendingReason = await moveApplicationToPendingQuestions(
        appId,
        pendingQuestions,
        validationMessages,
        logger
      );
      if (pendingReason) {
        const closeResult = await closeModal(page, { preserveApplication: true, logger });
        if (!closeResult.preserved && appId) {
          await applications.updateStatus(
            appId,
            'pending_questions',
            `${pendingReason} - LinkedIn draft could not be saved automatically`
          );
        }
        return 'pending_questions';
      }
    }

    logger.warn(`Could not confirm submission for: "${job.title}"`);
    if (appId) await applications.updateStatus(appId, 'failed', 'Step loop exhausted without submission');
    await closeModal(page);
    return 'failed';
  } catch (err) {
    logger.error(`applyToJob error: ${err.message}`);
    if (appId) await applications.updateStatus(appId, 'failed', err.message);
    await closeModal(page).catch(() => {});
    return 'failed';
  }
};

module.exports = { applyToJob, fillCurrentStep, handleResumeUpload };
