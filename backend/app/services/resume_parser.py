import fitz
import re

from app.services.pdf_text_extractor import PDFTextExtractor
from app.utils.date_utils import parse_date_safe, month_diff
from app.utils.text_utils import dedupe_keep_order, normalize_skill


KNOWN_SKILLS = [
    "python", "java", "javascript", "typescript", "sql", "postgresql",
    "mysql", "mongodb", "fastapi", "django", "flask", "node.js", "express",
    "playwright", "selenium", "scrapy", "html", "css", "react", "git",
    "docker", "aws", "azure", "rest api", "api", "machine learning",
    "nlp", "pandas", "numpy", "linux", "pytest", "redis"
]


class ResumeParser:
    SECTION_HEADERS = {
        "summary": ["professional summary", "summary", "profile"],
        "skills": ["skills", "technical skills", "core skills", "competencies"],
        "education": ["education", "academic qualification", "academics", "qualification"],
        "certificates": ["certificates", "certification", "certifications", "licenses"],
        "experience": ["experience", "work experience", "employment history", "professional experience"],
    }

    def parse_pdf(self, pdf_path: str) -> dict:
        raw_text = PDFTextExtractor.extract_text(pdf_path)

        profile = {
            "full_name": self.extract_name(raw_text),
            "email": self.extract_email(raw_text),
            "phone": self.extract_phone(raw_text),
            "linkedin_url": self.extract_linkedin(pdf_path, raw_text),
            "summary": self.extract_section(raw_text, "summary"),
            "skills": [],
            "education": [],
            "certificates": [],
            "experiences": [],
            "total_experience_years": 0.0,
            "skill_experience": {},
            "raw_text": raw_text,
        }

        profile["skills"] = self.extract_skills(raw_text)
        profile["education"] = self.extract_education(raw_text)
        profile["certificates"] = self.extract_certificates(raw_text)
        profile["experiences"] = self.extract_experience(raw_text)
        profile["total_experience_years"] = self.calculate_total_experience(profile["experiences"])
        profile["skill_experience"] = self.calculate_skill_experience(
            profile["experiences"], profile["skills"]
        )

        return profile

    def extract_name(self, text: str):
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return None

        first = lines[0]
        if "@" not in first and len(first.split()) <= 5 and not any(ch.isdigit() for ch in first):
            return first.title()
        return None

    def extract_email(self, text: str):
        match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
        return match.group(0) if match else None

    def extract_phone(self, text: str):
        match = re.search(r'(\+?\d[\d\-\s()]{8,}\d)', text)
        return match.group(1).strip() if match else None

    def extract_linkedin(self, pdf_path: str, text: str = ""):
        def clean_linkedin(value: str | None):
            if not value:
                return None

            value = str(value).strip()

            # normalize broken exported forms
            value = value.replace("\\", "/")
            value = value.replace("https:/www.", "https://www.")
            value = value.replace("http:/www.", "http://www.")
            value = value.replace("https:/linkedin.com", "https://linkedin.com")
            value = value.replace("http:/linkedin.com", "http://linkedin.com")

            # handle embedded local-path wrappers like C:/.../-https:/www.linkedin.com/in/xyz/
            m = re.search(r'(https?:/+)?(www\.)?linkedin\.com/[A-Za-z0-9\-_/%.?=&]+', value, re.I)
            if m:
                url = m.group(0)

                if not url.lower().startswith("http"):
                    if url.lower().startswith("www."):
                        url = "https://" + url
                    else:
                        url = "https://www." + url

                url = url.replace("https:/www.", "https://www.")
                url = url.replace("http:/www.", "http://www.")
                url = url.replace("https:/linkedin.com", "https://linkedin.com")
                url = url.replace("http:/linkedin.com", "http://linkedin.com")

                return url.rstrip("/")

            return None

        try:
            with fitz.open(pdf_path) as doc:
                for page in doc:
                    # 1) normal link dictionaries
                    for link in page.get_links():
                        for key in ("uri", "file", "page", "to"):
                            value = link.get(key)
                            parsed = clean_linkedin(value)
                            if parsed:
                                return parsed

                    # 2) raw annotations
                    annot = page.first_annot
                    while annot:
                        info = annot.info or {}
                        for value in info.values():
                            parsed = clean_linkedin(value)
                            if parsed:
                                return parsed

                        try:
                            annot_obj = annot.get_text("text")
                            parsed = clean_linkedin(annot_obj)
                            if parsed:
                                return parsed
                        except Exception:
                            pass

                        annot = annot.next

            # 3) fallback to visible text
            parsed = clean_linkedin(text)
            if parsed:
                return parsed

        except Exception:
            pass

        return None

    def extract_section(self, text: str, section_name: str):
        headers = self.SECTION_HEADERS[section_name]

        all_headers = []
        for values in self.SECTION_HEADERS.values():
            all_headers.extend(values)

        start_pattern = r"(?:^|\n)\s*(?:%s)\s*\n" % "|".join(map(re.escape, headers))
        end_pattern = r"\n\s*(?:%s)\s*\n" % "|".join(map(re.escape, all_headers))

        start_match = re.search(start_pattern, text, re.I)
        if not start_match:
            return None

        remaining = text[start_match.end():]
        end_match = re.search(end_pattern, remaining, re.I)

        if end_match:
            return remaining[:end_match.start()].strip()

        return remaining.strip()

    def extract_skills(self, text: str):
        section = self.extract_section(text, "skills") or ""
        if not section:
            return []

        parts = re.split(r'[,|\n•]+', section)
        skills = []

        for part in parts:
            value = part.strip(" -:\t")
            if 1 < len(value) < 40:
                skills.append(value)

        return dedupe_keep_order(skills)

    def extract_education(self, text: str):
        section = self.extract_section(text, "education") or ""
        if not section:
            return []

        items = []
        lines = [line.strip() for line in section.splitlines() if line.strip()]

        date_pattern = (
            r'(?P<start>(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})'
            r'\s*[-–]\s*'
            r'(?P<end>(?:Present|Current|Now|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}))'
        )

        for line in lines:
            start_date = None
            end_date = None

            # 🔥 Skip certification-like lines
            if any(keyword in line.lower() for keyword in ["certification", "certificate", "certified"]):
                continue

            date_match = re.search(date_pattern, line, re.I)
            if date_match:
                start_date = date_match.group("start").strip()
                end_date = date_match.group("end").strip()
                line = line[:date_match.start()].strip()

            degree = None
            institution = None

            if "|" in line:
                degree_part, institution_part = line.split("|", 1)
                degree = degree_part.strip(" .")
                institution = institution_part.strip(" .")
            else:
                degree = line.strip(" .")

            items.append(
                {
                    "degree": degree,
                    "institution": institution,
                    "start_date": start_date,
                    "end_date": end_date,
                    "description": None,
                }
            )

        return items

    def extract_certificates(self, text: str):
        section = self.extract_section(text, "certificates") or ""
        if not section:
            return []

        lines = [line.strip() for line in section.splitlines() if line.strip()]
        items = []

        date_pattern = (
            r'(?P<date>'
            r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}'
            r'|'
            r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}'
            r')'
        )

        current = None
        i = 0

        while i < len(lines):
            line = lines[i]
            lower_line = line.lower()

            # Handle "Skills Learned:" line
            if lower_line.startswith("skills learned:"):
                if current:
                    skills_value = line[len("Skills Learned:"):].strip()

                    # If PDF wrapped badly, keep consuming lines until next certificate-looking line
                    j = i + 1
                    extra_parts = []

                    while j < len(lines):
                        next_line = lines[j].strip()
                        next_lower = next_line.lower()

                        # stop if next line looks like another skills block
                        if next_lower.startswith("skills learned:"):
                            break

                        # stop if next line looks like a new certificate entry
                        if "credential id:" in next_lower:
                            break

                        # otherwise this is probably wrapped continuation text
                        extra_parts.append(next_line)
                        j += 1

                    if extra_parts:
                        skills_value = f"{skills_value} {' '.join(extra_parts)}".strip()

                    current["skills_learned"] = skills_value
                    i = j
                    continue

            # New certificate line
            if "credential id:" in lower_line:
                if current:
                    items.append(current)

                issue_date = None
                date_match = re.search(date_pattern, line, re.I)
                if date_match:
                    issue_date = date_match.group("date").strip()
                    line_wo_date = line[:date_match.start()].strip(" .")
                else:
                    line_wo_date = line.strip(" .")

                # remove credential id part
                name_part = re.split(r'Credential ID\s*:\s*', line_wo_date, flags=re.I)[0].strip(" .:")

                name = name_part
                issuer = None

                # Extract issuer from parentheses
                issuer_match = re.search(r'^(.*?)\s*\(([^()]+)\)\s*$', name_part)
                if issuer_match:
                    name = issuer_match.group(1).strip(" .:")
                    issuer = issuer_match.group(2).strip()

                current = {
                    "name": name,
                    "issuer": issuer,
                    "issue_date": issue_date,
                    "skills_learned": None,
                }

            i += 1

        if current:
            items.append(current)

        return items

    def extract_experience(self, text: str):
        section = self.extract_section(text, "experience") or ""
        if not section:
            return []

        lines = [line.strip() for line in section.splitlines() if line.strip()]

        items = []
        current = None

        date_pattern = (
            r'(?P<start>(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})'
            r'\s*[-–]\s*'
            r'(?P<end>(?:Present|Current|Now|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}))'
        )

        for line in lines:

            # 🔹 Detect new experience header (line with date)
            date_match = re.search(date_pattern, line, re.I)

            if date_match:
                # Save previous entry
                if current:
                    current["description"] = " ".join(current["description"]).strip()
                    items.append(current)

                start_date = date_match.group("start").strip()
                end_date = date_match.group("end").strip()

                header_part = line[:date_match.start()].strip(" ,.-")

                title = None
                company = None

                if "," in header_part:
                    parts = [p.strip() for p in header_part.split(",", 1)]
                    title = parts[0]
                    company = parts[1] if len(parts) > 1 else None
                else:
                    title = header_part

                current = {
                    "title": title,
                    "company": company,
                    "start_date": start_date,
                    "end_date": end_date,
                    "description": []
                }

            # 🔹 Bullet lines (description)
            else:
                if current:
                    clean_line = line.lstrip("•-– ").strip()
                    if clean_line:
                        current["description"].append(clean_line)

        # Add last item
        if current:
            current["description"] = " ".join(current["description"]).strip()
            items.append(current)

        return items

    def calculate_total_experience(self, experiences: list[dict]) -> float:
        total_months = 0

        for exp in experiences:
            start_date = parse_date_safe(exp.get("start_date"))
            end_date = parse_date_safe(exp.get("end_date"))
            total_months += month_diff(start_date, end_date)

        return round(total_months / 12, 2)

    def calculate_skill_experience(self, experiences: list[dict], skills: list[str]) -> dict:
        result = {}
        all_skills = {normalize_skill(skill) for skill in skills if skill.strip()}
        all_skills.update({normalize_skill(skill) for skill in KNOWN_SKILLS})

        for skill in all_skills:
            if not skill:
                continue

            months = 0
            for exp in experiences:
                blob = f"{exp.get('title', '')}\n{exp.get('description', '')}".lower()
                if skill in blob:
                    start_date = parse_date_safe(exp.get("start_date"))
                    end_date = parse_date_safe(exp.get("end_date"))
                    months += month_diff(start_date, end_date)

            if months > 0:
                result[skill] = {
                    "skill": skill,
                    "months": months,
                    "years": round(months / 12, 2),
                }

        return result