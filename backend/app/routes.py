from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from pathlib import Path
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile

from .config import RESUME_UPLOAD_DIR
from fastapi.responses import StreamingResponse

from .db import (
    accounts,
    application_questions,
    applications,
    qa_templates,
    query_all,
    search_configs,
    account_resume_profiles
)
from .events import event_broker
from .worker_manager import worker_manager


router = APIRouter(prefix="/api")


async def _sse_stream(request: Request):
    subscriber = event_broker.subscribe()
    try:
        while True:
            if await request.is_disconnected():
                break

            try:
                message = await asyncio.wait_for(subscriber.get(), timeout=25)
                yield f"data: {json.dumps(message)}\n\n"
            except asyncio.TimeoutError:
                yield ": ping\n\n"
    finally:
        event_broker.unsubscribe(subscriber)


def _safe_account(account: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in account.items() if key not in {"password", "session_data"}}


def _bad_request(message: str) -> None:
    raise HTTPException(status_code=400, detail=message)


def _get_account_or_404(account_id: str) -> dict[str, Any]:
    account = accounts.get_by_id(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@router.get("/events")
async def events(request: Request):
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Access-Control-Allow-Origin": "*",
    }
    return StreamingResponse(_sse_stream(request), media_type="text/event-stream", headers=headers)


@router.get("/accounts")
def get_accounts():
    return [_safe_account(row) for row in accounts.get_all()]


@router.get("/accounts/stats")
def get_account_stats():
    return accounts.get_stats()


@router.post("/accounts", status_code=201)
def create_account(payload: dict[str, Any]):
    label = payload.get("label")
    email = payload.get("email")
    password = payload.get("password")
    if not email or not password:
        _bad_request("email and password required")

    account = accounts.create(label, email, password, None)
    return _safe_account(account)


@router.put("/accounts/{account_id}")
def update_account(account_id: str, payload: dict[str, Any]):
    fields = {key: value for key, value in payload.items() if key in {"label", "email", "password"} and value}
    updated = accounts.update(account_id, fields)
    return updated or {}


@router.delete("/accounts/{account_id}")
def delete_account(account_id: str):
    accounts.delete(account_id)
    return {"deleted": True}

@router.post("/accounts/{account_id}/resume", status_code=201)
async def upload_resume(account_id: str, resume: UploadFile = File(...)):
    account = _get_account_or_404(account_id)
    suffix = Path(resume.filename or "").suffix.lower()

    if not resume.filename:
        _bad_request("No file uploaded")

    ext = Path(resume.filename).suffix.lower()
    if ext != ".pdf":
        _bad_request("Only PDF resumes are supported right now.")

    filename = f'{int(datetime.utcnow().timestamp() * 1000)}-{os.getpid()}-{abs(hash(resume.filename or "")) % 1_000_000_000}{suffix}'
    destination = RESUME_UPLOAD_DIR / filename

    content = await resume.read()
    if not content:
        _bad_request("Uploaded file is empty")

    destination.write_bytes(content)

    updated = accounts.update(account_id, {"resume_path": str(destination)})

    resume_profile = account_resume_profiles.create_or_update_from_resume_path(account_id)

    return {
        "resume_path": str(destination),
        "filename": filename,
        "account": _safe_account(updated or account),
        "resume_profile": resume_profile,
    }

@router.post("/bot/start")
def start_many_bots(payload: dict[str, Any]):
    account_ids = payload.get("accountIds")
    if not account_ids:
        _bad_request("accountIds required")

    results = worker_manager.start_many(list(account_ids))
    return {"results": results, "running": worker_manager.get_running()}


@router.post("/bot/start/{account_id}")
def start_bot(account_id: str):
    try:
        worker_manager.start(account_id)
    except Exception as err:
        _bad_request(str(err))
    return {"started": True, "accountId": account_id}


@router.post("/bot/stop/{account_id}")
def stop_bot(account_id: str):
    stopped = worker_manager.stop(account_id)
    if stopped:
        accounts.update_status(account_id, "idle")
    return {"stopped": stopped}


@router.post("/bot/stop-all")
def stop_all_bots():
    running = worker_manager.get_running()
    worker_manager.stop_all()
    for account_id in running:
        try:
            accounts.update_status(account_id, "idle")
        except Exception:
            pass
    return {"stopped": True}


@router.get("/bot/running")
def get_running_bots():
    return {"running": worker_manager.get_running()}


@router.get("/accounts/{account_id}/search-configs")
def get_search_configs(account_id: str):
    return search_configs.get_by_account(account_id)


@router.post("/accounts/{account_id}/search-configs", status_code=201)
def create_search_config(account_id: str, payload: dict[str, Any]):
    data = dict(payload)
    data["account_id"] = account_id
    return search_configs.create(data)


@router.put("/search-configs/{config_id}")
def update_search_config(config_id: str, payload: dict[str, Any]):
    return search_configs.update(config_id, payload) or {}


@router.delete("/search-configs/{config_id}")
def delete_search_config(config_id: str):
    search_configs.delete(config_id)
    return {"deleted": True}

@router.get("/accounts/{account_id}/resume-profile")
def get_resume_profile(account_id: str):
    return account_resume_profiles.get_by_account(account_id)


@router.post("/accounts/{account_id}/resume-profile", status_code=201)
def create_resume_profile(account_id: str):
    return account_resume_profiles.create_or_update_from_resume_path(account_id)

