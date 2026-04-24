from __future__ import annotations

import re
import traceback
from typing import Any

from ..db import account_resume_profiles, accounts, applications, qa_templates, search_configs
from ..logger import WorkerLogger
from ..utils.humanize import random_int, sleep
from .answer_engine_bridge import build_answer_engine
from .easy_apply import apply_to_job
from .linkedin_auth import create_session
from .linkedin_search import search_jobs

REQUIRED_QA_TEMPLATE_FIELDS = [
    {"label": "Phone Number", "patterns": ["phone", "phone number", "mobile", "mobile number", "telephone"]},
    {"label": "Years of Experience", "patterns": ["years of experience", "total experience", "overall experience"]},
    {"label": "LinkedIn URL", "patterns": ["linkedin", "linkedin url", "linkedin profile", "linkedin profile url"]},
    {"label": "Current Location", "patterns": ["current location", "current city", "where are you located", "currently located"]},
]


def _normalize_template_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _has_required_template(templates: list[dict[str, Any]], patterns: list[str]) -> bool:
    normalized_patterns = {_normalize_template_text(pattern) for pattern in patterns if _normalize_template_text(pattern)}

    for template in templates or []:
        question_pattern = _normalize_template_text(template.get("question_pattern"))
        answer = str(template.get("answer") or "").strip()
        if question_pattern in normalized_patterns and answer:
            return True

    return False


def _find_missing_required_qa_templates(templates: list[dict[str, Any]]) -> list[str]:
    missing: list[str] = []

    for field in REQUIRED_QA_TEMPLATE_FIELDS:
        if not _has_required_template(templates, field["patterns"]):
            missing.append(field["label"])

    return missing

