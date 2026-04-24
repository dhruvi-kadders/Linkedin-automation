from __future__ import annotations

import os
import types
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..db import query_all


WORKSPACE_DIR = Path(__file__).resolve().parents[4]
ENGINE_FILE = WORKSPACE_DIR / "automation" / "automation" / "answer_engine" / "answer_engine.py"
MODEL_FILE = WORKSPACE_DIR / "automation" / "automation" / "answer_engine" / "answer_model1.pkl"
DISABLE_VALUES = {"0", "false", "no", "off"}


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def _as_bool(value: Any, default: bool = False) -> bool:
    text = _normalize(value)
    if not text:
        return default
    if text in {"yes", "true", "1", "y"}:
        return True
    if text in {"no", "false", "0", "n"}:
        return False
    return default


def _looks_like_person_name(value: str) -> bool:
    parts = [part for part in value.split() if part]
    return len(parts) >= 2 and all(part.replace("-", "").isalpha() for part in parts[:2])


def _split_name(value: str) -> tuple[str, str]:
    parts = [part for part in value.split() if part]
    if len(parts) < 2:
        return "", ""
    return parts[0], " ".join(parts[1:])


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _build_template_lookup(qa_templates: list[dict[str, Any]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for template in qa_templates or []:
        pattern = _normalize(template.get("question_pattern"))
        answer = str(template.get("answer") or "").strip()
        if pattern and answer and pattern not in lookup:
            lookup[pattern] = answer
    return lookup


@lru_cache(maxsize=1)
def _load_answer_engine_class():
    if not ENGINE_FILE.is_file():
        raise FileNotFoundError(f"Answer engine file not found: {ENGINE_FILE}")

    source = ENGINE_FILE.read_text(encoding="utf-8", errors="ignore")
    marker = '\nif __name__ == "__main__":'
    if marker in source:
        source = source.split(marker, 1)[0]

    module = types.ModuleType("external_answer_engine")
    module.__file__ = str(ENGINE_FILE)
    exec(compile(source, str(ENGINE_FILE), "exec"), module.__dict__)

    answer_engine_class = getattr(module, "AnswerEngine", None)
    if answer_engine_class is None:
        raise ImportError("AnswerEngine class was not found in the external module")
    return answer_engine_class


def build_candidate_profile(account: dict[str, Any], qa_templates: list[dict[str, Any]]) -> dict[str, Any]:
    lookup = _build_template_lookup(qa_templates)
    label = str(account.get("label") or "").strip()
    first_name, last_name = _split_name(label) if _looks_like_person_name(label) else ("", "")

    return {
        "personal": {
            "first_name": first_name,
            "last_name": last_name,
            "email": _first_non_empty(account.get("email")),
            "phone": lookup.get("phone", ""),
            "city": lookup.get("city", ""),
            "gender": lookup.get("gender", ""),
        },
        "preferences": {
            "salary_expectation": lookup.get("expected salary", ""),
            "years_experience": lookup.get("years of experience", ""),
            "contract": lookup.get("contract", "Yes"),
            "remote": _as_bool(lookup.get("work remotely"), True),
            "onsite": _as_bool(lookup.get("on-site"), False),
            "hybrid": _as_bool(lookup.get("hybrid"), False),
            "exclude_companies": "",
        },
        "legal": {
            "clearance": lookup.get("clearance", ""),
            "sponsorship": lookup.get("require sponsorship", "No"),
            "authorization": lookup.get("legally authorized", "Yes"),
            "citizen": lookup.get("citizen", ""),
            "background_check": lookup.get("background check", ""),
            "drug_test": lookup.get("drug test", ""),
        },
        "optional": {
            "race_ethnicity": lookup.get("race", ""),
            "veteran_status": lookup.get("veteran", ""),
            "disability": lookup.get("disability", ""),
        },
        "documents": {
            "resume_path": _first_non_empty(account.get("resume_path")),
        },
    }


def load_historical_training_pairs(account_id: str) -> list[tuple[str, str]]:
    rows = query_all(
        """
        SELECT DISTINCT ON (LOWER(question_text))
          question_text,
          answer
        FROM application_questions
        WHERE account_id = %s
          AND COALESCE(BTRIM(answer), '') <> ''
        ORDER BY LOWER(question_text), updated_at DESC, created_at DESC
        """,
        [account_id],
    )

    return [
        (str(row.get("question_text") or "").strip(), str(row.get("answer") or "").strip())
        for row in rows
        if str(row.get("question_text") or "").strip() and str(row.get("answer") or "").strip()
    ]


def build_answer_engine(
    account: dict[str, Any],
    qa_templates: list[dict[str, Any]],
    logger: Any | None = None,
    confidence_threshold: float = 0.70,
) -> Any | None:
    training_pairs = load_historical_training_pairs(account["id"])
    engine_disabled = _normalize(os.getenv("ENABLE_ANSWER_ENGINE", "true")) in DISABLE_VALUES

    if engine_disabled:
        if logger:
            logger.info("Answer engine disabled via ENABLE_ANSWER_ENGINE")
        return None

    try:
        AnswerEngine = _load_answer_engine_class()
        profile = build_candidate_profile(account, qa_templates)
        model_path = str(MODEL_FILE) if MODEL_FILE.is_file() else ""

        engine = AnswerEngine(
            candidate_profile=profile,
            resume_text=" ",
            model_path=model_path,
            confidence_threshold=confidence_threshold,
        )

        if training_pairs:
            engine.bulk_learn(training_pairs)

        if logger:
            logger.info(
                "Answer engine ready"
                + f" | seed_model={'yes' if model_path else 'no'}"
                + f" | learned_pairs={len(training_pairs)}"
                + f" | threshold={confidence_threshold}"
            )
        return engine
    except Exception as err:
        if logger:
            logger.warn(f"Answer engine unavailable: {err}")
        return None
