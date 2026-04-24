from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager
from typing import Any

from psycopg2 import pool
from psycopg2.extras import Json, RealDictCursor
from app.services.resume_parser import ResumeParser

from .config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER, SCHEMA_PATH


MAX_CONNECTIONS = 20
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_pool: pool.ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()
_initialized = False
_init_lock = threading.Lock()


def _sanitize_identifier(value: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"Unsafe SQL identifier: {value}")
    return value


def _connection_pool() -> pool.ThreadedConnectionPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = pool.ThreadedConnectionPool(
                1,
                MAX_CONNECTIONS,
                host=DB_HOST,
                port=DB_PORT,
                dbname=DB_NAME,
                user=DB_USER,
                password=DB_PASSWORD,
            )
        return _pool


@contextmanager
def _get_connection():
    db_pool = _connection_pool()
    conn = db_pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        db_pool.putconn(conn)


def initialize_database() -> None:
    global _initialized
    if _initialized:
        return

    with _init_lock:
        if _initialized:
            return

        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(schema_sql)
                cur.execute(
                    """
                    ALTER TABLE qa_templates
                    ADD COLUMN IF NOT EXISTS job_title_scope VARCHAR(255)
                    """
                )
                cur.execute(
                    """
                    UPDATE application_questions
                    SET answer = NULL,
                        is_answered = FALSE,
                        updated_at = NOW()
                    WHERE COALESCE(is_required, FALSE) = TRUE
                      AND COALESCE(is_answered, FALSE) = TRUE
                      AND LOWER(COALESCE(field_type, '')) IN ('select', 'combobox')
                      AND (
                        REGEXP_REPLACE(LOWER(BTRIM(COALESCE(answer, ''))), '\s+', ' ', 'g') IN (
                          'select',
                          'select an option',
                          'select one',
                          'please select',
                          'please make a selection',
                          'make a selection',
                          'choose an option',
                          'choose one'
                        )
                        OR REGEXP_REPLACE(LOWER(BTRIM(COALESCE(answer, ''))), '\s+', ' ', 'g')
                          = REGEXP_REPLACE(LOWER(BTRIM(question_text)), '\s+', ' ', 'g')
                      )
                    """
                )
                cur.execute(
                    """
                    DELETE FROM application_questions aq
                    USING (
                      SELECT id
                      FROM (
                        SELECT
                          id,
                          ROW_NUMBER() OVER (
                            PARTITION BY application_id, question_text
                            ORDER BY
                              CASE WHEN COALESCE(BTRIM(answer), '') != '' THEN 1 ELSE 0 END DESC,
                              is_answered DESC,
                              updated_at DESC,
                              created_at DESC,
                              step_index DESC,
                              id DESC
                          ) AS rn
                        FROM application_questions
                      ) ranked
                      WHERE rn > 1
                    ) dupes
                    WHERE aq.id = dupes.id
                    """
                )
                cur.execute("DROP INDEX IF EXISTS idx_application_questions_unique")
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_application_questions_unique
                    ON application_questions(application_id, question_text)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_application_questions_account
                    ON application_questions(account_id, created_at DESC)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_application_questions_application
                    ON application_questions(application_id)
                    """
                )
                cur.execute(
                    """
                    CREATE OR REPLACE VIEW application_stats AS
                    SELECT
                      a.id AS account_id,
                      a.label,
                      a.email,
                      a.status AS account_status,
                      COUNT(ja.id) FILTER (WHERE ja.status = 'applied') AS total_applied,
                      COUNT(ja.id) FILTER (WHERE ja.status = 'pending') AS total_pending,
                      COUNT(ja.id) FILTER (WHERE ja.status = 'failed') AS total_failed,
                      COUNT(ja.id) FILTER (WHERE ja.is_easy_apply = FALSE AND ja.status != 'applied') AS manual_review_count,
                      MAX(ja.applied_at) AS last_applied_at,
                      COUNT(ja.id) FILTER (WHERE ja.status = 'pending_questions') AS pending_questions_count
                    FROM accounts a
                    LEFT JOIN job_applications ja ON ja.account_id = a.id
                    GROUP BY a.id, a.label, a.email, a.status
                    """
                )

        _initialized = True


def query_all(sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
    initialize_database()
    with _get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or [])
            return [dict(row) for row in cur.fetchall()]


def query_one(sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> dict[str, Any] | None:
    rows = query_all(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> None:
    initialize_database()
    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or [])


def _build_update_sql(
    table: str,
    fields: dict[str, Any],
    where_field: str = "id",
    touch_updated_at: bool = False,
) -> tuple[str, list[Any]]:
    if not fields:
        raise ValueError("At least one field is required")

    safe_table = _sanitize_identifier(table)
    safe_where = _sanitize_identifier(where_field)
    assignments: list[str] = []
    values: list[Any] = []

    for key, value in fields.items():
        safe_key = _sanitize_identifier(key)
        assignments.append(f"{safe_key} = %s")
        values.append(value)

    if touch_updated_at:
        assignments.append("updated_at = NOW()")

    sql = f"UPDATE {safe_table} SET {', '.join(assignments)} WHERE {safe_where} = %s RETURNING *"
    return sql, values


class AccountsRepository:
    def get_all(self) -> list[dict[str, Any]]:
        return query_all("SELECT * FROM accounts ORDER BY created_at")

    def get_by_id(self, account_id: str) -> dict[str, Any] | None:
        return query_one("SELECT * FROM accounts WHERE id = %s", [account_id])

    def create(
        self,
        label: str | None,
        email: str,
        password: str,
        resume_path: str | None,
    ) -> dict[str, Any]:
        return query_one(
            """
            INSERT INTO accounts (label, email, password, resume_path)
            VALUES (%s, %s, %s, %s)
            RETURNING *
            """,
            [label, email, password, resume_path],
        ) or {}

    def update(self, account_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        if not fields:
            return self.get_by_id(account_id)
        sql, values = _build_update_sql("accounts", fields, touch_updated_at=True)
        return query_one(sql, [*values, account_id])

    def update_status(self, account_id: str, status: str) -> None:
        execute(
            """
            UPDATE accounts
            SET status = %s, updated_at = NOW()
            WHERE id = %s
            """,
            [status, account_id],
        )

    def save_session(self, account_id: str, session_data: dict[str, Any]) -> None:
        execute(
            """
            UPDATE accounts
            SET session_data = %s, updated_at = NOW()
            WHERE id = %s
            """,
            [Json(session_data), account_id],
        )

    def get_session(self, account_id: str) -> dict[str, Any] | None:
        row = query_one("SELECT session_data FROM accounts WHERE id = %s", [account_id])
        return row.get("session_data") if row else None

    def delete(self, account_id: str) -> None:
        execute("DELETE FROM accounts WHERE id = %s", [account_id])

    def get_stats(self) -> list[dict[str, Any]]:
        return query_all("SELECT * FROM application_stats ORDER BY label")


class SearchConfigsRepository:
    def get_by_account(self, account_id: str) -> list[dict[str, Any]]:
        return query_all(
            """
            SELECT * FROM search_configs
            WHERE account_id = %s AND active = TRUE
            """,
            [account_id],
        )

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        return query_one(
            """
            INSERT INTO search_configs
              (account_id, job_title, location, remote_only, easy_apply_only,
               max_applications, date_posted, experience_level, job_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            [
                data["account_id"],
                data["job_title"],
                data.get("location"),
                data.get("remote_only", False),
                data.get("easy_apply_only", True),
                data.get("max_applications", 50),
                data.get("date_posted", "past_week"),
                data.get("experience_level", []),
                data.get("job_type", []),
            ],
        ) or {}

    def update(self, config_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        if not fields:
            return query_one("SELECT * FROM search_configs WHERE id = %s", [config_id])
        sql, values = _build_update_sql("search_configs", fields)
        return query_one(sql, [*values, config_id])

    def delete(self, config_id: str) -> None:
        execute("DELETE FROM search_configs WHERE id = %s", [config_id])


class ApplicationsRepository:
    def get_by_id(self, application_id: str) -> dict[str, Any] | None:
        return query_one("SELECT * FROM job_applications WHERE id = %s", [application_id])

    def get_context(self, application_id: str) -> dict[str, Any]:
        application = query_one(
            """
            SELECT
              ja.*,
              a.label AS account_label,
              a.email AS account_email,
              sc.job_title AS search_job_title
            FROM job_applications ja
            LEFT JOIN accounts a ON a.id = ja.account_id
            LEFT JOIN search_configs sc ON sc.id = ja.search_config_id
            WHERE ja.id = %s
            """,
            [application_id],
        )

        if not application:
            return {"application": None, "relatedApplications": []}

        related = query_all(
            """
            SELECT
              id,
              account_id,
              search_config_id,
              job_url,
              job_title,
              company_name,
              location,
              is_easy_apply,
              status,
              error_message,
              applied_at,
              created_at
            FROM job_applications
            WHERE account_id = %s
              AND id != %s
              AND LOWER(COALESCE(job_title, '')) = LOWER(COALESCE(%s, ''))
              AND LOWER(COALESCE(company_name, '')) = LOWER(COALESCE(%s, ''))
            ORDER BY created_at DESC
            LIMIT 10
            """,
            [application["account_id"], application["id"], application.get("job_title", ""), application.get("company_name", "")],
        )

        return {"application": application, "relatedApplications": related}

    def find_by_url(self, account_id: str, job_url: str) -> dict[str, Any] | None:
        return query_one(
            """
            SELECT *
            FROM job_applications
            WHERE account_id = %s AND job_url = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            [account_id, job_url],
        )

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        existing = self.find_by_url(data["account_id"], data["job_url"])
        if existing:
            return existing

        return query_one(
            """
            INSERT INTO job_applications
              (account_id, search_config_id, job_url, job_title, company_name,
               location, is_easy_apply, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            [
                data["account_id"],
                data.get("search_config_id"),
                data["job_url"],
                data.get("job_title"),
                data.get("company_name"),
                data.get("location"),
                data.get("is_easy_apply", False),
                data.get("status", "pending"),
            ],
        ) or {}

    def update_status(self, application_id: str, status: str, error_message: str | None = None) -> None:
        execute(
            """
            UPDATE job_applications
            SET status = %s::varchar,
                error_message = %s,
                applied_at = CASE WHEN %s::text = 'applied' THEN NOW() ELSE applied_at END
            WHERE id = %s
            """,
            [status, error_message, status, application_id],
        )

    def get_by_account(self, account_id: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        filters = filters or {}
        sql = "SELECT * FROM job_applications WHERE account_id = %s"
        params: list[Any] = [account_id]

        if filters.get("status"):
            sql += " AND status = %s"
            params.append(filters["status"])

        if "is_easy_apply" in filters and filters["is_easy_apply"] is not None:
            sql += " AND is_easy_apply = %s"
            params.append(filters["is_easy_apply"])

        sql += " ORDER BY created_at DESC LIMIT %s"
        params.append(int(filters.get("limit") or 100))
        return query_all(sql, params)

    def get_manual_review(self, account_id: str) -> list[dict[str, Any]]:
        return query_all(
            """
            SELECT *
            FROM job_applications
            WHERE account_id = %s AND is_easy_apply = FALSE
            ORDER BY created_at DESC
            """,
            [account_id],
        )

    def get_pending_applications(self, account_id: str | None = None) -> list[dict[str, Any]]:
        return query_all(
            """
            SELECT
              ja.*,
              a.label AS account_label,
              a.email AS account_email,
              sc.job_title AS search_job_title,
              COUNT(aq.id) FILTER (
                WHERE aq.is_required = TRUE
                  AND COALESCE(aq.is_answered, FALSE) = FALSE
              ) AS missing_required_count,
              COUNT(aq.id) FILTER (
                WHERE COALESCE(aq.is_answered, FALSE) = TRUE
              ) AS answered_count
            FROM job_applications ja
            LEFT JOIN application_questions aq ON aq.application_id = ja.id
            LEFT JOIN accounts a ON a.id = ja.account_id
            LEFT JOIN search_configs sc ON sc.id = ja.search_config_id
            WHERE ja.status IN ('pending_questions', 'ready_to_retry')
              AND (%s::uuid IS NULL OR ja.account_id = %s)
            GROUP BY ja.id, a.label, a.email, sc.job_title
            ORDER BY ja.created_at DESC
            """,
            [account_id, account_id],
        )

    def exists_by_url(self, account_id: str, job_url: str) -> bool:
        row = query_one(
            """
            SELECT id
            FROM job_applications
            WHERE account_id = %s AND job_url = %s
            LIMIT 1
            """,
            [account_id, job_url],
        )
        return row is not None

    def get_retry_queue(
        self,
        account_id: str,
        search_config_id: str | None = None,
        prioritized_application_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return query_all(
            """
            SELECT
              ja.*,
              COALESCE(sc.job_title, ja.job_title) AS job_role
            FROM job_applications ja
            LEFT JOIN search_configs sc ON sc.id = ja.search_config_id
            WHERE ja.account_id = %s
              AND (%s::uuid IS NULL OR ja.search_config_id = %s)
              AND ja.status IN ('ready_to_retry', 'pending_questions')
              AND EXISTS (
                SELECT 1
                FROM application_questions aq
                WHERE aq.application_id = ja.id
              )
              AND NOT EXISTS (
                SELECT 1
                FROM application_questions aq
                WHERE aq.application_id = ja.id
                  AND aq.is_required = TRUE
                  AND COALESCE(aq.is_answered, FALSE) = FALSE
              )
            ORDER BY
              CASE
                WHEN %s::uuid IS NOT NULL AND ja.id = %s THEN 0
                ELSE 1
              END,
              ja.created_at ASC
            """,
            [account_id, search_config_id, search_config_id, prioritized_application_id, prioritized_application_id],
        )

    def mark_ready_to_retry_if_complete(self, application_id: str) -> str:
        row = query_one(
            """
            SELECT COUNT(*)::int AS missing_count
            FROM application_questions
            WHERE application_id = %s
              AND is_required = TRUE
              AND COALESCE(is_answered, FALSE) = FALSE
            """,
            [application_id],
        ) or {"missing_count": 0}

        status = "pending_questions" if int(row.get("missing_count") or 0) > 0 else "ready_to_retry"
        execute(
            """
            UPDATE job_applications
            SET status = %s::varchar
            WHERE id = %s
            """,
            [status, application_id],
        )
        return status


class QATemplatesRepository:
    def get_for_account(self, account_id: str) -> list[dict[str, Any]]:
        return query_all(
            """
            SELECT *
            FROM qa_templates
            WHERE (account_id = %s OR account_id IS NULL)
              AND answer IS NOT NULL
              AND BTRIM(answer) != ''
            ORDER BY
              CASE WHEN job_title_scope IS NULL THEN 1 ELSE 0 END,
              account_id NULLS LAST,
              priority DESC
            """,
            [account_id],
        )

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        return query_one(
            """
            INSERT INTO qa_templates
              (account_id, question_pattern, answer, field_type, priority, job_title_scope)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            [
                data.get("account_id"),
                data["question_pattern"],
                data["answer"],
                data.get("field_type", "text"),
                data.get("priority", 0),
                data.get("job_title_scope"),
            ],
        ) or {}

    def update(self, template_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        if not fields:
            return query_one("SELECT * FROM qa_templates WHERE id = %s", [template_id])
        sql, values = _build_update_sql("qa_templates", fields)
        return query_one(sql, [*values, template_id])

    def upsert_scoped(self, data: dict[str, Any]) -> dict[str, Any]:
        existing = query_one(
            """
            SELECT *
            FROM qa_templates
            WHERE account_id IS NOT DISTINCT FROM %s
              AND LOWER(question_pattern) = LOWER(%s)
              AND COALESCE(LOWER(job_title_scope), '') = COALESCE(LOWER(%s), '')
            ORDER BY priority DESC
            LIMIT 1
            """,
            [data.get("account_id"), data["question_pattern"], data.get("job_title_scope")],
        )

        if existing:
            return query_one(
                """
                UPDATE qa_templates
                SET answer = %s,
                    field_type = %s,
                    priority = %s,
                    job_title_scope = %s
                WHERE id = %s
                RETURNING *
                """,
                [
                    data["answer"],
                    data.get("field_type", "text"),
                    data.get("priority", 10),
                    data.get("job_title_scope"),
                    existing["id"],
                ],
            ) or {}

        return self.create(data)

    def delete(self, template_id: str) -> None:
        execute("DELETE FROM qa_templates WHERE id = %s", [template_id])


class ApplicationQuestionsRepository:
    def upsert_many(self, application_id: str, meta: dict[str, Any], questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        for question in questions:
            question_text = str(question.get("question_text") or "").strip()
            if not question_text:
                continue

            row = query_one(
                """
                INSERT INTO application_questions
                  (application_id, account_id, search_config_id, question_text, field_type,
                   options, answer, is_required, is_answered, step_index,
                   job_title_scope, job_title, company_name, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (application_id, question_text)
                DO UPDATE SET
                  account_id = EXCLUDED.account_id,
                  search_config_id = EXCLUDED.search_config_id,
                  field_type = EXCLUDED.field_type,
                  options = EXCLUDED.options,
                  answer = CASE
                    WHEN COALESCE(BTRIM(EXCLUDED.answer), '') != '' THEN EXCLUDED.answer
                    ELSE application_questions.answer
                  END,
                  is_required = EXCLUDED.is_required,
                  is_answered = CASE
                    WHEN EXCLUDED.is_answered = TRUE THEN TRUE
                    ELSE application_questions.is_answered
                  END,
                  step_index = GREATEST(application_questions.step_index, EXCLUDED.step_index),
                  job_title_scope = EXCLUDED.job_title_scope,
                  job_title = EXCLUDED.job_title,
                  company_name = EXCLUDED.company_name,
                  updated_at = NOW()
                RETURNING *
                """,
                [
                    application_id,
                    meta["account_id"],
                    meta.get("search_config_id"),
                    question_text,
                    question.get("field_type", "text"),
                    Json(question["options"]) if question.get("options") is not None else None,
                    question.get("answer"),
                    bool(question.get("is_required")),
                    bool(question.get("is_answered")),
                    question.get("step_index") or meta.get("step_index") or 1,
                    meta.get("job_title_scope"),
                    meta.get("job_title"),
                    meta.get("company_name"),
                ],
            )
            if row:
                rows.append(row)

        return rows

    def get_by_id(self, question_id: str) -> dict[str, Any] | None:
        return query_one(
            """
            SELECT
              aq.*,
              ja.job_url,
              ja.status AS application_status,
              sc.job_title AS search_job_title
            FROM application_questions aq
            JOIN job_applications ja ON ja.id = aq.application_id
            LEFT JOIN search_configs sc ON sc.id = aq.search_config_id
            WHERE aq.id = %s
            """,
            [question_id],
        )

    def get_by_application(self, application_id: str, include_answered: bool = True) -> list[dict[str, Any]]:
        return query_all(
            """
            SELECT *
            FROM application_questions
            WHERE application_id = %s
              AND (%s::boolean = TRUE OR COALESCE(is_answered, FALSE) = FALSE)
            ORDER BY step_index ASC, updated_at ASC, created_at ASC
            """,
            [application_id, include_answered],
        )

    def get_pending(self, account_id: str | None = None) -> list[dict[str, Any]]:
        return query_all(
            """
            SELECT
              aq.*,
              ja.job_url,
              ja.status AS application_status,
              sc.job_title AS search_job_title
            FROM application_questions aq
            JOIN job_applications ja ON ja.id = aq.application_id
            LEFT JOIN search_configs sc ON sc.id = aq.search_config_id
            WHERE aq.is_required = TRUE
              AND COALESCE(aq.is_answered, FALSE) = FALSE
              AND (%s::uuid IS NULL OR aq.account_id = %s)
            ORDER BY aq.updated_at DESC, aq.step_index DESC, aq.created_at DESC
            """,
            [account_id, account_id],
        )

    def answer(self, question_id: str, answer: str) -> dict[str, Any] | None:
        return query_one(
            """
            UPDATE application_questions
            SET answer = %s,
                is_answered = CASE WHEN COALESCE(BTRIM(%s), '') != '' THEN TRUE ELSE FALSE END,
                updated_at = NOW()
            WHERE id = %s
            RETURNING *
            """,
            [answer, answer, question_id],
        )

    def answer_matching_scope(self, account_id: str, question_text: str, job_title_scope: str | None, answer: str) -> list[str]:
        impacted = query_all(
            """
            SELECT DISTINCT application_id
            FROM application_questions
            WHERE account_id = %s
              AND LOWER(question_text) = LOWER(%s)
              AND COALESCE(LOWER(job_title_scope), '') = COALESCE(LOWER(%s), '')
              AND is_required = TRUE
              AND COALESCE(is_answered, FALSE) = FALSE
            """,
            [account_id, question_text, job_title_scope],
        )

        execute(
            """
            UPDATE application_questions
            SET answer = %s,
                is_answered = CASE WHEN COALESCE(BTRIM(%s), '') != '' THEN TRUE ELSE FALSE END,
                updated_at = NOW()
            WHERE account_id = %s
              AND LOWER(question_text) = LOWER(%s)
              AND COALESCE(LOWER(job_title_scope), '') = COALESCE(LOWER(%s), '')
              AND is_required = TRUE
            """,
            [answer, answer, account_id, question_text, job_title_scope],
        )

        return [row["application_id"] for row in impacted]


class LogsRepository:
    def insert(self, account_id: str | None, level: str, message: str, metadata: dict[str, Any] | None = None) -> None:
        execute(
            """
            INSERT INTO bot_logs (account_id, level, message, metadata)
            VALUES (%s, %s, %s, %s)
            """,
            [account_id, level, message, Json(metadata) if metadata is not None else None],
        )

    def get_by_account(self, account_id: str, limit: int = 200) -> list[dict[str, Any]]:
        return query_all(
            """
            SELECT *
            FROM bot_logs
            WHERE account_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            [account_id, limit],
        )

    def get_recent(self, limit: int = 500) -> list[dict[str, Any]]:
        return query_all(
            """
            SELECT bl.*, a.label, a.email
            FROM bot_logs bl
            LEFT JOIN accounts a ON a.id = bl.account_id
            ORDER BY bl.created_at DESC
            LIMIT %s
            """,
            [limit],
        )

_DEGREE_RANKS = (
    ("phd", 7),
    ("doctorate", 7),
    ("doctor", 7),
    ("master", 6),
    ("mba", 6),
    ("m.tech", 6),
    ("mtech", 6),
    ("ms", 6),
    ("bachelor", 5),
    ("b.tech", 5),
    ("btech", 5),
    ("b.e", 5),
    ("be", 5),
    ("bs", 5),
    ("associate", 4),
    ("diploma", 3),
)


def _collapse_resume_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_resume_text(value: Any) -> str:
    return _collapse_resume_text(value).lower()


def _format_resume_number(value: Any) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        text = _collapse_resume_text(value)
        return text or None
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _pick_resume_current_experience(profile: dict[str, Any]) -> dict[str, Any] | None:
    experiences = profile.get("experiences") or []
    if not experiences:
        return None

    for exp in experiences:
        if _normalize_resume_text(exp.get("end_date")) in {"present", "current", "now"}:
            return exp

    return experiences[0]


def _resume_degree_rank(entry: dict[str, Any]) -> tuple[int, int]:
    degree = _normalize_resume_text(entry.get("degree"))
    score = 0
    for keyword, rank in _DEGREE_RANKS:
        if keyword in degree:
            score = rank
            break
    return score, len(degree)


def _pick_resume_highest_education(profile: dict[str, Any]) -> dict[str, Any] | None:
    education = profile.get("education") or []
    if not education:
        return None
    return sorted(education, key=_resume_degree_rank, reverse=True)[0]


def build_resume_seed_templates(account_id: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    parsed = dict(profile or {})
    if isinstance(parsed.get("parsed_profile"), dict):
        parsed = dict(parsed["parsed_profile"])

    templates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(
        question_pattern: str,
        answer: Any,
        field_type: str = "text",
        priority: int = 10,
        job_title_scope: str | None = None,
    ) -> None:
        answer_text = _collapse_resume_text(answer)
        pattern_text = _collapse_resume_text(question_pattern)
        if not pattern_text or not answer_text:
            return

        key = (pattern_text.lower(), _normalize_resume_text(job_title_scope))
        if key in seen:
            return
        seen.add(key)

        templates.append(
            {
                "account_id": account_id,
                "question_pattern": pattern_text,
                "answer": answer_text,
                "field_type": field_type,
                "priority": priority,
                "job_title_scope": job_title_scope,
            }
        )

    add("phone", parsed.get("phone"), field_type="text", priority=12)
    add("years of experience", _format_resume_number(parsed.get("total_experience_years")), field_type="number", priority=10)
    add("email", parsed.get("email"), field_type="text", priority=10)
    add("linkedin", parsed.get("linkedin_url"), field_type="text", priority=10)
    add("linkedin profile", parsed.get("linkedin_url"), field_type="text", priority=10)
    add("full name", parsed.get("full_name"), field_type="text", priority=9)

    current_experience = _pick_resume_current_experience(parsed)
    if current_experience:
        add("current company", current_experience.get("company"), field_type="text", priority=9)
        add("current employer", current_experience.get("company"), field_type="text", priority=9)
        add("current job title", current_experience.get("title"), field_type="text", priority=9)
        add("current role", current_experience.get("title"), field_type="text", priority=9)

    highest_education = _pick_resume_highest_education(parsed)
    if highest_education:
        add("highest qualification", highest_education.get("degree"), field_type="text", priority=8)
        add("highest degree", highest_education.get("degree"), field_type="text", priority=8)

    certificate_names = [
        _collapse_resume_text(item.get("name"))
        for item in (parsed.get("certificates") or [])
        if _collapse_resume_text(item.get("name"))
    ]
    if certificate_names:
        add("certification", ", ".join(certificate_names[:3]), field_type="text", priority=6)

    return templates

class AccountResumeProfilesService:
    def get_by_account(self, account_id: str):
        query = """
        SELECT
            id,
            account_id,
            full_name,
            phone,
            linkedin_url,
            summary,
            raw_text,
            parsed_profile,
            total_experience_years,
            created_at,
            updated_at
        FROM account_resume_profiles
        WHERE account_id = %s
        LIMIT 1
        """
        return query_one(query, (account_id,))

    def create_or_update_from_resume_path(self, account_id: str):
        account_query = """
        SELECT id, email, resume_path
        FROM accounts
        WHERE id = %s
        LIMIT 1
        """
        account = query_one(account_query, (account_id,))
        if not account:
            raise ValueError("Account not found.")

        resume_path = account.get("resume_path")
        if not resume_path:
            raise ValueError("resume_path is empty for this account.")

        parser = ResumeParser()
        profile = parser.parse_pdf(resume_path)

        if not profile.get("email"):
            profile["email"] = account.get("email")

        query = """
        INSERT INTO account_resume_profiles (
            account_id,
            full_name,
            phone,
            linkedin_url,
            summary,
            raw_text,
            parsed_profile,
            total_experience_years,
            created_at,
            updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, NOW(), NOW())
        ON CONFLICT (account_id)
        DO UPDATE SET
            full_name = EXCLUDED.full_name,
            phone = EXCLUDED.phone,
            linkedin_url = EXCLUDED.linkedin_url,
            summary = EXCLUDED.summary,
            raw_text = EXCLUDED.raw_text,
            parsed_profile = EXCLUDED.parsed_profile,
            total_experience_years = EXCLUDED.total_experience_years,
            updated_at = NOW()
        RETURNING
            id,
            account_id,
            full_name,
            phone,
            linkedin_url,
            summary,
            raw_text,
            parsed_profile,
            total_experience_years,
            created_at,
            updated_at
        """

        profile_row = query_one(
            query,
            (
                account_id,
                profile.get("full_name"),
                profile.get("phone"),
                profile.get("linkedin_url"),
                profile.get("summary"),
                profile.get("raw_text"),
                json.dumps(profile),
                profile.get("total_experience_years", 0),
            ),
        )

        for template in build_resume_seed_templates(account_id, profile_row or profile):
            qa_templates.upsert_scoped(template)

        return profile_row

accounts = AccountsRepository()
search_configs = SearchConfigsRepository()
applications = ApplicationsRepository()
qa_templates = QATemplatesRepository()
application_questions = ApplicationQuestionsRepository()
logs = LogsRepository()
account_resume_profiles = AccountResumeProfilesService()
