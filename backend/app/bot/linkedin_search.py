from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlencode

from ..db import applications
from ..utils.humanize import human_delay, random_int, sleep
from .linkedin_job_utils import build_linkedin_job_url, compare_job_identity


BASE_URL = "https://www.linkedin.com/jobs/search/"
RESULTS_LIST_SEL = ".jobs-search-results-list, .scaffold-layout__list, .jobs-search__results-list"


def build_search_url(config: dict[str, Any], start: int = 0) -> str:
    params: dict[str, str] = {"keywords": config["job_title"], "sortBy": "DD"}
    if config.get("location"):
        params["location"] = config["location"]
    if config.get("easy_apply_only"):
        params["f_LF"] = "f_AL"
    if config.get("remote_only"):
        params["f_WT"] = "2"
    if start > 0:
        params["start"] = str(start)

    date_map = {"past_24h": "r86400", "past_week": "r604800", "past_month": "r2592000"}
    if config.get("date_posted") in date_map:
        params["f_TPR"] = date_map[config["date_posted"]]

    exp_map = {"entry": "2", "associate": "3", "mid": "4", "senior": "5", "director": "6"}
    experience = [exp_map[item] for item in config.get("experience_level") or [] if item in exp_map]
    if experience:
        params["f_E"] = ",".join(experience)

    type_map = {"full_time": "F", "part_time": "P", "contract": "C", "internship": "I"}
    job_types = [type_map[item] for item in config.get("job_type") or [] if item in type_map]
    if job_types:
        params["f_JT"] = ",".join(job_types)

    return f"{BASE_URL}?{urlencode(params)}"


