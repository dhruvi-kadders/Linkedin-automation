from __future__ import annotations

import re
import unicodedata


LINKEDIN_BASE_URL = "https://www.linkedin.com"


def normalize_comparable_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("&", " and ").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def strip_decorators(value: str | None) -> str:
    text = normalize_comparable_text(value)
    text = re.sub(r"\b(remote|hybrid|on site|onsite)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokenize(value: str | None) -> list[str]:
    return [token for token in strip_decorators(value).split(" ") if token]


def is_loose_text_match(left: str | None, right: str | None, min_overlap: float = 0.6) -> bool:
    left_text = strip_decorators(left)
    right_text = strip_decorators(right)

    if not left_text or not right_text:
        return False
    if left_text == right_text:
        return True
    if left_text in right_text or right_text in left_text:
        return True

    left_tokens = list(dict.fromkeys(tokenize(left_text)))
    right_tokens = list(dict.fromkeys(tokenize(right_text)))
    if not left_tokens or not right_tokens:
        return False

    right_set = set(right_tokens)
    overlap = sum(1 for token in left_tokens if token in right_set)
    score = overlap / min(len(left_tokens), len(right_tokens))
    return score >= min_overlap


def extract_job_id_from_url(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None

    path_match = re.search(r"/jobs/view/(\d+)", text, re.I)
    if path_match:
        return path_match.group(1)

    query_match = re.search(r"[?&#](?:currentJobId|jobId)=(\d+)", text, re.I)
    if query_match:
        return query_match.group(1)

    raw_match = re.fullmatch(r"\d+", text)
    if raw_match:
        return raw_match.group(0)

    return None


def build_linkedin_job_url(job_id: str | None, fallback_url: str = "") -> str:
    return f"{LINKEDIN_BASE_URL}/jobs/view/{job_id}/" if job_id else str(fallback_url or "")


def compare_job_identity(expected: dict | None = None, actual: dict | None = None) -> dict[str, object]:
    expected = expected or {}
    actual = actual or {}

    expected_id = extract_job_id_from_url(expected.get("url")) or extract_job_id_from_url(expected.get("jobId"))
    actual_id = extract_job_id_from_url(actual.get("url")) or extract_job_id_from_url(actual.get("jobId"))

    title_match = (
        is_loose_text_match(expected.get("title"), actual.get("title"), 0.6)
        if expected.get("title") and actual.get("title")
        else None
    )
    company_match = (
        is_loose_text_match(expected.get("company"), actual.get("company"), 0.75)
        if expected.get("company") and actual.get("company")
        else None
    )

    if expected_id and actual_id:
        matches = expected_id == actual_id
    else:
        compared = [value for value in [title_match, company_match] if value is not None]
        matches = bool(compared) and all(compared) and any(value is True for value in compared)

    return {
        "matches": matches,
        "expectedId": expected_id,
        "actualId": actual_id,
        "titleMatch": title_match,
        "companyMatch": company_match,
    }

