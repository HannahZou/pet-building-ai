"""Photo validation and storage.

``PhotoStorage`` is the interface; ``LocalPhotoStorage`` is the MVP backend.
A cloud backend (S3, GCS, Azure Blob) only needs ``save`` / ``read`` / ``delete``
with opaque keys - nothing else in the app knows where bytes live.

Validation: extension allow-list (jpg, jpeg, png), configurable size limit
(``PAWPAL_MAX_PHOTO_MB``, default 5), the *content* must decode as the format the
extension claims, and a pixel limit guards against decompression bombs.
Sanitising: images are re-encoded from pixels only, which drops EXIF (GPS,
device, timestamps) and text chunks; orientation is applied first.
Files are stored outside any public/static folder (``PAWPAL_PHOTO_DIR``, default
``data/photos``) under random names; the UI never shows paths or keys.
"""
from __future__ import annotations

import io
import os
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG"}
CONTENT_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png"}
MAX_PIXELS = 40_000_000
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class PhotoValidationError(Exception):
    pass


def max_photo_bytes() -> int:
    try:
        mb = float(os.getenv("PAWPAL_MAX_PHOTO_MB", "5"))
    except ValueError:
        mb = 5.0
    return int(mb * 1024 * 1024)


@dataclass(frozen=True)
class CleanImage:
    data: bytes
    ext: str
    content_type: str
    width: int
    height: int


def sanitize_image(data: bytes, filename: str, max_bytes: int | None = None) -> CleanImage:
    max_bytes = max_bytes if max_bytes is not None else max_photo_bytes()
    ext = Path(filename or "").suffix.lower().lstrip(".")
    if ext not in ALLOWED:
        raise PhotoValidationError("Only JPG, JPEG and PNG photos are allowed.")
    if not data:
        raise PhotoValidationError("The file is empty.")
    if len(data) > max_bytes:
        raise PhotoValidationError(f"Photos must be at most {max_bytes / 1024 / 1024:.0f} MB.")
    expected = ALLOWED[ext]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                actual = probe.format
                width, height = probe.size
                probe.verify()  # structural check
            if width * height > MAX_PIXELS:
                raise PhotoValidationError("The image dimensions are too large.")
            with Image.open(io.BytesIO(data)) as img:
                img.load()
                img = ImageOps.exif_transpose(img)
                pixels = img.convert("RGB") if expected == "JPEG" else img.convert("RGBA" if "A" in img.getbands() else "RGB")
    except PhotoValidationError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombWarning,
            Image.DecompressionBombError) as exc:
        raise PhotoValidationError("The file is not a valid image.") from exc
    if actual != expected:
        raise PhotoValidationError(f"The file content is {actual or 'unknown'}, which does not match '.{ext}'.")

    clean = Image.new(pixels.mode, pixels.size)
    clean.paste(pixels)  # pixels only: no EXIF, ICC or text metadata carried over
    out = io.BytesIO()
    if expected == "JPEG":
        clean.save(out, format="JPEG", quality=90, optimize=True)
    else:
        clean.save(out, format="PNG", optimize=True)
    return CleanImage(out.getvalue(), "jpg" if expected == "JPEG" else "png",
                      CONTENT_TYPES[expected], clean.width, clean.height)


class PhotoStorage(Protocol):
    def save(self, data: bytes, ext: str) -> str: ...
    def read(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class LocalPhotoStorage:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or os.getenv("PAWPAL_PHOTO_DIR") or PROJECT_ROOT / "data" / "photos").resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise PhotoValidationError("Invalid photo key.")
        return path

    def save(self, data: bytes, ext: str) -> str:
        name = uuid.uuid4().hex
        key = f"{name[:2]}/{name}.{ext}"
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "xb") as fh:  # never overwrite
            fh.write(data)
        return key

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass


def default_storage() -> PhotoStorage:
    return LocalPhotoStorage()
