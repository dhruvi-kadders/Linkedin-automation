from __future__ import annotations

import fitz  # PyMuPDF
from app.utils.text_utils import clean_text


class PDFTextExtractor:
    @staticmethod
    def extract_text(pdf_path: str) -> str:
        chunks: list[str] = []

        with fitz.open(pdf_path) as doc:
            for page in doc:
                # sort=True usually improves top-left to bottom-right reading order
                page_text = page.get_text("text", sort=True)
                if page_text:
                    chunks.append(page_text)

        return clean_text("\n".join(chunks))