@router.post("/application-questions/{question_id}/answer-from-resume")
def answer_application_question_from_resume(question_id: str):
    return application_questions.answer_from_resume(question_id)

@router.get("/accounts/{account_id}/applications")
def get_applications(
    account_id: str,
    status: str | None = Query(default=None),
    is_easy_apply: str | None = Query(default=None),
    limit: int | None = Query(default=None),
):
    filters: dict[str, Any] = {}
    if status:
        filters["status"] = status
    if is_easy_apply is not None:
        filters["is_easy_apply"] = is_easy_apply == "true"
    if limit:
        filters["limit"] = int(limit)
    return applications.get_by_account(account_id, filters)


@router.get("/accounts/{account_id}/applications/manual-review")
def get_manual_review(account_id: str):
    return applications.get_manual_review(account_id)


@router.get("/applications/{application_id}/questions")
def get_application_questions(application_id: str, includeAnswered: str = Query(default="true")):
    include_answered = includeAnswered != "false"
    return application_questions.get_by_application(application_id, include_answered)


@router.get("/applications/{application_id}/context")
def get_application_context(application_id: str):
    context = applications.get_context(application_id)
    if not context["application"]:
        raise HTTPException(status_code=404, detail="Application not found")
    return context


@router.get("/pending-questions")
def get_pending_questions(accountId: str | None = Query(default=None)):
    return application_questions.get_pending(accountId)


@router.get("/pending-applications")
def get_pending_applications(accountId: str | None = Query(default=None)):
    return applications.get_pending_applications(accountId)


@router.post("/pending-questions/{question_id}/answer")
def answer_pending_question(question_id: str, payload: dict[str, Any]):
    answer = str(payload.get("answer") or "").strip()
    priority = int(payload.get("priority") or 10)
    if not answer:
        _bad_request("answer required")

    question = application_questions.get_by_id(question_id)
    if not question:
        raise HTTPException(status_code=404, detail="Pending question not found")

    job_title_scope = question.get("job_title_scope") or question.get("search_job_title")
    impacted_ids = application_questions.answer_matching_scope(
        question["account_id"],
        question["question_text"],
        job_title_scope,
        answer,
    )

    template = qa_templates.upsert_scoped(
        {
            "account_id": question["account_id"],
            "question_pattern": question["question_text"],
            "answer": answer,
            "field_type": question.get("field_type") or "text",
            "priority": priority,
            "job_title_scope": job_title_scope,
        }
    )

    status_ids = []
    seen_status_ids: set[str] = set()
    for application_id in [question.get("application_id"), *impacted_ids]:
        if not application_id or application_id in seen_status_ids:
            continue
        seen_status_ids.add(application_id)
        status_ids.append(application_id)

    application_statuses = [
        {"applicationId": application_id, "status": applications.mark_ready_to_retry_if_complete(application_id)}
        for application_id in status_ids
    ]

    return {"saved": True, "template": template, "applicationStatuses": application_statuses}


@router.post("/applications/{application_id}/retry")
def retry_application(application_id: str):
    application = applications.get_by_id(application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    status = applications.mark_ready_to_retry_if_complete(application["id"])
    if status != "ready_to_retry":
        raise HTTPException(
            status_code=400,
            detail={"error": "This application still has unanswered required questions", "status": status},
        )

    worker_started = False
    worker_already_running = False

    if worker_manager.is_running(application["account_id"]):
        worker_already_running = True
    else:
        try:
            worker_manager.start(application["account_id"], {"prioritizedApplicationId": application["id"]})
        except Exception as err:
            _bad_request(str(err))
        worker_started = True

    return {
        "queued": True,
        "status": status,
        "applicationId": application["id"],
        "accountId": application["account_id"],
        "workerStarted": worker_started,
        "workerAlreadyRunning": worker_already_running,
        "prioritizedApplicationId": application["id"] if worker_started else None,
        "willRetryOnCurrentRun": worker_started,
        "willRetryOnNextRun": worker_already_running,
    }


@router.get("/qa-templates")
def get_qa_templates(accountId: str | None = Query(default=None)):
    if accountId:
        return qa_templates.get_for_account(accountId)
    return query_all("SELECT * FROM qa_templates ORDER BY priority DESC")


@router.post("/qa-templates", status_code=201)
def create_qa_template(payload: dict[str, Any]):
    return qa_templates.create(payload)


@router.put("/qa-templates/{template_id}")
def update_qa_template(template_id: str, payload: dict[str, Any]):
    return qa_templates.update(template_id, payload) or {}


@router.delete("/qa-templates/{template_id}")
def delete_qa_template(template_id: str):
    qa_templates.delete(template_id)
    return {"deleted": True}


@router.get("/logs")
def get_logs(accountId: str | None = Query(default=None), limit: int | None = Query(default=None)):
    row_limit = int(limit or (200 if accountId else 500))
    if accountId:
        return query_all(
            """
            SELECT bl.*, a.label, a.email
            FROM bot_logs bl
            LEFT JOIN accounts a ON a.id = bl.account_id
            WHERE bl.account_id = %s
            ORDER BY bl.created_at DESC
            LIMIT %s
            """,
            [accountId, row_limit],
        )
    return query_all(
        """
        SELECT bl.*, a.label, a.email
        FROM bot_logs bl
        LEFT JOIN accounts a ON a.id = bl.account_id
        ORDER BY bl.created_at DESC
        LIMIT %s
        """,
        [row_limit],
    )
