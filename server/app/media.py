"""사진 저장: storage/media/YYYY/MM/<sha256>.<ext>"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG": ("image/jpeg", "jpg"), "PNG": ("image/png", "png"), "WEBP": ("image/webp", "webp"), "GIF": ("image/gif", "gif")}


@dataclass(frozen=True)
class StoredMedia:
    file_path: str
    mime_type: str
    size_bytes: int
    width: int
    height: int
    sha256: str


def store_image(data: bytes, media_root: Path, max_bytes: int, now: datetime) -> StoredMedia:
    if not data or len(data) > max_bytes:
        raise HTTPException(413, "이미지 크기가 허용 범위를 벗어났습니다.")
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt, (width, height) = img.format, img.size
            img.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(415, "이미지 파일이 아닙니다.") from exc
    if fmt not in ALLOWED_FORMATS:
        raise HTTPException(415, f"지원하지 않는 이미지 형식입니다: {fmt}")

    mime, ext = ALLOWED_FORMATS[fmt]
    sha256 = hashlib.sha256(data).hexdigest()
    relative = f"{now:%Y/%m}/{sha256}.{ext}"
    target = media_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        tmp = target.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(target)
    return StoredMedia(relative, mime, len(data), width, height, sha256)


def resolve(media_root: Path, relative: str) -> Path:
    path = (media_root / relative).resolve()
    if not path.is_file() or media_root not in path.parents:
        raise HTTPException(404, "파일이 없습니다.")
    return path
