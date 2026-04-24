from __future__ import annotations

import re
from typing import Any, Sequence

from ..utils.text_utils import normalize_skill


YES_VALUES = {"yes", "y", "true", "1"}
NO_VALUES = {"no", "n", "false", "0"}
PHONE_QUESTION_RE = re.compile(r"\b(phone|mobile|telephone|contact\s*number)\b", re.I)
EMAIL_QUESTION_RE = re.compile(r"\bemail\b", re.I)
LINKEDIN_QUESTION_RE = re.compile(r"\blinked\s*in\b|\blinkedin\b|\bprofile\s+url\b", re.I)

FIRST_NAME_QUESTION_RE = re.compile(r"\bfirst\s*name\b", re.I)
LAST_NAME_QUESTION_RE = re.compile(r"\blast\s*name\b", re.I)
FULL_NAME_QUESTION_RE = re.compile(r"\bfull\s*name\b|\byour\s*name\b", re.I)

TOTAL_EXPERIENCE_RE = re.compile(
    r"\b(how\s+many\s+years|years?\s+of\s+(?:work\s+)?experience|total\s+experience|overall\s+experience)\b",
    re.I,
)
SKILL_EXPERIENCE_RE = re.compile(
    r"\b(?:how\s+many\s+years|years?\s+of\s+(?:work\s+)?experience|experience)\b.*?\b(?:with|in|using|on)\b\s+(?P<skill>[^?.,;:()/]+)",
    re.I,
)

CURRENT_COMPANY_RE = re.compile(r"\b(current|present)\s+(company|employer|organization)\b", re.I)
CURRENT_TITLE_RE = re.compile(r"\b(current|present)\s+(job\s+title|title|designation|role|position)\b", re.I)

HIGHEST_QUALIFICATION_RE = re.compile(
    r"\b(highest|highest\s+level\s+of)\s+(qualification|education|degree)\b",
    re.I,
)
EDUCATION_RE = re.compile(r"\b(education|degree|qualification|university|college)\b", re.I)
CERTIFICATION_RE = re.compile(r"\b(certification|certificate|license|credential)\b", re.I)
SUMMARY_RE = re.compile(r"\b(summary|about\s+(?:you|yourself)|professional\s+summary|profile)\b", re.I)

