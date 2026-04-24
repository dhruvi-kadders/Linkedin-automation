from __future__ import annotations

import html
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any


try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover - optional dependency
    PdfReader = None


MONTH_ALIASES = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
MONTH_PATTERN = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
DATE_RANGE_RE = re.compile(
    rf"(?P<start>{MONTH_PATTERN}\s+\d{{4}})\s*[–-]\s*(?P<end>Present|{MONTH_PATTERN}\s+\d{{4}})$",
    re.IGNORECASE,
)
SECTION_HEADER_RE = re.compile(
    r"^(Professional Summary|Professional Experience|Technical Skills|Education|Certification|Certifications)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
BULLET_CHARS = "•●▪■◆►▶◦●*-"
DEGREE_KEYWORDS = (
    "bachelor",
    "master",
    "masters",
    "b.tech",
    "m.tech",
    "b.e",
    "m.e",
    "mba",
    "bsc",
    "msc",
    "phd",
    "doctorate",
    "diploma",
)
CERT_DATE_RE = (
    rf"(?:{MONTH_PATTERN}\s+\d{{1,2}},\s+\d{{4}}|{MONTH_PATTERN}\s+\d{{4}})"
)
CERTIFICATION_HEADER_RE = re.compile(
    rf"(?:^|(?<=\.\s)|(?<=\n))(?P<title>[A-Z][A-Za-z0-9&()™.,/'\-\s]+?)\s*:\s*Credential ID:\s*(?P<credential>[A-Z0-9-]+)\.?\s*"
    rf"(?P<issue_date>{CERT_DATE_RE})",
    re.IGNORECASE,
)
LINKEDIN_URI_RE = re.compile(r"/URI\s*\((.*?)\)", re.IGNORECASE | re.DOTALL)


class ResumeParseError(ValueError):
    """Raised when a resume file cannot be parsed safely."""


@dataclass(frozen=True)
class ParsedMonth:
    year: int
    month: int

    @property
    def iso(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def ordinal(self) -> int:
        return self.year * 12 + (self.month - 1)


class ResumeParserService:
    """Parses fixed-format resumes into structured JSON-friendly dictionaries."""

    def parse(self, filename: str, content: bytes) -> dict[str, Any]:
        extension = Path(filename or "").suffix.lower()
        if extension == ".pdf":
            raw_text = self._extract_text_from_pdf(content)
            linkedin_url = self._extract_linkedin_url_from_pdf(content)
        elif extension == ".docx":
            raw_text = self._extract_text_from_docx(content)
            linkedin_url = None
        elif extension in {".doc", ".txt"}:
            raw_text = self._decode_legacy_text(content)
            linkedin_url = None
        else:
            raise ResumeParseError(f"Unsupported resume format: {extension or 'unknown'}")

        normalized_text = self._normalize_document_text(raw_text)
        if not normalized_text:
            raise ResumeParseError("The uploaded resume did not contain readable text.")

        sections = self._split_sections(normalized_text)
        preamble = sections.pop("_preamble", "")

        parsed = self._parse_resume_sections(preamble, sections)
        if linkedin_url and not parsed.get("linkedin_url"):
            parsed["linkedin_url"] = linkedin_url

        parsed["raw_text"] = normalized_text
        return parsed

    def _extract_text_from_pdf(self, content: bytes) -> str:
        candidates: list[str] = []

        pdftotext_output = self._extract_text_with_pdftotext(content)
        if pdftotext_output:
            candidates.append(pdftotext_output)

        pypdf_output = self._extract_text_with_pypdf(content)
        if pypdf_output:
            candidates.append(pypdf_output)

        if not candidates:
            return self._decode_legacy_text(content)

        return max(candidates, key=self._text_quality_score)

    def _extract_text_with_pypdf(self, content: bytes) -> str:
        if PdfReader is None:
            return ""

        try:
            reader = PdfReader(BytesIO(content))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:
            return ""

    def _extract_text_with_pdftotext(self, content: bytes) -> str:
        executable = self._locate_pdftotext()
        if not executable:
            return ""

        temp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp_file:
                temp_file.write(content)
                temp_path = temp_file.name

            result = subprocess.run(
                [executable, "-simple", "-enc", "UTF-8", "-q", temp_path, "-"],
                capture_output=True,
                check=True,
            )
            return result.stdout.decode("utf-8", errors="ignore")
        except Exception:
            return ""
        finally:
            if temp_path:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except Exception:
                    pass

    def _extract_text_from_docx(self, content: bytes) -> str:
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                xml_bytes = archive.read("word/document.xml")
        except Exception as err:
            raise ResumeParseError(f"Could not read DOCX resume: {err}") from err

        xml_text = xml_bytes.decode("utf-8", errors="ignore")
        xml_text = re.sub(r"</w:p>", "\n", xml_text)
        xml_text = re.sub(r"</w:tr>", "\n", xml_text)
        xml_text = re.sub(r"<[^>]+>", "", xml_text)
        return html.unescape(xml_text)

    def _decode_legacy_text(self, content: bytes) -> str:
        decoded = content.decode("utf-8", errors="ignore")
        if decoded.strip():
            return decoded
        return content.decode("latin-1", errors="ignore").replace("\x00", "")

    def _extract_linkedin_url_from_pdf(self, content: bytes) -> str | None:
        urls: list[str] = []

        if PdfReader is not None:
            try:
                reader = PdfReader(BytesIO(content))
                for page in reader.pages:
                    annotations = page.get("/Annots") or []
                    for reference in annotations:
                        try:
                            annotation = reference.get_object()
                            action = annotation.get("/A") or {}
                            uri = action.get("/URI")
                            if uri:
                                urls.append(str(uri))
                        except Exception:
                            continue
            except Exception:
                pass

        decoded = content.decode("latin-1", errors="ignore")
        urls.extend(match.group(1) for match in LINKEDIN_URI_RE.finditer(decoded))

        for candidate in urls:
            normalized = self._normalize_linkedin_url(candidate)
            if normalized:
                return normalized
        return None

    def _locate_pdftotext(self) -> str | None:
        candidates = [
            shutil.which("pdftotext"),
            r"C:\Program Files\Git\mingw64\bin\pdftotext.exe",
            r"C:\Program Files\Git\usr\bin\pdftotext.exe",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return candidate
        return None

    def _text_quality_score(self, text: str) -> int:
        section_bonus = sum(
            400
            for header in (
                "Professional Summary",
                "Professional Experience",
                "Technical Skills",
                "Education",
                "Certification",
            )
            if header.lower() in text.lower()
        )
        return len(text) + section_bonus

    def _normalize_document_text(self, value: str) -> str:
        value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
        value = value.replace("\u00a0", " ").replace("\f", "\n")
        value = value.replace("ď‚·", "•").replace("â€¢", "•").replace("\uf0b7", "•")
        value = value.replace("â€“", "-").replace("â€”", "-").replace("–", "-").replace("—", "-")
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()

    def _split_sections(self, text: str) -> dict[str, str]:
        matches = list(SECTION_HEADER_RE.finditer(text))
        if not matches:
            return {"_preamble": text}

        sections: dict[str, str] = {"_preamble": text[: matches[0].start()].strip()}
        for index, match in enumerate(matches):
            header = self._normalize_section_name(match.group(1))
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            sections[header] = text[start:end].strip()
        return sections

    def _normalize_section_name(self, value: str) -> str:
        cleaned = self._collapse_whitespace(value).lower()
        if cleaned.startswith("professional summary"):
            return "professional_summary"
        if cleaned.startswith("professional experience"):
            return "professional_experience"
        if cleaned.startswith("technical skills"):
            return "technical_skills"
        if cleaned.startswith("education"):
            return "education"
        if cleaned.startswith("certification"):
            return "certifications"
        if cleaned.startswith("certifications"):
            return "certifications"
        return cleaned.replace(" ", "_")

    def _parse_resume_sections(self, preamble: str, sections: dict[str, str]) -> dict[str, Any]:
        preamble_lines = [self._collapse_whitespace(line) for line in preamble.splitlines() if self._collapse_whitespace(line)]
        contact = self._parse_contact_block(preamble_lines)
        summary = self._collapse_whitespace(sections.get("professional_summary", ""))
        skills_by_category = self._parse_skill_categories(sections.get("technical_skills", ""))
        skill_terms = self._flatten_skill_terms(skills_by_category)
        professional_experience = self._parse_professional_experience(
            sections.get("professional_experience", ""),
            skill_terms,
            skills_by_category,
        )
        education = self._parse_education(sections.get("education", ""))
        certifications = self._parse_certifications(sections.get("certifications", ""))
        total_experience_years = self._extract_total_experience_from_summary(summary)
        if total_experience_years is None:
            total_experience_years = self._calculate_total_experience_from_roles(professional_experience)

        return {
            "name": contact.get("full_name"),
            "phone_number": contact.get("phone"),
            "email": contact.get("email"),
            "linkedin_url": contact.get("linkedin_url"),
            "location": contact.get("location"),
            "summary": summary or None,
            "total_experience_years": total_experience_years,
            "professional_experience": professional_experience,
            "experience_by_skill": self._build_experience_by_skill(professional_experience, skills_by_category),
            "skills_by_category": skills_by_category,
            "education": education,
            "certifications": certifications,
        }

    def _parse_contact_block(self, lines: list[str]) -> dict[str, str | None]:
        full_name = lines[0] if lines else None
        headline = lines[1] if len(lines) > 1 else None
        contact_line = lines[2] if len(lines) > 2 else " | ".join(lines[2:])
        if len(lines) > 3:
            contact_line = " | ".join(lines[2:])

        email_match = re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", contact_line or "", re.IGNORECASE)
        phone_match = re.search(r"(\+?\d[\d\s().-]{8,}\d)", contact_line or "")

        location = None
        if contact_line:
            parts = [self._collapse_whitespace(part) for part in contact_line.split("|")]
            for part in parts:
                lowered = part.lower()
                if not lowered or "email" in lowered or "linkedin" in lowered:
                    continue
                if email_match and email_match.group(0).lower() in lowered:
                    continue
                if phone_match and phone_match.group(0).strip() in part:
                    continue
                location = part
                break

        return {
            "full_name": full_name,
            "headline": headline,
            "location": location,
            "phone": phone_match.group(0).strip() if phone_match else None,
            "email": email_match.group(0) if email_match else None,
            "linkedin_url": None,
        }

    def _parse_skill_categories(self, section_text: str) -> dict[str, list[str]]:
        categories: dict[str, list[str]] = {}
        for block in self._collect_bullet_blocks(section_text):
            if ":" not in block:
                continue
            title, values = block.split(":", 1)
            title = self._collapse_whitespace(title)
            value_items = self._split_skill_items(values)
            if title and value_items:
                categories[title] = value_items
        return categories

    def _flatten_skill_terms(self, skills_by_category: dict[str, list[str]]) -> list[str]:
        ordered_terms: list[str] = []
        seen: set[str] = set()

        for category, values in skills_by_category.items():
            for candidate in [category, *values]:
                cleaned = self._collapse_whitespace(candidate).strip(" .")
                key = cleaned.casefold()
                if not cleaned or key in seen:
                    continue
                seen.add(key)
                ordered_terms.append(cleaned)
        return ordered_terms

    def _parse_professional_experience(
        self,
        section_text: str,
        skill_terms: list[str],
        skills_by_category: dict[str, list[str]],
    ) -> list[dict[str, Any]]:
        roles: list[dict[str, Any]] = []
        current_role: dict[str, Any] | None = None

        for raw_line in section_text.splitlines():
            stripped = self._collapse_whitespace(raw_line)
            if not stripped:
                continue

            if self._looks_like_experience_header(stripped):
                if current_role:
                    roles.append(self._finalize_role(current_role, skills_by_category))
                current_role = self._parse_experience_header(stripped)
                continue

            if current_role is None:
                continue

            if self._is_bullet_line(raw_line):
                bullet_text = self._strip_bullet(stripped)
                if bullet_text:
                    current_role.setdefault("highlights", []).append(bullet_text)
                continue

            highlights = current_role.setdefault("highlights", [])
            if highlights:
                highlights[-1] = self._collapse_whitespace(f"{highlights[-1]} {stripped}")
            else:
                current_role.setdefault("notes", []).append(stripped)

        if current_role:
            roles.append(self._finalize_role(current_role, skills_by_category))

        for role in roles:
            matched_terms = self._match_skill_terms_in_text(
                " ".join(
                    [
                        role.get("job_title", ""),
                        role.get("company_name", ""),
                        role.get("company", ""),
                        role.get("location", ""),
                        *role.get("highlights", []),
                    ]
                ),
                skill_terms,
            )
            merged = {skill.casefold(): skill for skill in [*(role.get("skills_highlighted") or []), *matched_terms]}
            role["skills_highlighted"] = sorted(merged.values(), key=str.casefold)

        return roles

    def _finalize_role(self, role: dict[str, Any], skills_by_category: dict[str, list[str]]) -> dict[str, Any]:
        start_month = self._parse_month_label(role.get("start_label"))
        end_label = role.get("end_label")
        end_month = None if not end_label or end_label.lower() == "present" else self._parse_month_label(end_label)
        effective_end = end_month or ParsedMonth(date.today().year, date.today().month)
        duration_months = self._month_distance_inclusive(start_month, effective_end) if start_month else None

        normalized_role = {
            "job_title": role.get("job_title"),
            "company_name": role.get("company_name") or role.get("company"),
            "company": role.get("company"),
            "location": role.get("location"),
            "date_range": role.get("date_range"),
            "start_date": start_month.iso if start_month else None,
            "end_date": end_month.iso if end_month else None,
            "end_label": end_label,
            "is_current": bool(end_label and end_label.lower() == "present"),
            "duration_months": duration_months,
            "highlights": [self._collapse_whitespace(item) for item in role.get("highlights", []) if item],
            "notes": [self._collapse_whitespace(item) for item in role.get("notes", []) if item],
        }
        normalized_role["skills_highlighted"] = self._match_categories_for_role(normalized_role, skills_by_category)
        return normalized_role

    def _match_categories_for_role(self, role: dict[str, Any], skills_by_category: dict[str, list[str]]) -> list[str]:
        role_text = " ".join(
            [
                role.get("job_title", ""),
                role.get("company_name", ""),
                role.get("company", ""),
                role.get("location", ""),
                *role.get("highlights", []),
            ]
        )

        highlighted_terms = set(self._match_skill_terms_in_text(role_text, self._flatten_skill_terms(skills_by_category)))
        for category, values in skills_by_category.items():
            category_terms = list(values)
            if any(term in highlighted_terms for term in category_terms):
                highlighted_terms.add(category)

        return sorted(highlighted_terms, key=str.casefold)

    def _build_experience_by_skill(
        self,
        roles: list[dict[str, Any]],
        skills_by_category: dict[str, list[str]],
    ) -> dict[str, dict[str, Any]]:
        month_map: dict[str, set[int]] = {}
        display_name: dict[str, str] = {}
        category_children = {
            category: list(values)
            for category, values in skills_by_category.items()
        }

        for role in roles:
            start = self._parse_month_label(role.get("start_date"))
            end = self._parse_month_label(role.get("end_date")) if role.get("end_date") else None
            if not start:
                continue
            end_month = end or ParsedMonth(date.today().year, date.today().month)
            months = set(range(start.ordinal, end_month.ordinal + 1))
            role_skills = set(role.get("skills_highlighted") or [])

            for skill in role_skills:
                key = self._normalize_lookup_key(skill)
                month_map.setdefault(key, set()).update(months)
                display_name.setdefault(key, skill)

            for category, children in category_children.items():
                if role_skills.intersection(children):
                    key = self._normalize_lookup_key(category)
                    month_map.setdefault(key, set()).update(months)
                    display_name.setdefault(key, category)

        return {
            key: {
                "skill": display_name.get(key, key),
                "months": len(months),
                "years": round(len(months) / 12, 1),
            }
            for key, months in sorted(month_map.items())
        }

    def _parse_education(self, section_text: str) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        pending_ranges = [
            self._collapse_whitespace(line)
            for line in section_text.splitlines()
            if DATE_RANGE_RE.search(self._collapse_whitespace(line))
            and "|" not in self._collapse_whitespace(line)
        ]

        for raw_line in section_text.splitlines():
            line = self._collapse_whitespace(raw_line)
            if not line:
                continue
            if "|" not in line and not any(keyword in line.lower() for keyword in DEGREE_KEYWORDS):
                continue

            date_match = DATE_RANGE_RE.search(line)
            date_range = date_match.group(0) if date_match else (pending_ranges.pop(0) if pending_ranges else None)
            line_without_dates = self._collapse_whitespace(line[: date_match.start()]) if date_match else line
            line_without_dates = line_without_dates.strip(" .")

            if "|" in line_without_dates:
                degree_part, institution_part = [item.strip(" .") for item in line_without_dates.split("|", 1)]
            else:
                degree_part, institution_part = line_without_dates, ""

            degree, course_name = self._split_degree_and_course(degree_part)
            institution, location = self._split_institution_and_location(institution_part)
            start_label, end_label = self._split_date_range(date_range)
            start_month = self._parse_month_label(start_label) if start_label else None
            end_month = self._parse_month_label(end_label) if end_label and end_label.lower() != "present" else None

            entries.append(
                {
                    "degree": degree or degree_part or None,
                    "course_name": course_name,
                    "institution": institution or None,
                    "location": location,
                    "date_range": date_range,
                    "start_date": start_month.iso if start_month else None,
                    "end_date": end_month.iso if end_month else None,
                    "raw": line_without_dates,
                }
            )
        return entries

    def _parse_certifications(self, section_text: str) -> list[dict[str, Any]]:
        collapsed = self._collapse_whitespace(section_text)
        certifications: list[dict[str, Any]] = []

        matches = list(CERTIFICATION_HEADER_RE.finditer(collapsed))
        for index, match in enumerate(matches):
            title = self._collapse_whitespace(match.group("title")).strip(" .")
            issue_date = self._parse_friendly_date(match.group("issue_date"))
            body_start = match.end()
            body_end = matches[index + 1].start() if index + 1 < len(matches) else len(collapsed)
            body = self._collapse_whitespace(collapsed[body_start:body_end]).strip(" .")
            skills_text = body[len("Skills Learned:") :].strip() if body.startswith("Skills Learned:") else body
            skills = self._split_skill_items(skills_text)
            name, issuer = self._split_certification_title(title)

            certifications.append(
                {
                    "name": name or title,
                    "issuer": issuer,
                    "credential_id": self._collapse_whitespace(match.group("credential")),
                    "issue_date": issue_date,
                    "skills_learned": skills,
                    "raw": title,
                }
            )

        return certifications

    def _clean_work_experience_entry(
        self,
        entry: dict[str, Any],
        technical_skills: dict[str, list[str]],
    ) -> dict[str, Any]:
        category_names = {self._collapse_whitespace(name) for name in technical_skills}
        raw_skills = [self._collapse_whitespace(skill) for skill in list(entry.get("skills_highlighted") or [])]
        filtered_skills = [skill for skill in raw_skills if skill and skill not in category_names]
        if not filtered_skills:
            filtered_skills = raw_skills

        return {
            "job_title": entry.get("job_title"),
            "company_name": entry.get("company_name") or entry.get("company"),
            "location": entry.get("location"),
            "start_date": entry.get("start_date"),
            "end_date": entry.get("end_date"),
            "skills_involved": filtered_skills,
        }

    def _clean_education_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        cleaned = {
            "degree": entry.get("degree"),
            "institution": entry.get("institution"),
            "location": entry.get("location"),
            "start_date": entry.get("start_date"),
            "end_date": entry.get("end_date"),
        }
        if entry.get("course_name"):
            cleaned["course_name"] = entry.get("course_name")
        return cleaned

    def _clean_certificate_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        cleaned = {
            "name": entry.get("name"),
            "issuer": entry.get("issuer"),
            "issue_date": entry.get("issue_date"),
        }
        if entry.get("credential_id"):
            cleaned["credential_id"] = entry.get("credential_id")
        return cleaned

    def _looks_like_experience_header(self, line: str) -> bool:
        return bool(DATE_RANGE_RE.search(line))

    def _parse_experience_header(self, line: str) -> dict[str, Any]:
        match = DATE_RANGE_RE.search(line)
        if not match:
            raise ResumeParseError(f"Invalid experience header: {line}")

        prefix = line[: match.start()].strip(" ,.-")
        parts = [item.strip(" .") for item in prefix.split(",") if item.strip()]
        start_label = self._collapse_whitespace(match.group("start"))
        end_label = self._collapse_whitespace(match.group("end"))
        job_title = parts[0] if parts else prefix or None
        company_name = parts[1] if len(parts) > 1 else None
        location = ", ".join(parts[2:]) if len(parts) > 2 else None

        return {
            "job_title": job_title,
            "company_name": company_name,
            "company": company_name,
            "location": location,
            "date_range": self._collapse_whitespace(match.group(0)),
            "start_label": start_label,
            "end_label": end_label,
            "highlights": [],
        }

    def _collect_bullet_blocks(self, section_text: str) -> list[str]:
        blocks: list[str] = []
        current: list[str] = []

        for raw_line in section_text.splitlines():
            stripped = self._collapse_whitespace(raw_line)
            if not stripped:
                continue

            if self._is_bullet_line(raw_line):
                if current:
                    blocks.append(self._collapse_whitespace(" ".join(current)))
                current = [self._strip_bullet(stripped)]
                continue

            if current:
                current.append(stripped)
            else:
                current = [stripped]

        if current:
            blocks.append(self._collapse_whitespace(" ".join(current)))

        return [block for block in blocks if block]

    def _is_bullet_line(self, raw_line: str) -> bool:
        stripped = raw_line.lstrip()
        return bool(stripped and stripped[0] in BULLET_CHARS)

    def _strip_bullet(self, value: str) -> str:
        return value.lstrip(BULLET_CHARS).strip()

    def _split_skill_items(self, value: str) -> list[str]:
        items: list[str] = []
        token: list[str] = []
        depth = 0

        for char in value:
            if char == "(":
                depth += 1
            elif char == ")" and depth > 0:
                depth -= 1

            if char == "," and depth == 0:
                item = self._collapse_whitespace("".join(token)).strip(" .")
                if item:
                    items.append(item)
                token = []
                continue

            token.append(char)

        final_item = self._collapse_whitespace("".join(token)).strip(" .")
        if final_item:
            items.append(final_item)

        return items

    def _split_degree_and_course(self, degree_part: str) -> tuple[str | None, str | None]:
        cleaned = self._collapse_whitespace(degree_part).strip(" .")
        match = re.match(
            r"^(?P<degree>.+?)\s+(?:in|of|specializing in|specialisation in|specialization in)\s+(?P<course>.+)$",
            cleaned,
            re.IGNORECASE,
        )
        if match:
            return (
                self._collapse_whitespace(match.group("degree")).strip(" ."),
                self._collapse_whitespace(match.group("course")).strip(" ."),
            )
        return cleaned or None, None

    def _split_institution_and_location(self, value: str) -> tuple[str | None, str | None]:
        cleaned = self._collapse_whitespace(value).strip(" .")
        if not cleaned:
            return None, None

        parts = [part.strip() for part in cleaned.split(",")]
        if len(parts) >= 2:
            return ", ".join(parts[:-1]), parts[-1]
        return cleaned, None

    def _split_certification_title(self, title: str) -> tuple[str | None, str | None]:
        cleaned = self._collapse_whitespace(title).strip(" .")
        by_match = re.match(r"^(?P<name>.+?)\s+by\s+(?P<issuer>.+)$", cleaned, re.IGNORECASE)
        if by_match:
            return (
                self._collapse_whitespace(by_match.group("name")).strip(" ."),
                self._collapse_whitespace(by_match.group("issuer")).strip(" ."),
            )

        issuer_match = re.match(r"^(?P<name>.+?)\s*\((?P<issuer>[^()]+)\)$", cleaned)
        if issuer_match:
            return (
                self._collapse_whitespace(issuer_match.group("name")).strip(" ."),
                self._collapse_whitespace(issuer_match.group("issuer")).strip(" ."),
            )

        return cleaned or None, None

    def _extract_total_experience_from_summary(self, summary: str) -> float | None:
        if not summary:
            return None

        match = re.search(r"(\d+(?:\.\d+)?)\+?\s+years?\s+of\s+experience", summary, re.IGNORECASE)
        if match:
            return float(match.group(1))
        return None

    def _calculate_total_experience_from_roles(self, roles: list[dict[str, Any]]) -> float | None:
        all_months: set[int] = set()
        for role in roles:
            start = self._parse_month_label(role.get("start_date"))
            end = self._parse_month_label(role.get("end_date")) if role.get("end_date") else None
            if not start:
                continue
            end_month = end or ParsedMonth(date.today().year, date.today().month)
            all_months.update(range(start.ordinal, end_month.ordinal + 1))

        if not all_months:
            return None
        return round(len(all_months) / 12, 1)

    def _month_distance_inclusive(self, start: ParsedMonth | None, end: ParsedMonth | None) -> int | None:
        if not start or not end:
            return None
        return max(0, end.ordinal - start.ordinal + 1)

    def _split_date_range(self, value: str | None) -> tuple[str | None, str | None]:
        if not value:
            return None, None
        match = DATE_RANGE_RE.search(self._collapse_whitespace(value))
        if not match:
            return None, None
        return self._collapse_whitespace(match.group("start")), self._collapse_whitespace(match.group("end"))

    def _parse_month_label(self, value: str | None) -> ParsedMonth | None:
        if not value:
            return None

        cleaned = self._collapse_whitespace(value)
        iso_match = re.fullmatch(r"(\d{4})-(\d{2})", cleaned)
        if iso_match:
            return ParsedMonth(int(iso_match.group(1)), int(iso_match.group(2)))

        match = re.fullmatch(rf"({MONTH_PATTERN})\s+(\d{{4}})", cleaned, re.IGNORECASE)
        if not match:
            return None

        month_name = match.group(1).casefold()
        month = MONTH_ALIASES.get(month_name)
        year = int(match.group(2))
        if not month:
            return None
        return ParsedMonth(year, month)

    def _parse_friendly_date(self, value: str | None) -> str | None:
        if not value:
            return None

        cleaned = self._collapse_whitespace(value)
        match = re.fullmatch(rf"({MONTH_PATTERN})\s+(\d{{1,2}}),\s*(\d{{4}})", cleaned, re.IGNORECASE)
        if match:
            month = MONTH_ALIASES.get(match.group(1).casefold())
            if month:
                return f"{int(match.group(3)):04d}-{month:02d}-{int(match.group(2)):02d}"

        month_match = re.fullmatch(rf"({MONTH_PATTERN})\s+(\d{{4}})", cleaned, re.IGNORECASE)
        if month_match:
            month = MONTH_ALIASES.get(month_match.group(1).casefold())
            if month:
                return f"{int(month_match.group(2)):04d}-{month:02d}-01"
        return None

    def _normalize_linkedin_url(self, value: str) -> str | None:
        candidate = value.strip().strip("<>()[]{}\"'")
        candidate = candidate.replace("\\(", "(").replace("\\)", ")")
        candidate = candidate.replace("file:///", "")
        candidate = candidate.replace("file://", "")

        linkedin_match = re.search(
            r"(https?:/+)?(?:www\.)?linkedin\.com/[A-Za-z0-9\-_/%.?=&]+",
            candidate,
            re.IGNORECASE,
        )
        if not linkedin_match:
            return None

        url = linkedin_match.group(0)
        if url.startswith("https:/") and not url.startswith("https://"):
            url = url.replace("https:/", "https://", 1)
        elif url.startswith("http:/") and not url.startswith("http://"):
            url = url.replace("http:/", "http://", 1)
        elif not url.lower().startswith(("http://", "https://")):
            url = f"https://{url.lstrip('/')}"

        url = url.replace("https://linkedin.com", "https://www.linkedin.com")
        url = url.replace("http://linkedin.com", "https://www.linkedin.com")
        url = re.sub(r"[)\].,]+$", "", url)
        return url

    def _match_skill_terms_in_text(self, text: str, skill_terms: list[str]) -> list[str]:
        normalized_text = self._normalize_lookup_key(text)
        matched: list[str] = []
        seen: set[str] = set()

        for term in sorted(skill_terms, key=len, reverse=True):
            for alias in self._skill_aliases(term):
                if len(alias) < 3:
                    continue
                if alias in normalized_text:
                    key = term.casefold()
                    if key not in seen:
                        seen.add(key)
                        matched.append(term)
                    break
        return matched

    def _skill_aliases(self, value: str) -> set[str]:
        aliases = {self._normalize_lookup_key(value)}
        stripped = self._normalize_lookup_key(re.sub(r"\([^)]*\)", "", value))
        if stripped:
            aliases.add(stripped)

        for inner_group in re.findall(r"\(([^)]*)\)", value):
            for item in inner_group.split(","):
                alias = self._normalize_lookup_key(item)
                if alias:
                    aliases.add(alias)

        return {alias for alias in aliases if alias}

    def _normalize_lookup_key(self, value: str | None) -> str:
        text = self._collapse_whitespace(value or "").casefold()
        text = text.replace("&", " and ")
        text = re.sub(r"[^a-z0-9+#./ ]+", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _collapse_whitespace(self, value: str | None) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()
