// backend/src/bot/linkedin-search.js
// Strategy: stay on the search page, click job cards in the left list,
// and read/apply from the right detail pane.

const { humanDelay, sleep, randomInt } = require('../utils/humanize');
const { applications } = require('../db');

const BASE_URL = 'https://www.linkedin.com/jobs/search/';

const buildSearchUrl = (config, start = 0) => {
  const params = new URLSearchParams();
  params.set('keywords', config.job_title);
  if (config.location) params.set('location', config.location);
  if (config.easy_apply_only) params.set('f_LF', 'f_AL');
  if (config.remote_only) params.set('f_WT', '2');
  if (start > 0) params.set('start', String(start));

  const dateMap = { past_24h: 'r86400', past_week: 'r604800', past_month: 'r2592000' };
  if (config.date_posted && dateMap[config.date_posted]) {
    params.set('f_TPR', dateMap[config.date_posted]);
  }

  const expMap = { entry: '2', associate: '3', mid: '4', senior: '5', director: '6' };
  if (config.experience_level?.length) {
    params.set('f_E', config.experience_level.map((e) => expMap[e]).filter(Boolean).join(','));
  }

  const typeMap = { full_time: 'F', part_time: 'P', contract: 'C', internship: 'I' };
  if (config.job_type?.length) {
    params.set('f_JT', config.job_type.map((t) => typeMap[t]).filter(Boolean).join(','));
  }

  params.set('sortBy', 'DD');
  return `${BASE_URL}?${params.toString()}`;
};

const waitForPageLoad = async (page, logger) => {
  await page.waitForLoadState('domcontentloaded', { timeout: 15000 }).catch(() => {});
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  await sleep(randomInt(1200, 2200));

  const url = page.url();
  if (url.includes('/login') || url.includes('/authwall') || url.includes('/checkpoint')) {
    logger.error(`Session invalid - redirected to: ${url}`);
    return false;
  }

  await page.waitForSelector(
    '.jobs-search-results-list, .scaffold-layout__list, .jobs-search__results-list',
    { timeout: 12000 }
  ).catch(() => {});

  return true;
};

const scrollToRevealCards = async (page) => {
  await page.evaluate(() => {
    const list =
      document.querySelector('.jobs-search-results-list') ||
      document.querySelector('.scaffold-layout__list') ||
      document.querySelector('.jobs-search__results-list');

    if (!list) return;

    let pos = list.scrollTop || 0;
    for (let i = 0; i < 8; i++) {
      setTimeout(() => {
        pos += 320;
        list.scrollTop = pos;
      }, i * 180);
    }
  });

  await sleep(randomInt(1800, 2600));
};

const extractVisibleJobs = async (page) => {
  return page.evaluate(() => {
    const seen = new Set();
    const jobs = [];
    const normalizeText = (value) => String(value || '').trim().toLowerCase().replace(/\s+/g, ' ');

    const pushJob = (jobId, title = '', company = '', rawText = '') => {
      if (!jobId || seen.has(jobId)) return;
      seen.add(jobId);
      jobs.push({ jobId, title, company, rawText });
    };

    document.querySelectorAll('li').forEach((li) => {
      const dataJobId =
        li.getAttribute('data-occludable-job-id') ||
        li.getAttribute('data-job-id') ||
        li.dataset.jobId ||
        li.dataset.occludableJobId;

      const anchor = li.querySelector('a[href*="/jobs/view/"]');
      const href = anchor?.href || '';
      const hrefMatch = href.match(/\/jobs\/view\/(\d+)/);
      const jobId = dataJobId || hrefMatch?.[1];
      if (!jobId) return;

      const rawText = (li.innerText || '').trim();
      const lines = rawText
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean);

      const title =
        li.querySelector('.job-card-list__title, .job-card-list__title--link, .job-card-container__link, .job-card-container__link-text, a[aria-label]')?.textContent?.trim() ||
        anchor?.textContent?.trim() ||
        lines[0] ||
        '';

      let company =
        li.querySelector('.job-card-container__company-name, .job-card-container__primary-description, .artdeco-entity-lockup__subtitle, .artdeco-entity-lockup__subtitle span')?.textContent?.trim() ||
        '';

      if (!company) {
        company =
          lines.find((line, index) =>
            index > 0 &&
            normalizeText(line) !== normalizeText(title) &&
            !/easy apply|promoted|viewed|ago|applicants?|applicant|remote|hybrid|on-site|onsite|\$|benefit/i.test(line)
          ) || '';
      }

      pushJob(jobId, title, company, rawText);
    });

    document.querySelectorAll('a[href*="/jobs/view/"]').forEach((a) => {
      const match = a.href.match(/\/jobs\/view\/(\d+)/);
      if (!match) return;
      pushJob(match[1], a.textContent?.trim() || '', '', a.textContent?.trim() || '');
    });

    return jobs;
  });
};