DEGREE_RANKS = (
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


def _resume_payload(resume_profile: Any) -> dict[str, Any]:
    if not isinstance(resume_profile, dict):
        return {}
    parsed = resume_profile.get("parsed_profile")
    return parsed if isinstance(parsed, dict) else resume_profile


def _format_number_answer(value: Any) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        text = collapse_whitespace(value)
        return text or None
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _finalize_resume_answer(value: Any, options: Sequence[str] | None) -> str | None:
    text = collapse_whitespace(value)
    if not text:
        return None
    snapped = snap_to_option(text, options)
    return snapped or text


def _split_full_name(value: Any) -> tuple[str, str]:
    parts = [part for part in collapse_whitespace(value).split() if part]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _pick_current_experience(parsed: dict[str, Any]) -> dict[str, Any] | None:
    experiences = parsed.get("experiences") or []
    if not experiences:
        return None

    for exp in experiences:
        end_date = normalize_text(exp.get("end_date"))
        if end_date in {"present", "current", "now"}:
            return exp

    return experiences[0]


def _education_rank(entry: dict[str, Any]) -> tuple[int, int]:
    degree = normalize_text(entry.get("degree"))
    score = 0
    for key, rank in DEGREE_RANKS:
        if key in degree:
            score = rank
            break
    return score, len(degree)


def _pick_highest_education(parsed: dict[str, Any]) -> dict[str, Any] | None:
    education = parsed.get("education") or []
    if not education:
        return None
    return sorted(education, key=_education_rank, reverse=True)[0]


def _extract_skill_from_question(question_text: str, parsed: dict[str, Any]) -> str | None:
    candidates: set[str] = set()

    for skill in parsed.get("skills") or []:
        normalized = normalize_skill(skill)
        if normalized:
            candidates.add(normalized)

    for skill in (parsed.get("skill_experience") or {}).keys():
        normalized = normalize_skill(skill)
        if normalized:
            candidates.add(normalized)

    normalized_question = normalize_text(question_text)

    for skill in sorted(candidates, key=len, reverse=True):
        escaped = re.escape(skill).replace(r"\ ", r"\s+")
        if re.search(rf"(?<![a-z0-9+#./-]){escaped}(?![a-z0-9+#./-])", normalized_question):
            return skill

    match = SKILL_EXPERIENCE_RE.search(question_text)
    if not match:
        return None

    raw_skill = match.group("skill")
    raw_skill = re.split(r"\b(experience|skills?|knowledge|proficiency)\b", raw_skill, maxsplit=1, flags=re.I)[0]
    normalized = normalize_skill(raw_skill)
    return normalized or None


def _find_skill_experience_years(parsed: dict[str, Any], skill_name: str) -> float | None:
    skill_experience = parsed.get("skill_experience") or {}

    for key, value in skill_experience.items():
        if normalize_skill(key) != skill_name:
            continue

        if isinstance(value, dict):
            years = value.get("years")
        else:
            years = value

        try:
            return float(years)
        except (TypeError, ValueError):
            return None

    return None


def resolve_resume_profile_answer(
    question_text: str,
    field_type: str,
    options: Sequence[str] | None,
    resume_profile: dict[str, Any] | None,
) -> str | None:
    parsed = _resume_payload(resume_profile)
    if not parsed:
        return None

    question = collapse_whitespace(question_text)
    first_name, last_name = _split_full_name(parsed.get("full_name"))
    current_experience = _pick_current_experience(parsed)
    highest_education = _pick_highest_education(parsed)

    if PHONE_QUESTION_RE.search(question):
        return _finalize_resume_answer(parsed.get("phone"), options)

    if EMAIL_QUESTION_RE.search(question):
        return _finalize_resume_answer(parsed.get("email"), options)

    if LINKEDIN_QUESTION_RE.search(question):
        return _finalize_resume_answer(parsed.get("linkedin_url"), options)

    if FIRST_NAME_QUESTION_RE.search(question):
        return _finalize_resume_answer(first_name, options)

    if LAST_NAME_QUESTION_RE.search(question):
        return _finalize_resume_answer(last_name, options)

    if FULL_NAME_QUESTION_RE.search(question):
        return _finalize_resume_answer(parsed.get("full_name"), options)

    skill_name = _extract_skill_from_question(question, parsed)
    if skill_name:
        skill_years = _find_skill_experience_years(parsed, skill_name)
        if skill_years is not None:
            return _finalize_resume_answer(_format_number_answer(skill_years), options)

    if TOTAL_EXPERIENCE_RE.search(question):
        return _finalize_resume_answer(_format_number_answer(parsed.get("total_experience_years")), options)

    if CURRENT_COMPANY_RE.search(question) and current_experience:
        return _finalize_resume_answer(current_experience.get("company"), options)

    if CURRENT_TITLE_RE.search(question) and current_experience:
        return _finalize_resume_answer(current_experience.get("title"), options)

    if HIGHEST_QUALIFICATION_RE.search(question) and highest_education:
        return _finalize_resume_answer(highest_education.get("degree"), options)

    if EDUCATION_RE.search(question) and highest_education:
        degree = collapse_whitespace(highest_education.get("degree"))
        institution = collapse_whitespace(highest_education.get("institution"))
        return _finalize_resume_answer(" - ".join(part for part in [degree, institution] if part), options)

    if CERTIFICATION_RE.search(question):
        certificate_names = [
            collapse_whitespace(item.get("name"))
            for item in (parsed.get("certificates") or [])
            if collapse_whitespace(item.get("name"))
        ]
        if certificate_names:
            return _finalize_resume_answer(", ".join(certificate_names[:3]), options)

    if SUMMARY_RE.search(question):
        return _finalize_resume_answer(parsed.get("summary"), options)

    return None

def normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9+#./ ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def collapse_whitespace(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def role_matches(job_role: str | None, scope: str | None) -> bool:
    left = normalize_text(job_role)
    right = normalize_text(scope)
    if not right:
        return True
    if not left:
        return False
    return left == right or right in left or left in right


def find_template_answer(label: str, qa_templates: list[dict[str, Any]], job_role: str | None) -> dict[str, Any] | None:
    lower = normalize_text(label)
    candidates = []
    for template in qa_templates or []:
        if not normalize_text(template.get("answer")):
            continue
        if normalize_text(template.get("question_pattern")) not in lower:
            continue
        if template.get("job_title_scope") and not role_matches(job_role, template.get("job_title_scope")):
            continue

        candidate = dict(template)
        candidate["score"] = (
            int(template.get("priority") or 0)
            + (100 if template.get("account_id") else 0)
            + (1000 if template.get("job_title_scope") else 0)
        )
        candidates.append(candidate)

    candidates.sort(key=lambda item: item["score"], reverse=True)
    return candidates[0] if candidates else None


def map_field_type_for_engine(field_type: Any) -> str:
    field = normalize_text(field_type)
    if field in {"select", "combobox"}:
        return "select"
    if field == "radio":
        return "radio"
    if field == "checkbox":
        return "checkbox"
    return "text"


def snap_to_option(answer: str, options: Sequence[str] | None) -> str | None:
    options = [str(option).strip() for option in (options or []) if str(option).strip()]
    if not answer:
        return None
    if not options:
        return answer

    normalized_answer = normalize_text(answer)
    exact = next((option for option in options if normalize_text(option) == normalized_answer), None)
    if exact:
        return exact

    if normalized_answer in YES_VALUES:
        for option in options:
            if normalize_text(option) in YES_VALUES:
                return option
    if normalized_answer in NO_VALUES:
        for option in options:
            if normalize_text(option) in NO_VALUES:
                return option

    answer_number = _extract_number(answer)
    if answer_number is not None:
        numeric_options = []
        for option in options:
            option_number = _extract_number(option)
            if option_number is not None:
                numeric_options.append((abs(option_number - answer_number), option))
        if numeric_options:
            numeric_options.sort(key=lambda item: item[0])
            return numeric_options[0][1]

    best_score = 0.0
    best_option = None
    answer_tokens = set(normalize_text(answer).split())
    for option in options:
        option_tokens = set(normalize_text(option).split())
        if not option_tokens:
            continue
        overlap = len(answer_tokens & option_tokens)
        union = len(answer_tokens | option_tokens)
        score = overlap / union if union else 0.0
        if normalized_answer in normalize_text(option) or normalize_text(option) in normalized_answer:
            score += 0.35
        if score > best_score:
            best_score = score
            best_option = option

    return best_option if best_score >= 0.2 else None


def resolve_application_answer(
    question_text: str,
    field_type: str,
    options: Sequence[str] | None,
    qa_templates: list[dict[str, Any]],
    job_role: str | None,
    answer_engine: Any | None,
    resume_profile: dict[str, Any] | None = None,
    logger: Any | None = None,
) -> dict[str, Any] | None:
    resume_answer = resolve_resume_profile_answer(question_text, field_type, options, resume_profile)

    # Let parsed resume win for skill-specific experience questions like:
    # "How many years of work experience do you have with ServiceNow?"
    if SKILL_EXPERIENCE_RE.search(question_text) and resume_answer:
        return {"answer": resume_answer, "source": "resume"}

    template = find_template_answer(question_text, qa_templates, job_role)
    if template:
        snapped = snap_to_option(str(template.get("answer") or "").strip(), options)
        return {"answer": snapped or str(template.get("answer") or "").strip(), "source": "template"}

    if resume_answer:
        return {"answer": resume_answer, "source": "resume"}

    if not answer_engine:
        return None

    try:
        if hasattr(answer_engine, "resolve_with_source"):
            answer, source = answer_engine.resolve_with_source(
                question_text,
                map_field_type_for_engine(field_type),
                list(options or []),
            )
        else:
            answer = answer_engine.resolve(
                question_text,
                map_field_type_for_engine(field_type),
                list(options or []),
            )
            source = "engine" if answer else None
    except Exception as err:
        if logger:
            logger.warn(f'Answer engine failed for "{question_text}": {err}')
        return None

    normalized_answer = collapse_whitespace(answer)
    if not normalized_answer:
        return None

    snapped = snap_to_option(normalized_answer, options)
    return {"answer": snapped or normalized_answer, "source": source or "engine"}


def _extract_number(value: str) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None
