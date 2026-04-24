from __future__ import annotations

import os
import re
from typing import Any

from ..db import application_questions, applications
from ..services.application_answerer import (
    find_template_answer as resolve_template_answer,
    resolve_application_answer,
)
from ..utils.humanize import human_scroll, human_type, random_int, sleep
from .linkedin_job_utils import compare_job_identity


MODAL_SEL = ", ".join(
    [
        ".jobs-easy-apply-modal",
        '[data-test-modal-id="easy-apply-modal"]',
        '.artdeco-modal[role="dialog"]',
        ".jobs-easy-apply-content",
    ]
)
APPLICATION_SURFACE_SEL = ", ".join(
    [
        MODAL_SEL,
        "[data-live-test-job-apply-page]",
        'form[action*="/apply"]',
    ]
)
APPLICATION_ENTRY_TEXT_RE = re.compile(r"easy apply|continue", re.I)
EASY_APPLY_BTN_SEL = ", ".join(
    [
        'button.jobs-apply-button[aria-label*="Easy Apply"]',
        'button[aria-label*="Easy Apply"]',
        # Re-entry button: aria-label="Continue applying to <Job> at <Company>"
        'button[data-live-test-job-apply-button]',
    ]
)
# Matches the exact visible text "Continue" found in the nested <span> of
# LinkedIn SDUI apply-flow anchor links: <a href="..."><span>Continue</span></a>
_SDUI_CONTINUE_TEXT_RE = re.compile(r"^\s*continue\s*$", re.I)


def normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def role_matches(job_role: str | None, scope: str | None) -> bool:
    left = normalize(job_role)
    right = normalize(scope)
    if not right:
        return True
    if not left:
        return False
    return left == right or right in left or left in right


def build_question_signature(questions: list[dict[str, Any]]) -> str:
    keys = [question.get("question_key") or f'{question.get("question_text")}|{question.get("field_type")}' for question in questions]
    return "||".join(sorted(keys))


def read_application_checkpoint(page: Any) -> dict[str, str]:
    try:
        return page.evaluate(
            '''
            (surfaceSel) => {
              const collapse = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
              const isVisible = (el) => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
              };

              const surface =
                [...document.querySelectorAll(surfaceSel)].find(isVisible) ||
                document.querySelector('[data-live-test-job-apply-page]') ||
                document.querySelector('form[action*="/apply"]') ||
                document.body;

              const readHeading = () => {
                const selectors = [
                  '.artdeco-modal__header h2',
                  '.jobs-easy-apply-content__title',
                  '[data-live-test-job-apply-page] h1',
                  '[data-live-test-job-apply-page] h2',
                  '[data-live-test-job-apply-page] h3',
                  'form[action*="/apply"] h1',
                  'form[action*="/apply"] h2',
                  'form[action*="/apply"] h3',
                  'h1',
                  'h2',
                  'h3',
                ];

                for (const selector of selectors) {
                  const node = surface.querySelector(selector) || document.querySelector(selector);
                  if (node && isVisible(node)) {
                    const text = collapse(node.textContent);
                    if (text) return text;
                  }
                }
                return '';
              };

              const readPrimaryAction = () => {
                const selectors = [
                  'button[aria-label*="Submit application"]',
                  'footer button[aria-label*="Submit"]',
                  'button[aria-label*="Review your application"]',
                  'button[aria-label*="Continue to next step"]',
                  'button[aria-label*="Next"]',
                  '.jobs-easy-apply-modal footer .artdeco-button--primary',
                  '.artdeco-modal__actionbar .artdeco-button--primary',
                  '[data-live-test-job-apply-page] .artdeco-button--primary',
                  'form[action*="/apply"] .artdeco-button--primary',
                ];

                for (const selector of selectors) {
                  const node = [...document.querySelectorAll(selector)].find(isVisible);
                  if (node) {
                    const text = collapse(`${node.textContent || ''} ${node.getAttribute('aria-label') || ''}`);
                    if (text) return text;
                  }
                }
                return '';
              };

              const surfaceText = collapse(surface.innerText || '').slice(0, 320);
              const stepMatch = surfaceText.match(/step\\s+\\d+\\s+of\\s+\\d+/i);

              return {
                url: window.location.href,
                heading: readHeading(),
                actionText: readPrimaryAction(),
                stepText: stepMatch ? collapse(stepMatch[0]) : '',
                fingerprint: surfaceText,
              };
            }
            ''',
            APPLICATION_SURFACE_SEL,
        ) or {}
    except Exception:
        return {}


def application_checkpoint_changed(before: dict[str, Any] | None, after: dict[str, Any] | None) -> bool:
    before = before or {}
    after = after or {}
    keys = ("url", "heading", "actionText", "stepText", "fingerprint")

    for key in keys:
        left = collapse_whitespace(before.get(key)).lower()
        right = collapse_whitespace(after.get(key)).lower()
        if left or right:
            if left != right:
                return True

    return False


def read_field_diagnostics(page: Any) -> dict[str, Any]:
    try:
        return (
            page.evaluate(
                '''
                (surfaceSel) => {
                  const collapse = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                  const isVisible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                  };

                  const surface =
                    [...document.querySelectorAll(surfaceSel)].find(isVisible) ||
                    document.querySelector('[data-live-test-job-apply-page]') ||
                    document.querySelector('form[action*="/apply"]') ||
                    document.body;

                  const nativeFields = [...surface.querySelectorAll('input, select, textarea')]
                    .filter((el) => el.type !== 'hidden' && !el.disabled && isVisible(el));

                  const comboboxes = [...surface.querySelectorAll('[role="combobox"], button[aria-haspopup="listbox"]')]
                    .filter((el) => !el.matches('input, select, textarea') && !el.disabled && isVisible(el));

                  const buttons = [...surface.querySelectorAll('button, a[href]')]
                    .filter(isVisible)
                    .map((el) => collapse(`${el.textContent || ''} ${el.getAttribute('aria-label') || ''}`))
                    .filter(Boolean)
                    .slice(0, 8);

                  return {
                    url: window.location.href,
                    nativeFieldCount: nativeFields.length,
                    comboboxCount: comboboxes.length,
                    textSnippet: collapse(surface.innerText || '').slice(0, 220),
                    buttons,
                  };
                }
                ''',
                APPLICATION_SURFACE_SEL,
            )
            or {}
        )
    except Exception:
        return {}