def run_worker_process(account_id: str, prioritized_application_id: str | None, event_queue: Any) -> None:
    resume_profile = account_resume_profiles.get_by_account(account_id) or {}
    def emit(event_type: str, payload: dict[str, Any]) -> None:
        event_queue.put({"type": event_type, "accountId": account_id, "payload": payload})

    logger = WorkerLogger(account_id, emit)
    emit("status", {"status": "starting"})

    session: dict[str, Any] | None = None

    try:
        account = accounts.get_by_id(account_id)
        if not account:
            raise RuntimeError(f"Account {account_id} not found in DB")

        logger.label = account.get("label") or account.get("email") or ""

        accounts.update_status(account_id, "running")
        emit("status", {"status": "running"})

        templates = qa_templates.get_for_account(account_id)
        logger.info(f"Loaded {len(templates)} Q&A templates")
        missing_required_templates = _find_missing_required_qa_templates(templates)
        if missing_required_templates:
            message = "Missing required QA templates: " + ", ".join(missing_required_templates)
            logger.warn(message)
            accounts.update_status(account_id, "idle")
            emit("status", {"status": "idle", "message": message})
            return
        # Optional model fallback. Set ENABLE_ANSWER_ENGINE=false to disable it quickly.
        answer_engine = build_answer_engine(account, templates, logger)

        def load_current_templates() -> list[dict[str, Any]]:
            return qa_templates.get_for_account(account_id)

        session = create_session(account, logger)
        browser = session["browser"]
        page = session["page"]
        logged_in = session["logged_in"]

        if not logged_in:
            emit("status", {"status": "error", "message": "Login failed"})
            accounts.update_status(account_id, "error")
            browser.close()
            session["playwright"].stop()
            return

        configs = search_configs.get_by_account(account_id)
        if not configs:
            logger.warn("No active search configs - add one in the dashboard")
            browser.close()
            session["playwright"].stop()
            accounts.update_status(account_id, "idle")
            emit("status", {"status": "idle", "message": "No search configs"})
            return

        stats = {"applied": 0, "failed": 0, "skipped": 0, "pending_questions": 0, "manual_review": 0}

        retry_queue = applications.get_retry_queue(account_id, None, prioritized_application_id)
        if prioritized_application_id:
            logger.info(f"Prioritizing retry application {prioritized_application_id}")

        seen_retry_jobs: set[str] = set()
        for row in retry_queue:
            retry_key = row.get("job_url") or f'{row.get("job_title")}|{row.get("company_name")}'
            if retry_key in seen_retry_jobs:
                logger.info(f'Skipping duplicate retry job: "{row.get("job_title")}" @ "{row.get("company_name")}"')
                continue
            seen_retry_jobs.add(retry_key)

            retry_job = {
                "application_id": row["id"],
                "config_id": row.get("search_config_id"),
                "url": row.get("job_url"),
                "title": row.get("job_title"),
                "company": row.get("company_name"),
                "location": row.get("location"),
                "job_role": row.get("job_role") or row.get("job_title"),
            }

            emit(
                "progress",
                {
                    "phase": "retrying",
                    "config": retry_job["job_role"],
                    "job": retry_job["title"],
                    "company": retry_job["company"],
                },
            )

            logger.info(f'Retrying pending-question application: "{retry_job["title"]}"')
            result = apply_to_job(page, retry_job, account, load_current_templates(), answer_engine, resume_profile, logger)

            if result == "applied":
                stats["applied"] += 1
            elif result == "failed":
                stats["failed"] += 1
            elif result == "skipped":
                stats["skipped"] += 1
            elif result == "pending_questions":
                stats["pending_questions"] += 1

            emit("application", {"job": retry_job, "result": result, "stats": stats})

            retry_pause = random_int(8000, 18000)
            logger.info(f"Waiting {round(retry_pause / 1000)}s before the next retry...")
            sleep(retry_pause)

        for index, config in enumerate(configs):
            logger.info(f'=== Config: "{config["job_title"]}" in "{config.get("location") or "anywhere"}" ===')
            emit("progress", {"phase": "searching", "config": config["job_title"]})

            def on_search_job_result(job: dict[str, Any], result: str) -> None:
                if result == "applied":
                    stats["applied"] += 1
                elif result == "failed":
                    stats["failed"] += 1
                elif result == "skipped":
                    stats["skipped"] += 1
                elif result == "pending_questions":
                    stats["pending_questions"] += 1

                emit("application", {"job": job, "result": result, "stats": stats})

            def on_easy_apply_job(job: dict[str, Any]) -> str:
                emit(
                    "progress",
                    {
                        "phase": "applying",
                        "config": config["job_title"],
                        "job": job.get("title"),
                        "company": job.get("company"),
                    },
                )

                logger.info(f'Applying immediately from right pane: "{job.get("title")}"')
                result = apply_to_job(page, job, account, load_current_templates(), answer_engine, resume_profile, logger)

                if result == "applied":
                    stats["applied"] += 1
                elif result == "failed":
                    stats["failed"] += 1
                elif result == "skipped":
                    stats["skipped"] += 1
                elif result == "pending_questions":
                    stats["pending_questions"] += 1

                emit("application", {"job": job, "result": result, "stats": stats})

                pause = random_int(12000, 30000)
                logger.info(f"Waiting {round(pause / 1000)}s before the next job...")
                sleep(pause)
                return result

            search_jobs(page, config, account_id, logger, on_easy_apply_job, on_search_job_result)

            if index < len(configs) - 1:
                logger.info("Pausing between search configs...")
                sleep(random_int(5000, 15000))

        browser.close()
        session["playwright"].stop()
        accounts.update_status(account_id, "idle")
        emit("status", {"status": "completed", "stats": stats})
        logger.info(f"Worker completed. Stats: {stats}")
    except Exception as err:
        logger.error(f"Worker crashed: {err}", {"stack": traceback.format_exc()})
        try:
            accounts.update_status(account_id, "error")
        except Exception:
            pass
        emit("status", {"status": "error", "message": str(err)})
    finally:
        if session:
            try:
                session["browser"].close()
            except Exception:
                pass
            try:
                session["playwright"].stop()
            except Exception:
                pass
