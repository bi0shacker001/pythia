"""Bounded ZIP extraction with complete preflight validation."""

from __future__ import annotations

import os
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

DEFAULT_MAX_MEMBERS = 100_000
DEFAULT_MAX_TOTAL_SIZE = 32 * 1024**3
DEFAULT_MAX_MEMBER_SIZE = 16 * 1024**3
DEFAULT_MAX_COMPRESSION_RATIO = 2_000


class UnsafeArchiveError(ValueError):
    pass


def _member_parts(name: str) -> tuple[str, ...]:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or normalized.startswith("/") or path.is_absolute():
        raise UnsafeArchiveError(f"archive member has an absolute or empty path: {name!r}")
    parts = path.parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise UnsafeArchiveError(f"archive member escapes or ambiguously names its destination: {name!r}")
    if ":" in parts[0]:
        raise UnsafeArchiveError(f"archive member uses a drive-qualified path: {name!r}")
    return parts


def safe_extract_zip(
    archive: zipfile.ZipFile | str | os.PathLike,
    destination: str | os.PathLike,
    *,
    max_members: int = DEFAULT_MAX_MEMBERS,
    max_total_size: int = DEFAULT_MAX_TOTAL_SIZE,
    max_member_size: int = DEFAULT_MAX_MEMBER_SIZE,
    max_compression_ratio: int = DEFAULT_MAX_COMPRESSION_RATIO,
) -> list[Path]:
    """Validate the complete ZIP, then extract regular files/directories only."""
    should_close = not isinstance(archive, zipfile.ZipFile)
    zf = zipfile.ZipFile(archive) if should_close else archive
    try:
        infos = zf.infolist()
        if len(infos) > max_members:
            raise UnsafeArchiveError(f"archive has {len(infos)} members; limit is {max_members}")
        total = 0
        checked = []
        for info in infos:
            parts = _member_parts(info.filename)
            if stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF):
                raise UnsafeArchiveError(f"archive contains a symbolic link: {info.filename!r}")
            if info.file_size < 0 or info.file_size > max_member_size:
                raise UnsafeArchiveError(f"archive member exceeds the size limit: {info.filename!r}")
            total += info.file_size
            if total > max_total_size:
                raise UnsafeArchiveError(f"archive expands beyond the {max_total_size}-byte total limit")
            if info.file_size and info.compress_size == 0:
                raise UnsafeArchiveError(f"archive member has invalid compressed size: {info.filename!r}")
            if info.compress_size and info.file_size / info.compress_size > max_compression_ratio:
                raise UnsafeArchiveError(f"archive member exceeds the compression-ratio limit: {info.filename!r}")
            checked.append((info, parts))

        root = Path(destination).resolve()
        root.mkdir(parents=True, exist_ok=True)
        extracted = []
        for info, parts in checked:
            target = root.joinpath(*parts)
            if target != root and root not in target.parents:
                raise UnsafeArchiveError(f"archive member escapes destination: {info.filename!r}")
            if info.is_dir() or info.filename.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            mode = (info.external_attr >> 16) & 0o777
            if mode:
                try:
                    target.chmod(mode)
                except OSError:
                    pass
            extracted.append(target)
        return extracted
    finally:
        if should_close:
            zf.close()