const clickJobCard = async (page, jobId, logger) => {
  const clicked = await page.evaluate((targetJobId) => {
    const candidates = [
      ...document.querySelectorAll(
        [
          `li[data-occludable-job-id="${targetJobId}"]`,
          `li[data-job-id="${targetJobId}"]`,
          `a[href*="/jobs/view/${targetJobId}"]`,
          `[data-job-id="${targetJobId}"]`,
          `[data-occludable-job-id="${targetJobId}"]`,
        ].join(', ')
      ),
    ];

    for (const candidate of candidates) {
      const clickable =
        candidate.tagName === 'A'
          ? candidate
          : candidate.querySelector('a[href*="/jobs/view/"]') ||
            candidate.querySelector('.job-card-list__title') ||
            candidate.querySelector('.job-card-container__link') ||
            candidate;

      if (clickable && clickable.offsetParent !== null) {
        clickable.click();
        return true;
      }
    }

    return false;
  }, jobId);

  if (!clicked) {
    logger.warn(`Could not click job card ${jobId}`);
    return false;
  }

  await sleep(randomInt(1200, 2200));
  await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
  await sleep(randomInt(600, 1200));
  return true;
};

const loadJobDetailFromPane = async (page, jobId) => {
  return page.evaluate((currentJobId) => {
    const text = (selectors) => {
      for (const selector of selectors) {
        const el = document.querySelector(selector);
        const value = el?.textContent?.trim();
        if (value) return value;
      }
      return '';
    };

    const title = text([
      'h1.job-details-jobs-unified-top-card__job-title',
      '.jobs-unified-top-card__job-title h1',
      'h1.t-24',
      '.job-view-layout h1',
      'h1',
    ]);

    const company = text([
      '.job-details-jobs-unified-top-card__company-name a',
      '.job-details-jobs-unified-top-card__company-name',
      '.jobs-unified-top-card__company-name a',
      '.jobs-unified-top-card__company-name',
    ]);

    const location = text([
      '.job-details-jobs-unified-top-card__primary-description-container .tvm__text',
      '.job-details-jobs-unified-top-card__bullet',
      '.jobs-unified-top-card__bullet',
      '.jobs-unified-top-card__primary-description-container .tvm__text',
    ]);

    const easyApplyButton =
      document.querySelector('button.jobs-apply-button[aria-label*="Easy Apply"]') ||
      document.querySelector('button[aria-label*="Easy Apply"]') ||
      [...document.querySelectorAll('button.jobs-apply-button, .jobs-apply-button')]
        .find((btn) => btn.textContent?.toLowerCase().includes('easy apply')) ||
      null;

    const alreadyApplied =
      !!document.querySelector('.artdeco-inline-feedback--success') ||
      !!document.querySelector('[data-test-applied-status]') ||
      document.body.innerText.includes('Application submitted') ||
      document.body.innerText.includes('Application was sent');

    return {
      title,
      company,
      location,
      url: `https://www.linkedin.com/jobs/view/${currentJobId}/`,
      isEasyApply: !!easyApplyButton && !easyApplyButton.disabled,
      alreadyApplied,
    };
  }, jobId);
};

const hasNextPage = async (page) => {
  return page.evaluate(() => {
    const btn = document.querySelector('button[aria-label="View next page"]');
    if (btn && !btn.disabled && btn.offsetParent !== null) return true;

    const active = document.querySelector('.artdeco-pagination__indicator--number.active');
    if (active && active.nextElementSibling) return true;

    return false;
  });
};

const clickNextPage = async (page, logger) => {
  const clicked = await page.evaluate(() => {
    const btn = document.querySelector('button[aria-label="View next page"]');
    if (btn && !btn.disabled) {
      btn.click();
      return true;
    }

    const active = document.querySelector('.artdeco-pagination__indicator--number.active');
    if (active) {
      const nextLi = active.nextElementSibling;
      const nextBtn = nextLi?.querySelector('button');
      if (nextBtn) {
        nextBtn.click();
        return true;
      }
    }

    return false;
  });

  if (!clicked) return false;

  await sleep(randomInt(2000, 3500));
  await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
  await sleep(randomInt(800, 1400));
  logger.info('Moved to next page');
  return true;
};

