"""Checks for evidence uploads (D10, CW-084).

The pilot has no malware scanning (CUS-140), so these checks matter: allowed extensions only,
25 MB per file, and the file's contents must match its extension. Macro-enabled and legacy
Office files, audio and anything else are rejected.
"""

import zipfile

from django.conf import settings
from django.core.exceptions import ValidationError

TYPES = {
    "pdf": ("application/pdf", "supplier_document"),
    "png": ("image/png", "screenshot"),
    "jpg": ("image/jpeg", "screenshot"),
    "jpeg": ("image/jpeg", "screenshot"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "supplier_document"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "supplier_document"),
    "eml": ("message/rfc822", "email"),
    "msg": ("application/vnd.ms-outlook", "email"),
    "srt": ("text/plain", "transcript"),
    "vtt": ("text/vtt", "transcript"),
    "txt": ("text/plain", "transcript"),
}
ACCEPT = ",".join("." + ext for ext in TYPES)
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _ext(name):
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _head(upload, n=8192):
    upload.seek(0)
    data = upload.read(n)
    upload.seek(0)
    return data


def _check_ooxml(upload, main_part):
    try:
        with zipfile.ZipFile(upload) as z:
            names = set(z.namelist())
    except zipfile.BadZipFile:
        return False
    finally:
        upload.seek(0)
    if "[Content_Types].xml" not in names or not any(n.startswith(main_part) for n in names):
        return False
    if any(n.lower().endswith("vbaproject.bin") for n in names):
        raise ValidationError("Files containing macros can't be uploaded.")
    return True


def _is_text(upload):
    upload.seek(0)
    try:
        for chunk in upload.chunks():
            chunk.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False
    finally:
        upload.seek(0)


def validate_upload(upload):
    """Return (extension, content_type, suggested_category) or raise ValidationError."""
    name = upload.name or ""
    ext = _ext(name)
    if ext not in TYPES:
        raise ValidationError(
            f"“{name}” can't be uploaded. Allowed: PDF, PNG, JPG, Word (.docx), Excel (.xlsx), "
            "emails (.eml, .msg) and transcripts (.srt, .vtt, .txt). No audio."
        )
    limit = settings.EVIDENCE_MAX_UPLOAD_BYTES
    if upload.size > limit:
        raise ValidationError(f"“{name}” is larger than {limit // (1024 * 1024)} MB.")
    if upload.size == 0:
        raise ValidationError(f"“{name}” is empty.")
    head = _head(upload)
    ok = {
        "pdf": lambda: head.startswith(b"%PDF-"),
        "png": lambda: head.startswith(b"\x89PNG\r\n\x1a\n"),
        "jpg": lambda: head.startswith(b"\xff\xd8\xff"),
        "jpeg": lambda: head.startswith(b"\xff\xd8\xff"),
        "docx": lambda: _check_ooxml(upload, "word/"),
        "xlsx": lambda: _check_ooxml(upload, "xl/"),
        "msg": lambda: head.startswith(OLE),
        # Exported emails use various encodings; check it starts with a header line like "From: ...".
        "eml": lambda: b":" in head.split(b"\n", 1)[0] and b"\x00" not in head,
        "srt": lambda: _is_text(upload),
        "vtt": lambda: _is_text(upload) and head.lstrip(b"\xef\xbb\xbf").startswith(b"WEBVTT"),
        "txt": lambda: _is_text(upload),
    }[ext]()
    if not ok:
        raise ValidationError(f"“{name}” doesn't look like a real .{ext} file, so it can't be uploaded.")
    content_type, category = TYPES[ext]
    return ext, content_type, category