def wait_for_page_load(page: Any, logger: Any) -> bool:
    page.wait_for_load_state("domcontentloaded", timeout=15000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    sleep(random_int(1200, 2200))

    url = page.url
    if any(segment in url for segment in ["/login", "/authwall", "/checkpoint"]):
        logger.error(f"Session invalid - redirected to: {url}")
        return False

    try:
        page.wait_for_selector(RESULTS_LIST_SEL, timeout=12000)
    except Exception:
        pass
    return True


def ensure_search_results_context(page: Any, search_url: str, logger: Any) -> bool:
    try:
        element = page.query_selector(RESULTS_LIST_SEL)
        has_results_list = bool(element and element.is_visible())
    except Exception:
        has_results_list = False

    if "/jobs/search" in page.url and has_results_list:
        return True

    logger.info("Returning to search results to continue scanning cards")
    page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
    if not wait_for_page_load(page, logger):
        return False

    scroll_to_reveal_cards(page)
    return True


def scroll_to_reveal_cards(page: Any) -> None:
    page.evaluate(
        """
        () => {
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
        }
        """
    )
    sleep(random_int(1800, 2600))


def extract_visible_jobs(page: Any) -> list[dict[str, Any]]:
    return page.evaluate(
        """
        () => {
          const seen = new Set();
          const jobs = [];
          const normalizeText = (value) => String(value || '').trim().toLowerCase().replace(/\\s+/g, ' ');

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
            const hrefMatch = href.match(/\\/jobs\\/view\\/(\\d+)/);
            const jobId = dataJobId || hrefMatch?.[1];
            if (!jobId) return;

            const rawText = (li.innerText || '').trim();
            const lines = rawText
              .split('\\n')
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
                  !/easy apply|promoted|viewed|ago|applicants?|applicant|remote|hybrid|on-site|onsite|\\$|benefit/i.test(line)
                ) || '';
            }

            pushJob(jobId, title, company, rawText);
          });

          document.querySelectorAll('a[href*="/jobs/view/"]').forEach((a) => {
            const match = a.href.match(/\\/jobs\\/view\\/(\\d+)/);
            if (!match) return;
            pushJob(match[1], a.textContent?.trim() || '', '', a.textContent?.trim() || '');
          });

          return jobs;
        }
        """
    )


def click_job_card(page: Any, job_id: str, logger: Any) -> bool:
    clicked = page.evaluate(
        """
        (targetJobId) => {
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
        }
        """,
        job_id,
    )

    if not clicked:
        logger.warn(f"Could not click job card {job_id}")
        return False

    sleep(random_int(1200, 2200))
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    sleep(random_int(600, 1200))
    return True


def load_job_detail_from_pane(page: Any) -> dict[str, Any]:
    return page.evaluate(
        """
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

          const detailRoot =
            document.querySelector('.jobs-search__job-details--container') ||
            document.querySelector('.scaffold-layout__detail') ||
            document.querySelector('.jobs-details') ||
            document.querySelector('.job-view-layout') ||
            document;

          const isVisible = (el) => {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
          };

          // Detect "Easy Apply" buttons, "Continue applying" re-entry buttons, and
          // SDUI apply-flow anchor links (<a href="...openSDUIApplyFlow=true..."><span>Continue</span></a>).
          const easyApplyButton =
            // Standard Easy Apply / Continue applying button
            [...detailRoot.querySelectorAll('button.jobs-apply-button, .jobs-apply-button, button[data-live-test-job-apply-button]')]
              .find((el) => {
                if (!isVisible(el)) return false;
                const text = `${el.textContent || ''} ${el.getAttribute('aria-label') || ''}`.trim();
                return /easy apply|continue/i.test(text);
              }) ||
            // SDUI apply-flow anchor: <a href="...openSDUIApplyFlow=true..."><span>Continue</span></a>
            [...detailRoot.querySelectorAll('a[href*="openSDUIApplyFlow=true"], a[href*="/jobs/view/"][href*="/apply"]')]
              .find((el) => isVisible(el)) ||
            null;

          const alreadyApplied =
            !!document.querySelector('.artdeco-inline-feedback--success') ||
            !!document.querySelector('[data-test-applied-status]') ||
            document.body.innerText.includes('Application submitted') ||
            document.body.innerText.includes('Application was sent');

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
            title,
            company,
            location,
            url: jobId ? `https://www.linkedin.com/jobs/view/${jobId}/` : window.location.href,
            isEasyApply: !!easyApplyButton && !easyApplyButton.disabled && easyApplyButton.getAttribute('aria-disabled') !== 'true',
            alreadyApplied,
          };
        }
        """
    )


def load_confirmed_job_detail_from_pane(page: Any, job: dict[str, Any], logger: Any) -> dict[str, Any] | None:
    expected = {
        "jobId": job.get("jobId"),
        "url": build_linkedin_job_url(job.get("jobId")),
        "title": job.get("title"),
        "company": job.get("company"),
    }

    detail: dict[str, Any] | None = None
    comparison = compare_job_identity(expected, {})

    for attempt in range(1, 6):
        detail = load_job_detail_from_pane(page)
        comparison = compare_job_identity(expected, detail)
        if comparison["matches"]:
            return detail

        if attempt < 5:
            try:
                page.wait_for_load_state("networkidle", timeout=4000)
            except Exception:
                pass
            sleep(random_int(600, 1100))

    logger.warn(
        f'Detail pane mismatch for card {job.get("jobId")}: expected "{job.get("title")}" @ "{job.get("company")}", '
        f'found "{(detail or {}).get("title", "Unknown title")}" @ "{(detail or {}).get("company", "Unknown company")}"'
    )
    return None


def has_next_page(page: Any) -> bool:
    return page.evaluate(
        """
        () => {
          const btn = document.querySelector('button[aria-label="View next page"]');
          if (btn && !btn.disabled && btn.offsetParent !== null) return true;

          const active = document.querySelector('.artdeco-pagination__indicator--number.active');
          if (active && active.nextElementSibling) return true;

          return false;
        }
        """
    )


def click_next_page(page: Any, logger: Any) -> bool:
    clicked = page.evaluate(
        """
        () => {
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
        }
        """
    )

    if not clicked:
        return False

    sleep(random_int(2000, 3500))
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    sleep(random_int(800, 1400))
    logger.info("Moved to next page")
    return True


def search_jobs(
    page: Any,
    config: dict[str, Any],
    account_id: str,
    logger: Any,
    on_easy_apply_job: Callable[[dict[str, Any]], str] | None = None,
    on_search_job_result: Callable[[dict[str, Any], str], None] | None = None,
) -> list[dict[str, Any]]:
    search_url = build_search_url(config)
    logger.info(f'Search: "{config["job_title"]}" in "{config.get("location") or "anywhere"}"')
    logger.info(f"URL: {search_url}")

    page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
    if not wait_for_page_load(page, logger):
        return []
    active_search_url = page.url

    easy_apply_jobs: list[dict[str, Any]] = []
    seen_job_ids: set[str] = set()
    page_num = 1
    total_seen = 0
    consecutive_empty = 0
    max_jobs = int(config.get("max_applications") or 50)

    while total_seen < max_jobs:
        if not ensure_search_results_context(page, active_search_url, logger):
            break
        active_search_url = page.url

        logger.info(f"Page {page_num} - scrolling to load cards...")
        scroll_to_reveal_cards(page)

        visible_jobs = extract_visible_jobs(page)
        new_jobs = [job for job in visible_jobs if job["jobId"] not in seen_job_ids]
        logger.info(f"Page {page_num}: {len(visible_jobs)} visible jobs, {len(new_jobs)} new")

        if not new_jobs:
            consecutive_empty += 1
            logger.warn(f"Empty page {consecutive_empty}/3")
            if consecutive_empty >= 3:
                logger.warn("3 consecutive empty pages - stopping search")
                break
        else:
            consecutive_empty = 0

        for job in new_jobs:
            if total_seen >= max_jobs:
                break
            seen_job_ids.add(job["jobId"])

            if not ensure_search_results_context(page, active_search_url, logger):
                logger.warn("Could not restore the current search results page - stopping this search config")
                return easy_apply_jobs

            job_url = build_linkedin_job_url(job["jobId"])
            existing_row = applications.find_by_url(account_id, job_url)
            existing_status = str((existing_row or {}).get("status") or "").strip().lower()

            if existing_status == "applied":
                logger.info(f'Already applied in DB: {job["jobId"]}')
                continue

            if existing_row:
                logger.info(f'In DB with status "{existing_row.get("status")}" - rechecking {job["jobId"]}')

            logger.info(f'Opening card {job["jobId"]} ({total_seen + 1}/{max_jobs})')
            if not click_job_card(page, job["jobId"], logger):
                continue

            detail = load_confirmed_job_detail_from_pane(page, job, logger)
            total_seen += 1

            if not detail:
                logger.warn(f'Skipping card {job["jobId"]} because the detail pane did not settle on the selected job')
                if on_search_job_result:
                    on_search_job_result(
                        {
                            "jobId": job["jobId"],
                            "url": job_url,
                            "title": job.get("title"),
                            "company": job.get("company"),
                            "location": None,
                            "config_id": config["id"],
                            "job_role": config["job_title"],
                        },
                        "skipped",
                    )
                continue

            logger.info(
                f'"{detail.get("title")}" @ "{detail.get("company")}" | '
                f'EasyApply={detail.get("isEasyApply")} | Applied={detail.get("alreadyApplied")}'
            )

            if not detail.get("title"):
                if on_search_job_result:
                    on_search_job_result(
                        {
                            "jobId": job["jobId"],
                            "url": detail.get("url") or job_url,
                            "title": detail.get("title") or job.get("title"),
                            "company": detail.get("company") or job.get("company"),
                            "location": detail.get("location"),
                            "config_id": config["id"],
                            "job_role": config["job_title"],
                        },
                        "skipped",
                    )
                continue

            if detail.get("alreadyApplied"):
                existing_or_created = applications.create(
                    {
                        "account_id": account_id,
                        "search_config_id": config["id"],
                        "job_url": detail["url"],
                        "job_title": detail.get("title"),
                        "company_name": detail.get("company"),
                        "location": detail.get("location"),
                        "is_easy_apply": bool(detail.get("isEasyApply")),
                        "status": "applied",
                    }
                )
                if existing_or_created.get("id"):
                    applications.update_status(existing_or_created["id"], "applied", "Already applied on LinkedIn")
                if on_search_job_result:
                    on_search_job_result(
                        {
                            "jobId": job["jobId"],
                            "url": detail["url"],
                            "title": detail.get("title"),
                            "company": detail.get("company"),
                            "location": detail.get("location"),
                            "config_id": config["id"],
                            "job_role": config["job_title"],
                        },
                        "skipped",
                    )
                continue

            if detail.get("isEasyApply"):
                easy_apply_job = {
                    "jobId": job["jobId"],
                    "url": detail["url"],
                    "title": detail.get("title"),
                    "company": detail.get("company"),
                    "location": detail.get("location"),
                    "config_id": config["id"],
                    "job_role": config["job_title"],
                }
                easy_apply_jobs.append(easy_apply_job)
                logger.info(f'Queued Easy Apply: {detail.get("title")}')

                if on_easy_apply_job:
                    result = on_easy_apply_job(easy_apply_job)
                    logger.info(f'Immediate apply result for "{detail.get("title")}": {result}')

                    if not ensure_search_results_context(page, active_search_url, logger):
                        logger.warn("Could not restore search results after the apply attempt - stopping this search config")
                        return easy_apply_jobs

                    try:
                        page.wait_for_selector(RESULTS_LIST_SEL, timeout=5000)
                    except Exception:
                        pass
            else:
                applications.create(
                    {
                        "account_id": account_id,
                        "search_config_id": config["id"],
                        "job_url": detail["url"],
                        "job_title": detail.get("title"),
                        "company_name": detail.get("company"),
                        "location": detail.get("location"),
                        "is_easy_apply": False,
                        "status": "manual_review",
                    }
                )
                logger.info(f'Manual review: {detail.get("title")}')
                if on_search_job_result:
                    on_search_job_result(
                        {
                            "jobId": job["jobId"],
                            "url": detail["url"],
                            "title": detail.get("title"),
                            "company": detail.get("company"),
                            "location": detail.get("location"),
                            "config_id": config["id"],
                            "job_role": config["job_title"],
                        },
                        "skipped",
                    )

            sleep(random_int(500, 1000))

        if total_seen >= max_jobs:
            break

        if not has_next_page(page):
            logger.info("No more pages available")
            break

        if not click_next_page(page, logger):
            logger.warn("Could not advance to next page - stopping")
            break

        page_num += 1
        active_search_url = page.url
        human_delay("normal")

    logger.info(f"Search complete - Easy Apply queued: {len(easy_apply_jobs)}, total seen: {total_seen}")
    return easy_apply_jobs
