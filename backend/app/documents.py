from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader


def extract_text(path: Path, mime_type: str | None) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf" or mime_type == "application/pdf":
        reader = PdfReader(str(path))
        pages = []
        for index, page in enumerate(reader.pages, start=1):
            pages.append(f"[PDF page {index}]\n{page.extract_text() or ''}")
        return "\n\n".join(pages)
    if suffix in {".pptx", ".ppt"}:
        if suffix == ".ppt":
            return ""
        from pptx import Presentation

        presentation = Presentation(str(path))
        slides = []
        for index, slide in enumerate(presentation.slides, start=1):
            texts = [shape.text for shape in slide.shapes if hasattr(shape, "text") and shape.text.strip()]
            slides.append(f"[PPT slide {index}]\n" + "\n".join(texts))
        return "\n\n".join(slides)
    if suffix in {".txt", ".md", ".csv"} or (mime_type or "").startswith("text/"):
        return path.read_text(encoding="utf-8", errors="replace")
    return ""