def get_pending_questions(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [question for question in questions if question.get("is_required") and not question.get("is_answered")]


def get_blocking_questions(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    required_unanswered = [question for question in questions if question.get("is_required") and not question.get("is_answered")]
    if required_unanswered:
        return required_unanswered

    unanswered = [question for question in questions if not question.get("is_answered")]
    if unanswered:
        return unanswered

    return questions


def collapse_whitespace(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


_PLACEHOLDER_SELECTION_VALUES = {
    "select",
    "select an option",
    "select one",
    "please select",
    "please make a selection",
    "make a selection",
    "choose an option",
    "choose one",
}


def normalize_field_value(value: Any) -> str:
    collapsed = collapse_whitespace(value).lower()
    return re.sub(r"^[\s\-:]+|[\s\-:]+$", "", collapsed)


def is_placeholder_selection_value(field_type: Any, question_text: Any, value: Any) -> bool:
    if normalize(field_type) not in {"select", "combobox"}:
        return False

    normalized_value = normalize_field_value(value)
    if not normalized_value:
        return False

    normalized_question = normalize_field_value(question_text)
    return normalized_value in _PLACEHOLDER_SELECTION_VALUES or (
        bool(normalized_question) and normalized_value == normalized_question
    )


def sanitize_captured_question(question: dict[str, Any] | None) -> dict[str, Any]:
    sanitized = dict(question or {})
    current_value = collapse_whitespace(sanitized.get("currentValue"))
    sanitized["currentValue"] = current_value

    if is_placeholder_selection_value(
        sanitized.get("field_type"),
        sanitized.get("question_text"),
        current_value,
    ):
        sanitized["currentValue"] = ""
        sanitized["is_answered"] = False

    return sanitized


def read_button_label(button: Any) -> str:
    try:
        text = button.text_content() or ""
    except Exception:
        text = ""

    try:
        aria = button.get_attribute("aria-label") or ""
    except Exception:
        aria = ""

    return collapse_whitespace(f"{text} {aria}")


def classify_action_label(value: Any) -> str:
    label = collapse_whitespace(value).lower()
    if "submit" in label:
        return "submit"
    if "review" in label:
        return "review"
    if "continue to next step" in label or label == "next" or label.startswith("next "):
        return "next"
    if label == "continue" or label.startswith("continue "):
        return "next"
    return "primary"


def is_button_interactable(button: Any) -> bool:
    if not button:
        return False

    try:
        if not button.is_visible():
            return False
    except Exception:
        return False

    try:
        if button.is_disabled():
            return False
    except Exception:
        pass

    try:
        if collapse_whitespace(button.get_attribute("aria-disabled")).lower() == "true":
            return False
    except Exception:
        pass

    return True


def truncate_for_log(value: Any, max_len: int = 120) -> str:
    text = collapse_whitespace(value)
    if len(text) <= max_len:
        return text
    return f"{text[: max_len - 3]}..."


def looks_like_repeated_leading_text(value: Any) -> bool:
    text = collapse_whitespace(value)
    if len(text) < 8:
        return False

    max_len = len(text) // 2
    for length in range(max_len, 3, -1):
        first = text[:length].strip()
        second = text[length : length * 2].strip()
        if first and len(first) >= 4 and first == second:
            return True
    return False


def build_label_debug_message(field: dict[str, Any], step_index: int, field_index: int) -> str:
    debug = field.get("label_debug") or {}
    return " | ".join(
        [
            f"Label debug [step {step_index} field {field_index + 1}]",
            f'type={field.get("field_type") or "unknown"}',
            f'chosen="{truncate_for_log(field.get("question_text"))}"',
            f'explicit="{truncate_for_log(debug.get("explicitLabel"))}"',
            f'container="{truncate_for_log(debug.get("containerLabel"))}"',
            f'groupHeading="{truncate_for_log(debug.get("groupHeading"))}"',
            f'groupLabel="{truncate_for_log(debug.get("groupLabel"))}"',
            f'aria="{truncate_for_log(debug.get("ariaLabel"))}"',
            f'placeholder="{truncate_for_log(debug.get("placeholder"))}"',
            f'name="{truncate_for_log(debug.get("name"))}"',
        ]
    )


def build_label_debug_html_message(field: dict[str, Any], step_index: int, field_index: int) -> str:
    debug = field.get("label_debug") or {}
    return " | ".join(
        [
            f"Label debug HTML [step {step_index} field {field_index + 1}]",
            f'chosen="{truncate_for_log(field.get("question_text"))}"',
            f'explicitHtml="{truncate_for_log(debug.get("explicitHtml"), 280)}"',
            f'explicitInnerHtml="{truncate_for_log(debug.get("explicitInnerHtml"), 280)}"',
        ]
    )


def log_field_label_debug(fields: list[dict[str, Any]], step_index: int, logger: Any) -> None:
    if not logger or not fields:
        return

    for index, field in enumerate(fields):
        debug_values = list((field.get("label_debug") or {}).values())
        suspicious = looks_like_repeated_leading_text(field.get("question_text")) or any(
            looks_like_repeated_leading_text(value) for value in debug_values
        )

        message = build_label_debug_message(field, step_index, index)
        if suspicious:
            logger.info(message)
            if (field.get("label_debug") or {}).get("explicitHtml"):
                logger.info(build_label_debug_html_message(field, step_index, index))
        else:
            logger.debug(message)


def is_apply_flow_url(value: Any) -> bool:
    text = str(value or "")
    return bool(re.search(r"/jobs/view/\d+/apply/?", text, re.I) or re.search(r"openSDUIApplyFlow=true", text, re.I))


def is_application_entry_label(value: Any) -> bool:
    return bool(APPLICATION_ENTRY_TEXT_RE.search(collapse_whitespace(value)))


def find_visible_easy_apply_button(page: Any) -> Any | None:
    try:
        locator = page.get_by_role("button", name=APPLICATION_ENTRY_TEXT_RE)
        count = locator.count()
        for index in range(count):
            button = locator.nth(index)
            if button.is_visible():
                return button
    except Exception:
        pass

    try:
        buttons = page.query_selector_all(EASY_APPLY_BTN_SEL)
    except Exception:
        buttons = []

    for button in buttons:
        try:
            if not button.is_visible():
                continue
            label = f'{button.text_content() or ""} {button.get_attribute("aria-label") or ""}'
            if is_application_entry_label(label):
                return button
        except Exception:
            continue

    # Fallback: find <a> elements whose visible text is exactly "Continue".
    # LinkedIn SDUI apply-flow links render as <a href="..."><span>Continue</span></a>
    # with no aria-label, so we detect them by their nested span text content.
    try:
        anchors = page.query_selector_all("a[href]")
    except Exception:
        anchors = []

    for anchor in anchors:
        try:
            if not anchor.is_visible():
                continue
            text = collapse_whitespace(anchor.text_content() or "")
            if _SDUI_CONTINUE_TEXT_RE.match(text):
                return anchor
        except Exception:
            continue

    return None


def wait_for_application_surface(page: Any) -> bool:
    try:
        page.wait_for_selector(APPLICATION_SURFACE_SEL, timeout=8000)
    except Exception:
        return False

    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass
    sleep(random_int(800, 1400))
    return True


def open_easy_apply_modal(page: Any, logger: Any) -> bool:
    if is_modal_open(page):
        logger.info("Application form is already open")
        return True

    button = None

    for _ in range(3):
        button = find_visible_easy_apply_button(page)
        if button:
            break
        try:
            page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass
        sleep(random_int(800, 1400))

    if not button:
        logger.warn("Easy Apply button was not found on detail pane")
        return False

    try:
        label = button.get_attribute("aria-label") or button.text_content() or ""
    except Exception:
        label = ""
    logger.info(f'Clicking button: "{label.strip()}"')

    try:
        button.scroll_into_view_if_needed()
    except Exception:
        pass
    sleep(random_int(500, 900))

    try:
        button.click(timeout=5000)
    except Exception:
        page.evaluate(
            """
            () => {
              // Search both buttons and anchor links for Easy Apply / Continue entry points.
              const controls = [...document.querySelectorAll('button, a[href]')];
              const entry = controls.find((el) => {
                const text = (el.textContent || '').trim();
                const aria = (el.getAttribute('aria-label') || '').trim();
                const href = (el.getAttribute('href') || '');
                return (
                  /easy apply/i.test(text) ||
                  /easy apply/i.test(aria) ||
                  /continue applying/i.test(aria) ||
                  /openSDUIApplyFlow=true/i.test(href)
                );
              });
              if (entry) entry.click();
            }
            """,
        )

    if wait_for_application_surface(page) or is_modal_open(page):
        logger.info("Application form opened")
        return True

    logger.warn("Application form did not open")
    return False


def has_inline_easy_apply_context(page: Any) -> bool:
    return bool(find_visible_easy_apply_button(page))


def read_current_job_context(page: Any) -> dict[str, Any]:
    return page.evaluate(
        '''
        () => {
          const extractJobId = (value) => {
            const text = String(value || '').trim();
            if (!text) return '';

            const pathMatch = text.match(/\\/jobs\\/view\\/(\\d+)/i);
            if (pathMatch?.[1]) return pathMatch[1];

            const queryMatch = text.match(/[?&#](?:currentJobId|jobId)=(\\d+)/i);
            if (queryMatch?.[1]) return queryMatch[1];

            const rawIdMatch = text.match(/^\\d+$/);
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
        }
        '''
    )


def format_job_label(job: dict[str, Any]) -> str:
    title = str(job.get("title") or "").strip() or "Unknown title"
    company = str(job.get("company") or "").strip() or "Unknown company"
    return f'"{title}" @ "{company}"'


def build_job_mismatch_message(expected: dict[str, Any], actual: dict[str, Any] | None) -> str:
    actual = actual or {}
    seen_label = f" Saw {format_job_label(actual)}." if actual.get("title") or actual.get("company") else ""
    actual_url = f' Opened URL: {actual.get("url")}' if actual.get("url") and actual.get("url") != expected.get("url") else ""
    return f"LinkedIn opened a different job than expected for {format_job_label(expected)}.{seen_label}{actual_url}".strip()


def confirm_expected_job_context(page: Any, job: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "jobId": job.get("jobId"),
        "url": job.get("url"),
        "title": job.get("title"),
        "company": job.get("company"),
    }

    actual: dict[str, Any] = {}
    comparison = compare_job_identity(expected, actual)

    for attempt in range(1, 5):
        try:
            actual = read_current_job_context(page)
        except Exception:
            actual = {}
        comparison = compare_job_identity(expected, actual)
        if comparison["matches"]:
            return {"ok": True, "actual": actual, "comparison": comparison}

        if attempt < 4:
            try:
                page.wait_for_load_state("networkidle", timeout=4000)
            except Exception:
                pass
            sleep(random_int(700, 1200))

    return {"ok": False, "actual": actual, "comparison": comparison}


def extract_fields(page: Any) -> list[dict[str, Any]]:
    return page.evaluate(
        '''
        (modalSel) => {
          const isApplyPage =
            /\\/jobs\\/view\\/\\d+\\/apply\\/?/i.test(window.location.href) ||
            /openSDUIApplyFlow=true/i.test(window.location.href);

          const cssEscape = (value) => {
            if (typeof CSS !== 'undefined' && CSS.escape) return CSS.escape(value);
            return String(value).replace(/["\\\\]/g, '\\\\$&');
          };

          const collapseRepeatedText = (value) => {
            const text = String(value || '').replace(/\\s+/g, ' ').trim();
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

            const nativeCount = [...root.querySelectorAll('input, select, textarea')]
              .filter(isCandidateField)
              .length;

            const comboCount = [...root.querySelectorAll('[role="combobox"], button[aria-haspopup="listbox"]')]
              .filter((el) => !el.matches('input, select, textarea'))
              .filter((el) => !el.disabled)
              .filter(isVisible)
              .length;

            return nativeCount + comboCount;
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
            // SDUI apply-flow page specific selectors
            '[data-view-name*="apply"] .artdeco-button--primary',
            '[data-view-name*="apply"] button[type="submit"]',
            '.jobs-easy-apply-modal footer .artdeco-button--primary',
            '.artdeco-modal__actionbar .artdeco-button--primary',
            '[data-live-test-job-apply-page] .artdeco-button--primary',
            'form[action*="/apply"] .artdeco-button--primary',
            // Broad primary-button fallback scoped away from nav
            'main .artdeco-button--primary',
          ];

          const actionButton = actionSelectors
            .map((selector) => findFirstVisible(selector))
            .find((button) => {
              return !!button && !button.disabled && button.getAttribute('aria-disabled') !== 'true';
            }) || null;

          const actionRoot = actionButton?.closest(
            [
              '[data-live-test-job-apply-page]',
              '[data-view-name*="apply"]',
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

          // When on a SDUI apply-flow page and no specific root was found, walk the
          // DOM to find the tightest visible ancestor that contains ALL candidate
          // inputs — this gives us the actual form rather than document.body.
          const findTightestFormRoot = () => {
            const allInputs = [...document.querySelectorAll('input, select, textarea')]
              .filter(isCandidateField);
            if (!allInputs.length) return null;

            // Start from the common ancestor of all inputs.
            let candidate = allInputs[0].parentElement;
            while (candidate && candidate !== document.body) {
              const contained = allInputs.every((inp) => candidate.contains(inp));
              if (contained && isVisible(candidate)) return candidate;
              candidate = candidate.parentElement;
            }
            return null;
          };

          const preferredRoots = [
            actionRoot,
            findFirstVisible(modalSel),
            findFirstVisible('[data-live-test-job-apply-page]'),
            findFirstVisible('[data-view-name*="apply"]'),
            findFirstVisible('form[action*="/apply"]'),
            detailRoot,
            isApplyPage ? findTightestFormRoot() : null,
            // SDUI apply page: form is in <main> with no modal wrapper
            isApplyPage ? (document.querySelector('main') || null) : null,
            isApplyPage ? document.body : null,
          ].filter(Boolean);

          const modal =
            preferredRoots.find((root) => countCandidateInputs(root) > 0) ||
            preferredRoots[0] ||
            null;

          if (!modal) return [];

          const getQuestionContainer = (el) =>
            el.closest(
              [
                'fieldset',
                '.fb-dash-form-element',
                '.jobs-easy-apply-form-section__grouping',
                '.jobs-easy-apply-form-element',
                '.jobs-easy-apply-form-section',
                '.artdeco-form-item',
                '[data-test-form-element]',
              ].join(', ')
            );

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
            const ariaLabel = el.getAttribute('aria-label')?.trim();
            if (ariaLabel) return `${tag}[aria-label="${cssEscape(ariaLabel)}"]`;
            const ariaControls = el.getAttribute('aria-controls')?.trim();
            if (ariaControls) return `${tag}[aria-controls="${cssEscape(ariaControls)}"]`;
            return null;
          };

          const buildQuestionKey = (parts) => parts.filter(Boolean).join('|').toLowerCase();

          const fields = [];
          const seenRadio = new Set();
          const seenCheckbox = new Set();
          const allInputs = [...modal.querySelectorAll('input, select, textarea')].filter(isCandidateField);
          const comboCandidates = [...modal.querySelectorAll('[role="combobox"], button[aria-haspopup="listbox"]')]
            .filter((el) => !el.matches('input, select, textarea'))
            .filter((el) => !el.disabled)
            .filter(isVisible)
            .filter((el) => !el.closest('header, nav, [role="search"], .search-global-typeahead, .jobs-search-box'));

          allInputs.forEach((el) => {
            const labelDetails = getLabelDetails(el);
            const label = labelDetails.chosen;
            const required = !!(
              el.required ||
              el.getAttribute('aria-required') === 'true' ||
              /\\*/.test(label)
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

          comboCandidates.forEach((el) => {
            const labelDetails = getLabelDetails(el);
            const label = labelDetails.chosen;
            const selector = buildSelector(el);
            const currentValue = readText(el);
            const required = !!(/\*/.test(label) || el.getAttribute('aria-required') === 'true');

            if (!label || !selector) return;

            fields.push({
              question_text: label,
              question_key: buildQuestionKey([label, 'combobox', selector]),
              selector,
              field_type: 'combobox',
              options: [],
              currentValue,
              is_required: required,
              is_answered: currentValue.length > 0 && currentValue.toLowerCase() !== label.toLowerCase(),
              label_debug: labelDetails,
            });
          });

          return fields.filter((field) => field.question_text);
        }
        ''',
        MODAL_SEL,
    )


# def find_answer(label: str, qa_templates: list[dict[str, Any]], job_role: str | None) -> dict[str, Any] | None:
#     return resolve_template_answer(label, qa_templates, job_role)


def map_field_type_for_engine(field_type: Any) -> str:
    field = normalize(field_type)
    if field in {"select", "combobox"}:
        return "select"
    if field == "radio":
        return "radio"
    if field == "checkbox":
        return "checkbox"
    return "text"


def resolve_field_answer(
    field: dict[str, Any],
    qa_templates: list[dict[str, Any]],
    job: dict[str, Any],
    answer_engine: Any | None,
    resume_profile: dict[str, Any] | None,
    logger: Any,
) -> dict[str, Any] | None:
    question_text = str(field.get("question_text") or "").strip()
    if not question_text:
        return None

    return resolve_application_answer(
        question_text=question_text,
        field_type=str(field.get("field_type") or "text"),
        options=list(field.get("options") or []),
        qa_templates=qa_templates,
        job_role=job.get("job_role"),
        answer_engine=answer_engine,
        resume_profile=resume_profile,
        logger=logger,
    )


def dedupe_captured_questions(questions: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}

    for raw_question in questions or []:
        question = sanitize_captured_question(raw_question)
        if not question or not question.get("question_text"):
            continue

        key = collapse_whitespace(
            question.get("question_key")
            or f'{question.get("question_text")}|{question.get("field_type") or "text"}|{question.get("selector") or ""}'
        ).lower()

        existing = seen.get(key)
        if not existing:
            seen[key] = dict(question)
            continue

        seen[key] = {
            **existing,
            **question,
            "options": question.get("options") or existing.get("options"),
            "currentValue": question.get("currentValue") or existing.get("currentValue") or "",
            "is_required": bool(existing.get("is_required") or question.get("is_required")),
            "is_answered": bool(existing.get("is_answered") or question.get("is_answered")),
            "label_debug": question.get("label_debug") or existing.get("label_debug"),
        }

    return list(seen.values())


def apply_template_to_field(page: Any, field: dict[str, Any], resolved_answer: dict[str, Any] | None, logger: Any) -> bool:
    if field.get("is_answered") and field.get("currentValue"):
        return True
    if not resolved_answer or not field.get("selector"):
        return False

    answer_value = str(resolved_answer.get("answer") or "").strip()
    if not answer_value:
        return False

    source = str(resolved_answer.get("source") or "unknown").strip()
    logger.info(f'Filling [{source}] "{field.get("question_text")}" -> "{answer_value}"')

    try:
        if field.get("field_type") == "select":
            page.wait_for_selector(field["selector"], timeout=5000)
            matched = page.evaluate(
                '''
                ({ selector, answer }) => {
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
                }
                ''',
                {"selector": field["selector"], "answer": answer_value},
            )
            sleep(random_int(200, 500))
            return bool(matched)

        if field.get("field_type") == "radio":
            matched = page.evaluate(
                '''
                ({ selector, answer }) => {
                  const readLabelText = (node) => {
                    if (!node) return '';

                    const clone = node.cloneNode(true);
                    clone
                      .querySelectorAll('.visually-hidden, .artdeco-visually-hidden, .screen-reader-text, .sr-only')
                      .forEach((el) => el.remove());

                    const text = (clone.textContent || node.textContent || '').replace(/\\s+/g, ' ').trim();
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
                }
                ''',
                {"selector": field["selector"], "answer": answer_value},
            )
            sleep(random_int(200, 400))
            return bool(matched)

        if field.get("field_type") == "checkbox":
            answer = normalize(answer_value)
            if answer in {"yes", "true", "1"}:
                element = page.query_selector(field["selector"])
                checked = element.is_checked() if element else False
                if element and not checked:
                    element.click()
            sleep(random_int(100, 300))
            return True

        if field.get("field_type") == "combobox":
            page.wait_for_selector(field["selector"], timeout=5000)
            matched = page.evaluate(
                '''
                ({ selector, answer }) => {
                  const collapse = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                  const isVisible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                  };

                  const trigger = document.querySelector(selector);
                  if (!trigger) return false;

                  trigger.click();

                  const lower = collapse(answer).toLowerCase();
                  const candidates = [
                    ...document.querySelectorAll(
                      [
                        '[role="option"]',
                        'li[role="option"]',
                        'button[role="option"]',
                        '.artdeco-typeahead__result',
                        '.basic-typeahead__selectable',
                        '.jobs-easy-apply-form-section__grouping button',
                        '.artdeco-dropdown__item',
                      ].join(', ')
                    ),
                  ].filter(isVisible);

                  const readText = (node) => collapse(node?.innerText || node?.textContent || '');
                  const match = candidates.find((node) => {
                    const text = readText(node).toLowerCase();
                    return text && (text.includes(lower) || lower.includes(text));
                  });

                  if (!match) return false;
                  match.click();
                  return true;
                }
                ''',
                {"selector": field["selector"], "answer": answer_value},
            )
            sleep(random_int(300, 700))
            return bool(matched)

        element = page.query_selector(field["selector"])
        if not element:
            return False
        element.scroll_into_view_if_needed()
        element.click(click_count=3)
        element.fill("")
        sleep(random_int(100, 250))
        human_type(page, field["selector"], answer_value, clear=False, speed="fast")
        sleep(random_int(200, 400))
        return True
    except Exception as err:
        logger.warn(f'Fill error for "{field.get("question_text")}": {err}')
        return False


def get_validation_messages(page: Any) -> list[str]:
    return page.evaluate(
        '''
        (modalSel) => {
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
        }
        ''',
        MODAL_SEL,
    )


def persist_step_questions(app_id: str | None, account: dict[str, Any], job: dict[str, Any], step_index: int, questions: list[dict[str, Any]]) -> None:
    if not app_id or not questions:
        return

    unique_questions = [{**question, "step_index": step_index} for question in dedupe_captured_questions(questions)]
    application_questions.upsert_many(
        app_id,
        {
            "account_id": account["id"],
            "search_config_id": job.get("config_id"),
            "job_title_scope": job.get("job_role"),
            "job_title": job.get("title"),
            "company_name": job.get("company"),
            "step_index": step_index,
        },
        [
            {
                "question_text": question.get("question_text"),
                "field_type": question.get("field_type"),
                "options": question.get("options"),
                "answer": question.get("currentValue") or None,
                "is_required": question.get("is_required"),
                "is_answered": question.get("is_answered"),
                "step_index": step_index,
            }
            for question in unique_questions
        ],
    )


def persist_step_questions_once(
    app_id: str | None,
    account: dict[str, Any],
    job: dict[str, Any],
    step_index: int,
    questions: list[dict[str, Any]],
    persisted_signatures: set[str],
) -> str:
    unique_questions = dedupe_captured_questions(questions)
    signature = build_question_signature(unique_questions)

    if not signature or signature in persisted_signatures:
        return signature

    persist_step_questions(app_id, account, job, step_index, unique_questions)
    persisted_signatures.add(signature)
    return signature


def fill_current_step(
    page: Any,
    qa_templates: list[dict[str, Any]],
    job: dict[str, Any],
    answer_engine: Any | None,
    resume_profile: dict[str, Any] | None,
    step_index: int,
    logger: Any,
) -> dict[str, Any]:
    before_fields = dedupe_captured_questions(extract_fields(page))
    if not before_fields:
        try:
            page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass
        sleep(random_int(700, 1200))
        before_fields = dedupe_captured_questions(extract_fields(page))

    # Third attempt: wait explicitly for inputs to appear inside the form surface.
    # Covers both the modal-based "Continue" flow (saved state loads async) and
    # the SDUI apply page (form is in <main>, not in a modal overlay).
    if not before_fields:
        surface_input_sel = ", ".join(
            f"{sel} input:not([type='hidden']), {sel} select, {sel} textarea"
            for sel in [
                ".jobs-easy-apply-modal",
                '[data-test-modal-id="easy-apply-modal"]',
                '.artdeco-modal[role="dialog"]',
                ".jobs-easy-apply-content",
                "[data-live-test-job-apply-page]",
                # SDUI apply page: form lives in <main>, not in a modal
                "main",
            ]
        )
        try:
            page.wait_for_selector(surface_input_sel, timeout=8000)
        except Exception:
            pass
        before_fields = dedupe_captured_questions(extract_fields(page))

    logger.info(f"Fields in this step: {len(before_fields)}")
    if not before_fields:
        diagnostics = read_field_diagnostics(page)
        logger.warn(
            "No detectable fields in this step"
            + (
                f' | nativeInputs={diagnostics.get("nativeFieldCount", 0)}'
                f' | comboboxes={diagnostics.get("comboboxCount", 0)}'
                f' | buttons={" || ".join(diagnostics.get("buttons") or [])}'
                f' | snippet="{truncate_for_log(diagnostics.get("textSnippet"), 180)}"'
            )
        )
    log_field_label_debug(before_fields, step_index, logger)

    filled = 0
    for field in before_fields:
        resolved_answer = resolve_field_answer(field, qa_templates, job, answer_engine, resume_profile, logger)
        if apply_template_to_field(page, field, resolved_answer, logger):
            filled += 1

    after_fields = dedupe_captured_questions(extract_fields(page))
    after_map = {field.get("question_key"): field for field in after_fields}

    questions = dedupe_captured_questions(
        [
            {**(after_map.get(field.get("question_key")) or field), "step_index": step_index}
            for field in before_fields
        ]
    )

    pending_questions = get_pending_questions(questions)
    logger.info(f"Filled {filled}/{len(before_fields)} fields")

    if pending_questions:
        logger.warn(
            "Required questions still unanswered: "
            + " | ".join(str(question.get("question_text")) for question in pending_questions)
        )

    return {
        "questions": questions,
        "pendingQuestions": pending_questions,
        "signature": build_question_signature(questions),
    }


def handle_resume_upload(page: Any, resume_path: str | None, logger: Any) -> None:
    if not resume_path:
        return

    try:
        file_input = page.query_selector('input[type="file"]')
        if not file_input:
            return

        uploaded_label = page.query_selector(".jobs-document-upload__filename, .jobs-resume-picker__resume-name")
        if uploaded_label:
            logger.info("Resume already present in modal")
            return

        absolute_path = os.path.abspath(resume_path)
        file_input.set_input_files(absolute_path)
        sleep(random_int(2000, 3500))
        logger.info("Resume uploaded")
    except Exception as err:
        logger.warn(f"Resume upload failed: {err}")


def get_footer_action(page: Any) -> dict[str, Any] | None:
    checks = [
        {"sel": 'button[aria-label*="Submit application"]', "type": "submit"},
        {"sel": 'footer button[aria-label*="Submit"]', "type": "submit"},
        {"sel": 'button[aria-label*="Review your application"]', "type": "review"},
        {"sel": 'button[aria-label*="Continue to next step"]', "type": "next"},
        {"sel": 'a[aria-label*="Continue to next step"]', "type": "next"},
        {"sel": 'button[aria-label*="Next"]', "type": "next"},
        {"sel": 'a[aria-label*="Next"]', "type": "next"},
        {"sel": '[data-live-test-job-apply-page] .artdeco-button--primary', "type": "primary"},
        {"sel": 'form[action*="/apply"] .artdeco-button--primary', "type": "primary"},
        {"sel": ".jobs-easy-apply-modal footer .artdeco-button--primary", "type": "primary"},
        {"sel": ".artdeco-modal__actionbar .artdeco-button--primary", "type": "primary"},
        {"sel": "footer .artdeco-button--primary", "type": "primary"},
    ]

    for check in checks:
        try:
            buttons = page.query_selector_all(check["sel"])
        except Exception:
            buttons = []

        for button in buttons:
            if not is_button_interactable(button):
                continue

            action_type = check["type"]
            if action_type == "primary":
                action_type = classify_action_label(read_button_label(button))
            return {"type": action_type, "button": button}

    # Fallback: scan all visible button/a elements by visible text for
    # "Continue" buttons that lack a matching aria-label.
    try:
        candidates = page.query_selector_all("button, a")
    except Exception:
        candidates = []

    for element in candidates:
        if not is_button_interactable(element):
            continue
        label = read_button_label(element)
        action_type = classify_action_label(label)
        if action_type in ("next", "review", "submit"):
            return {"type": action_type, "button": element}

    return None


def is_modal_open(page: Any) -> bool:
    try:
        element = page.query_selector(MODAL_SEL)
        if element and element.is_visible():
            return True
    except Exception:
        pass

    if not is_apply_flow_url(page.url):
        return False

    try:
        return bool(
            page.evaluate(
                """
                () => !!(
                  document.querySelector('[data-live-test-job-apply-page]') ||
                  document.querySelector('form[action*="/apply"]') ||
                  document.querySelector('input, select, textarea')
                )
                """
            )
        )
    except Exception:
        return False


def is_success(page: Any) -> bool:
    return bool(
        page.evaluate(
            """
            () => {
              const content = document.body.innerText.toLowerCase();
              return (
                content.includes('application was sent') ||
                content.includes('your application was submitted') ||
                content.includes('applied to') ||
                !!document.querySelector('.artdeco-inline-feedback--success') ||
                !!document.querySelector('[data-test-applied-status]') ||
                !!document.querySelector('.jobs-easy-apply-content__confirmation')
              );
            }
            """
        )
    )


def find_visible_button(page: Any, selectors: list[str]) -> Any | None:
    for selector in selectors:
        try:
            buttons = page.query_selector_all(selector)
        except Exception:
            buttons = []
        for button in buttons:
            try:
                if button.is_visible():
                    return button
            except Exception:
                continue
    return None


def click_button_by_text(page: Any, patterns: list[str]) -> str:
    return str(
        page.evaluate(
            '''
            (rawPatterns) => {
              const regexes = rawPatterns.map((raw) => new RegExp(raw, 'i'));
              const elements = [...document.querySelectorAll('button, a')];

              for (const btn of elements) {
                const style = window.getComputedStyle(btn);
                if (style.display === 'none' || style.visibility === 'hidden') continue;

                const text = `${btn.textContent || ''} ${btn.getAttribute('aria-label') || ''}`.trim();
                if (regexes.some((re) => re.test(text))) {
                  btn.click();
                  return text;
                }
              }

              return '';
            }
            ''',
            patterns,
        )
        or ""
    )


def close_modal(page: Any, preserve_application: bool = False, logger: Any | None = None) -> dict[str, Any]:
    try:
        dismiss_selectors = [
            'button[aria-label="Dismiss"]',
            ".artdeco-modal__dismiss",
            "button[data-test-modal-close-btn]",
        ]

        for selector in dismiss_selectors:
            button = page.query_selector(selector)
            if button and button.is_visible():
                button.click()
                sleep(1000)
                break

        sleep(800)

        if not is_modal_open(page):
            if preserve_application and logger:
                logger.info("Closed pending application without a discard prompt")
            return {"preserved": preserve_application, "closed": True}

        if preserve_application:
            save_selectors = [
                'button[data-control-name*="save_application"]',
                'button[aria-label*="Save application"]',
                'button[aria-label*="Save"]',
                ".artdeco-modal button.artdeco-button--primary",
            ]

            save_button = find_visible_button(page, save_selectors)
            if save_button:
                label = (save_button.text_content() or save_button.get_attribute("aria-label") or "").strip()
                if re.search(r"save|keep", label, re.I) or re.search(r"save_application", label, re.I):
                    save_button.click()
                    sleep(1200)
                    if logger:
                        logger.info(f'Saved pending application before closing modal: "{label}"')
                    return {"preserved": True, "closed": True}

            saved_by_text = click_button_by_text(page, [r"save application", r"^save$", r"keep application"])
            if saved_by_text:
                sleep(1200)
                if logger:
                    logger.info(f'Saved pending application before closing modal: "{saved_by_text}"')
                return {"preserved": True, "closed": True}

            if logger:
                logger.warn('Could not confirm a "Save application" action, falling back to discard so the bot can continue')

        discard = find_visible_button(
            page,
            [
                'button[data-control-name="discard_application_confirm_btn"]',
                'button[aria-label*="Discard"]',
                'button[aria-label*="Don\\\'t save"]',
            ],
        )
        if discard:
            discard.click()
            sleep(800)

        return {"preserved": False, "closed": True}
    except Exception:
        return {"preserved": False, "closed": False}


def move_application_to_pending_questions(
    app_id: str | None,
    pending_questions: list[dict[str, Any]],
    validation_messages: list[str],
    logger: Any,
) -> str | None:
    if not app_id or not pending_questions:
        return None

    reason_parts = [f"Awaiting answers for {len(pending_questions)} required question(s)"]
    if validation_messages:
        reason_parts.append(" | ".join(validation_messages))

    reason = " - ".join(reason_parts)
    applications.update_status(app_id, "pending_questions", reason)
    logger.warn(
        "Stored application under pending questions: "
        + " | ".join(str(question.get("question_text")) for question in pending_questions)
    )
    return reason


def apply_to_job(
    page: Any,
    job: dict[str, Any],
    account: dict[str, Any],
    qa_templates: list[dict[str, Any]],
    answer_engine: Any | None,
    resume_profile: dict[str, Any] | None,
    logger: Any,
) -> str:
    logger.info(f'Applying: "{job.get("title")}" @ "{job.get("company")}"')

    app_id = job.get("application_id")
    persisted_signatures: set[str] = set()
    if app_id:
        applications.update_status(app_id, "pending", None)
        try:
            existing_questions = application_questions.get_by_application(app_id, True)
            existing_signature = build_question_signature(dedupe_captured_questions(existing_questions))
            if existing_signature:
                persisted_signatures.add(existing_signature)
        except Exception:
            pass
    else:
        app_row = applications.create(
            {
                "account_id": account["id"],
                "search_config_id": job.get("config_id"),
                "job_url": job.get("url"),
                "job_title": job.get("title"),
                "company_name": job.get("company"),
                "location": job.get("location"),
                "is_easy_apply": True,
                "status": "pending",
            }
        )
        app_id = app_row.get("id")

    try:
        has_inline_context = has_inline_easy_apply_context(page)
        job_url = str(job.get("url") or "")
        expected_path = job_url.replace("https://www.linkedin.com", "")
        if not has_inline_context and expected_path and expected_path not in page.url:
            page.goto(job_url, wait_until="domcontentloaded", timeout=20000)
            sleep(random_int(1500, 2500))

        target_check = confirm_expected_job_context(page, job)
        if not target_check["ok"]:
            reason = build_job_mismatch_message(job, target_check.get("actual"))
            logger.warn(reason)
            if app_id:
                applications.update_status(app_id, "manual_review", reason)
            return "skipped"

        if not open_easy_apply_modal(page, logger):
            if app_id:
                applications.update_status(app_id, "manual_review", "Easy Apply modal did not open")
            return "skipped"

        step = 0
        max_steps = 20

        while step < max_steps:
            step += 1
            logger.info(f"Step {step}")

            handle_resume_upload(page, account.get("resume_path"), logger)
            step_result = fill_current_step(page, qa_templates, job, answer_engine, resume_profile, step, logger)
            persist_step_questions_once(app_id, account, job, step, step_result["questions"], persisted_signatures)

            human_scroll(page, "down", 300)
            sleep(random_int(400, 800))

            if is_success(page):
                logger.info(f'Applied: "{job.get("title")}"')
                if app_id:
                    applications.update_status(app_id, "applied")
                close_modal(page)
                return "applied"

            action = get_footer_action(page)
            if not action:
                validation_messages = get_validation_messages(page)
                logger.warn("No footer action button found")

                if is_success(page):
                    if app_id:
                        applications.update_status(app_id, "applied")
                    return "applied"

                pending_reason = move_application_to_pending_questions(
                    app_id,
                    step_result["pendingQuestions"],
                    validation_messages,
                    logger,
                )
                if pending_reason:
                    close_result = close_modal(page, preserve_application=True, logger=logger)
                    if not close_result["preserved"] and app_id:
                        applications.update_status(
                            app_id,
                            "pending_questions",
                            f"{pending_reason} - LinkedIn draft could not be saved automatically",
                        )
                    return "pending_questions"

                break

            logger.info(f'Action button: {action["type"]}')
            before_signature = step_result["signature"]
            before_checkpoint = read_application_checkpoint(page)

            if action["type"] == "submit":
                action["button"].click()
                sleep(random_int(2000, 3500))
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass

                if is_success(page):
                    logger.info(f'Applied (post-submit): "{job.get("title")}"')
                    if app_id:
                        applications.update_status(app_id, "applied")
                    close_modal(page)
                    return "applied"

                if is_modal_open(page):
                    after_submit_questions = dedupe_captured_questions(
                        [{**question, "step_index": step} for question in extract_fields(page)]
                    )
                    validation_messages = get_validation_messages(page)
                    blocking_questions = get_blocking_questions(after_submit_questions)
                    after_checkpoint = read_application_checkpoint(page)
                    after_submit_signature = build_question_signature(after_submit_questions)
                    if after_submit_signature == before_signature:
                        if (
                            not validation_messages
                            and not blocking_questions
                            and application_checkpoint_changed(before_checkpoint, after_checkpoint)
                        ):
                            logger.info(
                                f'Application advanced after {action["type"]} without exposing new questions'
                            )
                            continue

                        logger.warn(f'Application did not advance after {action["type"]}')
                        persist_step_questions_once(
                            app_id,
                            account,
                            job,
                            step,
                            after_submit_questions,
                            persisted_signatures,
                        )
                        pending_reason = move_application_to_pending_questions(
                            app_id,
                            blocking_questions,
                            validation_messages,
                            logger,
                        )
                        if pending_reason:
                            close_result = close_modal(page, preserve_application=True, logger=logger)
                            if not close_result["preserved"] and app_id:
                                applications.update_status(
                                    app_id,
                                    "pending_questions",
                                    f"{pending_reason} - LinkedIn draft could not be saved automatically",
                                )
                            return "pending_questions"
                        break

                logger.info("Submit clicked but success not confirmed yet - continuing")
                continue

            action["button"].click()
            sleep(random_int(1000, 1800))
            try:
                page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass

            if not is_modal_open(page):
                if is_success(page):
                    logger.info(f'Applied (modal closed): "{job.get("title")}"')
                    if app_id:
                        applications.update_status(app_id, "applied")
                    return "applied"
                break

            post_action_questions = dedupe_captured_questions(
                [{**question, "step_index": step} for question in extract_fields(page)]
            )
            validation_messages = get_validation_messages(page)
            blocking_questions = get_blocking_questions(post_action_questions)
            after_checkpoint = read_application_checkpoint(page)
            after_signature = build_question_signature(post_action_questions)

            if after_signature == before_signature:
                if (
                    not validation_messages
                    and not blocking_questions
                    and application_checkpoint_changed(before_checkpoint, after_checkpoint)
                ):
                    logger.info(
                        f'Application advanced after {action["type"]} without exposing new questions'
                    )
                    continue

                logger.warn(f'Application did not advance after {action["type"]}')
                persist_step_questions_once(
                    app_id,
                    account,
                    job,
                    step,
                    post_action_questions,
                    persisted_signatures,
                )
                pending_reason = move_application_to_pending_questions(
                    app_id,
                    blocking_questions,
                    validation_messages,
                    logger,
                )
                if pending_reason:
                    close_result = close_modal(page, preserve_application=True, logger=logger)
                    if not close_result["preserved"] and app_id:
                        applications.update_status(
                            app_id,
                            "pending_questions",
                            f"{pending_reason} - LinkedIn draft could not be saved automatically",
                        )
                    return "pending_questions"
                break

        try:
            raw_final_questions = extract_fields(page)
        except Exception:
            raw_final_questions = []

        final_questions = [{**question, "step_index": step or 1} for question in raw_final_questions]

        if final_questions:
            persist_step_questions_once(
                app_id,
                account,
                job,
                step or 1,
                final_questions,
                persisted_signatures,
            )
            pending_questions = get_pending_questions(final_questions)
            validation_messages = get_validation_messages(page)
            pending_reason = move_application_to_pending_questions(
                app_id,
                pending_questions,
                validation_messages,
                logger,
            )
            if pending_reason:
                close_result = close_modal(page, preserve_application=True, logger=logger)
                if not close_result["preserved"] and app_id:
                    applications.update_status(
                        app_id,
                        "pending_questions",
                        f"{pending_reason} - LinkedIn draft could not be saved automatically",
                    )
                return "pending_questions"

        logger.warn(f'Could not confirm submission for: "{job.get("title")}"')
        if app_id:
            applications.update_status(app_id, "failed", "Step loop exhausted without submission")
        close_modal(page)
        return "failed"
    except Exception as err:
        logger.error(f"apply_to_job error: {err}")
        if app_id:
            applications.update_status(app_id, "failed", str(err))
        try:
            close_modal(page)
        except Exception:
            pass
        return "failed"
