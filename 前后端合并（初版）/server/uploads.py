# -*- coding: utf-8 -*-
"""上传文件校验。

原则：不相信客户端给的文件名和 Content-Type。
- 扩展名必须在白名单内；
- 文件头（魔数）必须和扩展名声明的类型一致；
- 大小不超过上限（读取时就截断，不会把超大文件整个读进内存）；
- 图片要能被完整解码，像素总数不超过上限（防解压炸弹）；
- 落盘文件名由服务端生成，不使用客户端文件名。
"""
from __future__ import annotations

import hashlib
import io
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .errors import UploadRejected

IMAGE_EXTS = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp", ".bmp": "bmp"}
AUDIO_EXTS = {".wav": "wav", ".mp3": "mp3", ".webm": "webm", ".ogg": "ogg", ".m4a": "mp4", ".mp4": "mp4"}
MIME = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp", "bmp": "image/bmp",
        "wav": "audio/wav", "mp3": "audio/mpeg", "webm": "audio/webm", "ogg": "audio/ogg", "mp4": "audio/mp4"}


def sniff(head: bytes) -> Optional[str]:
    """根据文件头判断真实类型，认不出返回 None。"""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head.startswith(b"BM"):
        return "bmp"
    if head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "mp3"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    if head.startswith(b"OggS"):
        return "ogg"
    if head[4:8] == b"ftyp":
        return "mp4"
    return None


@dataclass
class ValidatedUpload:
    kind: str            # image / audio
    format: str          # png / jpeg / wav ...
    ext: str             # 规范化后的扩展名，如 .jpg
    mime: str
    size: int
    sha256: str
    data: bytes
    original_name: str
    width: Optional[int] = None
    height: Optional[int] = None

    def save(self, dest_dir: Path, stem: str) -> Path:
        """用服务端指定的文件名保存，返回路径。"""
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"{stem}{self.ext}"
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(self.data)
        tmp.replace(path)
        return path


def _read_limited(stream, max_bytes: int, field: str) -> bytes:
    data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise UploadRejected(f"文件超过大小上限 {max_bytes / 1024 / 1024:.0f} MB", code="UPLOAD_TOO_LARGE",
                             http_status=413, details={"field": field, "max_bytes": max_bytes})
    if not data:
        raise UploadRejected("上传的文件是空的", code="UPLOAD_EMPTY", details={"field": field})
    return data


def _validate(file_storage, allowed: dict, kind: str, max_bytes: int, field: str) -> ValidatedUpload:
    if file_storage is None or not getattr(file_storage, "filename", ""):
        raise UploadRejected(f"缺少文件字段 {field}", code="UPLOAD_MISSING", details={"field": field})
    original = os.path.basename(file_storage.filename.replace("\\", "/"))
    ext = os.path.splitext(original)[1].lower()
    if ext not in allowed:
        raise UploadRejected(f"不支持的文件类型 {ext or '（无扩展名）'}，允许：{'、'.join(sorted(allowed))}",
                             code="UPLOAD_TYPE_NOT_ALLOWED", http_status=415,
                             details={"field": field, "filename": original})
    data = _read_limited(file_storage.stream, max_bytes, field)
    actual = sniff(data[:16])
    if actual != allowed[ext]:
        raise UploadRejected("文件内容与扩展名不符", code="UPLOAD_CONTENT_MISMATCH", http_status=415,
                             details={"field": field, "filename": original, "declared": allowed[ext],
                                      "detected": actual or "unknown"})
    canonical_ext = ".jpg" if actual == "jpeg" else ext
    return ValidatedUpload(kind=kind, format=actual, ext=canonical_ext, mime=MIME[actual], size=len(data),
                           sha256=hashlib.sha256(data).hexdigest(), data=data, original_name=original)


def validate_image(file_storage, *, max_bytes: int, max_pixels: int, field: str = "file") -> ValidatedUpload:
    upload = _validate(file_storage, IMAGE_EXTS, "image", max_bytes, field)
    from PIL import Image
    try:
        with Image.open(io.BytesIO(upload.data)) as img:
            width, height = img.size
            if width * height > max_pixels:
                raise UploadRejected(f"图片分辨率过大（{width}×{height}）", code="UPLOAD_IMAGE_TOO_LARGE",
                                     http_status=413, details={"field": field, "max_pixels": max_pixels})
            img.verify()              # 只校验结构
        with Image.open(io.BytesIO(upload.data)) as img:
            img.load()                # 真正解码一遍，截断或损坏的图片在这里报错
    except UploadRejected:
        raise
    except Exception as exc:
        raise UploadRejected("图片无法解码，可能已损坏", code="UPLOAD_IMAGE_CORRUPT",
                             details={"field": field, "reason": str(exc)[:200]}) from exc
    if width < 64 or height < 64:
        raise UploadRejected(f"图片太小（{width}×{height}），至少 64×64", code="UPLOAD_IMAGE_TOO_SMALL",
                             details={"field": field})
    upload.width, upload.height = width, height
    return upload


def validate_audio(file_storage, *, max_bytes: int, field: str = "audio") -> ValidatedUpload:
    return _validate(file_storage, AUDIO_EXTS, "audio", max_bytes, field)
