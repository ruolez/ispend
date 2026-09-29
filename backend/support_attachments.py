"""Screenshots on problem reports.

Only PNG, JPEG and WebP, decided by the file's first bytes rather than its name or the browser's
claim. Every image is decoded and written out again, which drops EXIF (a phone photo of a screen
carries its location) and anything else riding along in the file. Stored under the customer's
statements folder with a random name, so backups carry them and erasing the account removes them.
"""

import io
import os
import secrets

from PIL import Image, ImageOps

import config

MAX_FILES = 3
MAX_BYTES = 5 * 1024 * 1024
MAX_SIDE = 10000
MAX_PIXELS = 40_000_000
# Stored copies are scaled down to this; a 6K monitor screenshot is still readable at 4096.
STORED_SIDE = 4096

FORMATS = {
    "png": {"pil": "PNG", "mime": "image/png", "ext": "png", "save": {}},
    "jpeg": {"pil": "JPEG", "mime": "image/jpeg", "ext": "jpg", "save": {"quality": 90}},
    "webp": {"pil": "WEBP", "mime": "image/webp", "ext": "webp", "save": {"quality": 90}},
}


class AttachmentError(Exception):
    pass


def sniff(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _decode(data, kind):
    try:
        img = Image.open(io.BytesIO(data))
    except Exception as e:
        raise AttachmentError("That image could not be read") from e
    if img.format != FORMATS[kind]["pil"]:
        raise AttachmentError("That image could not be read")
    w, h = img.size
    if w > MAX_SIDE or h > MAX_SIDE or w * h > MAX_PIXELS:
        raise AttachmentError("That image is too large")
    try:
        img.load()
    except Exception as e:
        raise AttachmentError("That image could not be read") from e
    return img


def prepare(data):
    """Validate and re-encode one image: {data, mime, ext, bytes, width, height}."""
    if not data:
        raise AttachmentError("The screenshot is empty")
    if len(data) > MAX_BYTES:
        raise AttachmentError(f"Each screenshot must be under {MAX_BYTES // (1024 * 1024)} MB")
    kind = sniff(data)
    if kind is None:
        raise AttachmentError("Screenshots must be PNG, JPEG or WebP images")
    spec = FORMATS[kind]
    img = ImageOps.exif_transpose(_decode(data, kind))
    if kind == "jpeg" and img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    elif kind == "webp" and img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    if max(img.size) > STORED_SIDE:
        img.thumbnail((STORED_SIDE, STORED_SIDE))
    # A fresh copy with no info dict: nothing from the upload's metadata can reach the encoder.
    clean = img.copy()
    clean.info = {}
    buf = io.BytesIO()
    clean.save(buf, format=spec["pil"], **spec["save"])
    out = buf.getvalue()
    return {"data": out, "mime": spec["mime"], "ext": spec["ext"], "bytes": len(out),
            "width": clean.size[0], "height": clean.size[1]}


def prepare_all(files):
    """files: uploaded file objects (werkzeug FileStorage or anything with .read)."""
    files = [f for f in files if f is not None]
    if len(files) > MAX_FILES:
        raise AttachmentError(f"Attach at most {MAX_FILES} screenshots")
    return [prepare(f.read(MAX_BYTES + 1)) for f in files]


def abs_path(rel):
    return os.path.join(config.STATEMENTS_DIR, rel)


def store(user_id, prepared):
    rel = f"{int(user_id)}/support/{secrets.token_hex(16)}.{prepared['ext']}"
    path = abs_path(rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.part"
    with open(tmp, "wb") as f:
        f.write(prepared["data"])
    os.replace(tmp, path)
    return rel


def remove(rels):
    """Best effort: used when the database write after a store fails."""
    for rel in rels:
        try:
            os.unlink(abs_path(rel))
        except OSError:
            pass