const searchJobs = async (page, config, accountId, logger, onEasyApplyJob = null) => {
  const searchUrl = buildSearchUrl(config);
  logger.info(`Search: "${config.job_title}" in "${config.location || 'anywhere'}"`);
  logger.info(`URL: ${searchUrl}`);

  await page.goto(searchUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  const ok = await waitForPageLoad(page, logger);
  if (!ok) return [];

  const easyApplyJobs = [];
  const seenJobIds = new Set();
  let pageNum = 1;
  let totalSeen = 0;
  let consecutiveEmpty = 0;
  const maxJobs = config.max_applications || 50;

  while (totalSeen < maxJobs) {
    logger.info(`Page ${pageNum} - scrolling to load cards...`);
    await scrollToRevealCards(page);

    const visibleJobs = await extractVisibleJobs(page);
    const newJobs = visibleJobs.filter((job) => !seenJobIds.has(job.jobId));

    logger.info(`Page ${pageNum}: ${visibleJobs.length} visible jobs, ${newJobs.length} new`);

    if (newJobs.length === 0) {
      consecutiveEmpty++;
      logger.warn(`Empty page ${consecutiveEmpty}/3`);
      if (consecutiveEmpty >= 3) {
        logger.warn('3 consecutive empty pages - stopping search');
        break;
      }
    } else {
      consecutiveEmpty = 0;
    }

    for (const job of newJobs) {
      if (totalSeen >= maxJobs) break;
      seenJobIds.add(job.jobId);

      const jobUrl = `https://www.linkedin.com/jobs/view/${job.jobId}/`;
      const existing = await applications.findByUrl(accountId, jobUrl);
      const existingRow = existing.rows[0];

      if (existingRow?.status === 'applied') {
        logger.info(`Already applied in DB: ${job.jobId}`);
        continue;
      }

      if (existingRow) {
        logger.info(`In DB with status "${existingRow.status}" - rechecking ${job.jobId}`);
      }

      logger.info(`Opening card ${job.jobId} (${totalSeen + 1}/${maxJobs})`);
      const clicked = await clickJobCard(page, job.jobId, logger);
      if (!clicked) continue;

      const detail = await loadJobDetailFromPane(page, job.jobId);
      totalSeen++;

      logger.info(`"${detail.title}" @ "${detail.company}" | EasyApply=${detail.isEasyApply} | Applied=${detail.alreadyApplied}`);

      if (!detail.title) continue;

      if (detail.alreadyApplied) {
        const existingOrCreated = await applications.create({
          account_id: accountId,
          search_config_id: config.id,
          job_url: detail.url,
          job_title: detail.title,
          company_name: detail.company,
          location: detail.location,
          is_easy_apply: !!detail.isEasyApply,
          status: 'applied',
        }).catch(() => null);

        const appId = existingOrCreated?.rows?.[0]?.id;
        if (appId) {
          await applications.updateStatus(appId, 'applied', 'Already applied on LinkedIn').catch(() => {});
        }

        continue;
      }

      if (detail.isEasyApply) {
        const easyApplyJob = {
          jobId: job.jobId,
          url: detail.url,
          title: detail.title,
          company: detail.company,
          location: detail.location,
          config_id: config.id,
          job_role: config.job_title,
        };

        easyApplyJobs.push(easyApplyJob);
        logger.info(`Queued Easy Apply: ${detail.title}`);

        if (onEasyApplyJob) {
          const result = await onEasyApplyJob(easyApplyJob);
          logger.info(`Immediate apply result for "${detail.title}": ${result}`);

          await page.waitForSelector(
            '.jobs-search-results-list, .scaffold-layout__list, .jobs-search__results-list',
            { timeout: 5000 }
          ).catch(() => {});
        }
      } else {
        await applications.create({
          account_id: accountId,
          search_config_id: config.id,
          job_url: detail.url,
          job_title: detail.title,
          company_name: detail.company,
          location: detail.location,
          is_easy_apply: false,
          status: 'manual_review',
        }).catch(() => {});
        logger.info(`Manual review: ${detail.title}`);
      }

      await sleep(randomInt(500, 1000));
    }

    if (totalSeen >= maxJobs) break;

    const canNext = await hasNextPage(page);
    if (!canNext) {
      logger.info('No more pages available');
      break;
    }

    const moved = await clickNextPage(page, logger);
    if (!moved) {
      logger.warn('Could not advance to next page - stopping');
      break;
    }

    pageNum++;
    await humanDelay('normal');
  }

  logger.info(`Search complete - Easy Apply queued: ${easyApplyJobs.length}, total seen: ${totalSeen}`);
  return easyApplyJobs;
};

module.exports = { searchJobs, buildSearchUrl };
