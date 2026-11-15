"""Uploaded files: what they really are (checked by their first bytes, not their name), how many pages they have, and the
page images a vision model needs. PDFs are rendered with PyMuPDF."""

import hashlib
from dataclasses import dataclass
from typing import Optional

import pymupdf  # PyMuPDF

# (extension, MIME type, magic bytes at the start of the file)
SIGNATURES = (
    ("pdf", "application/pdf", b"%PDF-"),
    ("png", "image/png", b"\x89PNG\r\n\x1a\n"),
    ("jpg", "image/jpeg", b"\xff\xd8\xff"),
)


@dataclass(frozen=True)
class FileType:
    extension: str
    mime: str


def detect_file_type(data: bytes) -> Optional[FileType]:
    """The real type of the file from its content, or None when it is not a PDF, PNG or JPEG (whatever its name says)."""
    for extension, mime, magic in SIGNATURES:
        if data.startswith(magic):
            return FileType(extension, mime)
    return None


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class UnreadableFile(ValueError):
    """The bytes look like a PDF or image but cannot be opened."""


def count_pages(data: bytes, file_type: FileType) -> int:
    """1 for an image; the page count for a PDF. Raises UnreadableFile for a damaged or password-protected PDF."""
    if file_type.mime != "application/pdf":
        return 1
    try:
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            if pdf.needs_pass:
                raise UnreadableFile("The PDF is password protected.")
            return pdf.page_count
    except UnreadableFile:
        raise
    except Exception as error:  # PyMuPDF raises several error types for damaged files
        raise UnreadableFile("The PDF could not be opened (it may be damaged).") from error


@dataclass
class SourceFile:
    """What the LLM clients receive: the original bytes (Gemini reads PDFs directly) and, on request, page images (Groq reads images only)."""
    data: bytes
    mime: str
    pages: int = 1

    def page_images(self, dpi: int = 150) -> list[tuple[bytes, str]]:
        """[(image bytes, MIME type)], one per page. An uploaded image is returned as it is; a PDF is rendered to PNG."""
        if self.mime != "application/pdf":
            return [(self.data, self.mime)]
        images = []
        with pymupdf.open(stream=self.data, filetype="pdf") as pdf:
            for page in pdf:
                images.append((page.get_pixmap(dpi=dpi).tobytes("png"), "image/png"))
        return images
