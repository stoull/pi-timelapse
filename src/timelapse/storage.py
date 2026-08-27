from __future__ import annotations

import re
import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Union

from PIL import Image, ImageOps

from .config_schema import ProjectConfig


class StorageError(RuntimeError):
    pass


_FRAME_TIME = re.compile(r"frame_(\d{8})_(\d{6})(?:_(\d{3}))?", re.IGNORECASE)
_MIN_FILTER_SPAN = timedelta(minutes=5)


def parse_photo_time(filename: str) -> Optional[datetime]:
    match = _FRAME_TIME.search(filename)
    if not match:
        return None
    stamp = f"{match.group(1)}{match.group(2)}{(match.group(3) or '000')}000"
    try:
        return datetime.strptime(stamp, "%Y%m%d%H%M%S%f")
    except ValueError:
        return None


def floor_to_five_minutes(value: datetime) -> datetime:
    return value.replace(minute=(value.minute // 5) * 5, second=0, microsecond=0)


def ceil_to_five_minutes(value: datetime) -> datetime:
    floored = floor_to_five_minutes(value)
    if floored == value.replace(second=0, microsecond=0) and value.second == 0 and value.microsecond == 0:
        return floored
    return floored + _MIN_FILTER_SPAN


def _iso(value: Optional[datetime]) -> Optional[str]:
    return None if value is None else value.replace(microsecond=0).isoformat(timespec="minutes")


def parse_filter_datetime(value: Optional[str]) -> Optional[datetime]:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip().replace("Z", "")
    for fmt, size in (("%Y-%m-%dT%H:%M", 16), ("%Y-%m-%dT%H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16)):
        try:
            return datetime.strptime(text[:size], fmt)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise StorageError("时间格式无效") from exc
    return parsed.replace(tzinfo=None)


def photo_time_range(photos: list[Path]) -> Dict[str, Optional[str]]:
    times = [parse_photo_time(path.name) for path in photos]
    times = [value for value in times if value is not None]
    if not times:
        return {
            "earliest": None,
            "latest": None,
            "start_min": None,
            "end_max": None,
            "min_span_minutes": 5,
        }
    earliest = min(times)
    latest = max(times)
    start_min = floor_to_five_minutes(earliest)
    end_max = ceil_to_five_minutes(latest)
    if end_max < start_min + _MIN_FILTER_SPAN:
        end_max = start_min + _MIN_FILTER_SPAN
    return {
        "earliest": _iso(earliest),
        "latest": _iso(latest),
        "start_min": _iso(start_min),
        "end_max": _iso(end_max),
        "min_span_minutes": 5,
    }


def storage_status(project: ProjectConfig) -> Dict[str, Union[float, str, bool]]:
    try:
        usage = shutil.disk_usage(project.storage.root)
    except OSError as exc:
        return {
            "available": False,
            "error": str(exc),
            "free_gb": 0,
            "total_gb": 0,
            "used_percent": 0,
            "root": str(project.storage.root),
        }
    divisor = 1024**3
    return {
        "available": usage.free / divisor >= project.storage.min_free_gb,
        "free_gb": round(usage.free / divisor, 2),
        "total_gb": round(usage.total / divisor, 2),
        "used_percent": round(usage.used / usage.total * 100, 1),
        "root": str(project.storage.root),
    }


def ensure_space(project: ProjectConfig) -> None:
    status = storage_status(project)
    if not status["available"]:
        raise StorageError(f"存储空间不足或不可用（剩余 {status.get('free_gb', 0)} GB）")


def frame_path(project: ProjectConfig, now: datetime) -> Path:
    root = project.project_dir / "frames"
    if project.storage.mkdir_by == "day":
        root /= now.strftime("%Y-%m-%d")
    return root / f"frame_{now.strftime('%Y%m%d_%H%M%S_%f')[:-3]}.jpg"


def latest_frame(project: ProjectConfig):
    frames = project.project_dir / "frames"
    if not frames.exists():
        return None
    return max(frames.glob("**/*.jpg"), key=lambda p: p.name, default=None)


def count_frames(project: ProjectConfig) -> int:
    frames = project.project_dir / "frames"
    return sum(1 for _ in frames.glob("**/*.jpg")) if frames.exists() else 0


def list_photos(
    project: ProjectConfig,
    offset: int = 0,
    limit: int = 48,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> Dict[str, Any]:
    frames = project.project_dir / "frames"
    photos = sorted(frames.glob("**/*.jpg"), key=lambda path: path.name, reverse=True)
    bounds = photo_time_range(photos)
    if start is not None or end is not None:
        filtered = []
        for path in photos:
            captured = parse_photo_time(path.name)
            if captured is None:
                continue
            if start is not None and captured < start:
                continue
            if end is not None and captured > end:
                continue
            filtered.append(path)
        photos = filtered
    page = photos[offset : offset + limit]
    items = []
    for path in page:
        photo_id = path.relative_to(frames).as_posix()
        thumb = thumbnail_path(project, path)
        captured = parse_photo_time(path.name)
        items.append(
            {
                "id": photo_id,
                "filename": path.name,
                "size": path.stat().st_size,
                "has_thumbnail": thumb.is_file(),
                "captured_at": _iso(captured),
            }
        )
    return {
        "items": items,
        "total": len(photos),
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < len(photos),
        "range": bounds,
    }


def first_photo(project: ProjectConfig) -> Optional[Path]:
    frames = project.project_dir / "frames"
    photos = sorted(frames.glob("**/*.jpg"), key=lambda path: path.name)
    return photos[0] if photos else None


def photo_path(project: ProjectConfig, photo_id: str) -> Path:
    frames = (project.project_dir / "frames").resolve()
    candidate = (frames / photo_id).resolve()
    try:
        candidate.relative_to(frames)
    except ValueError as exc:
        raise StorageError("照片路径无效") from exc
    if candidate.suffix.lower() not in {".jpg", ".jpeg"} or not candidate.is_file():
        raise StorageError("照片不存在")
    return candidate


def thumbnail_path(project: ProjectConfig, frame: Path) -> Path:
    frames = (project.project_dir / "frames").resolve()
    relative = frame.resolve().relative_to(frames)
    return project.project_dir / "thumbnails" / relative


_thumbnail_lock = threading.Lock()


def create_thumbnail(project: ProjectConfig, frame: Path) -> Path:
    target = thumbnail_path(project, frame)
    if target.is_file() and target.stat().st_mtime_ns >= frame.stat().st_mtime_ns:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        f".{target.stem}.{threading.get_ident()}.thumbnailing.jpg"
    )
    # Serialize thumbnail work: concurrent 4K encodes can stall the Pi.
    with _thumbnail_lock:
        if target.is_file() and target.stat().st_mtime_ns >= frame.stat().st_mtime_ns:
            return target
        try:
            with Image.open(frame) as source:
                image = ImageOps.exif_transpose(source)
                resampling = getattr(Image, "Resampling", Image)
                # BILINEAR is much cheaper than LANCZOS on Raspberry Pi.
                image.thumbnail((320, 180), resampling.BILINEAR)
                image.convert("RGB").save(temporary, "JPEG", quality=65, optimize=False)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def ensure_thumbnails(project: ProjectConfig, photo_ids: list[str]) -> Dict[str, int]:
    created = 0
    skipped = 0
    failed = 0
    for photo_id in photo_ids:
        try:
            frame = photo_path(project, photo_id)
            target = thumbnail_path(project, frame)
            existed = target.is_file() and target.stat().st_mtime_ns >= frame.stat().st_mtime_ns
            create_thumbnail(project, frame)
            if existed:
                skipped += 1
            else:
                created += 1
        except Exception:
            failed += 1
    return {"created": created, "skipped": skipped, "failed": failed}


def delete_photo(project: ProjectConfig, photo_id: str) -> Dict[str, Any]:
    frame = photo_path(project, photo_id)
    thumbnail = thumbnail_path(project, frame)
    size = frame.stat().st_size
    frame.unlink()
    thumbnail.unlink(missing_ok=True)
    for parent in (frame.parent, thumbnail.parent):
        try:
            parent.rmdir()
        except OSError:
            pass
    return {"id": photo_id, "size": size}


def delete_photos(project: ProjectConfig, photo_ids: list[str]) -> Dict[str, Any]:
    deleted: list[Dict[str, Any]] = []
    errors: list[Dict[str, str]] = []
    for photo_id in photo_ids:
        try:
            deleted.append(delete_photo(project, photo_id))
        except StorageError as exc:
            errors.append({"id": photo_id, "error": str(exc)})
    return {"deleted": deleted, "errors": errors, "count": len(deleted)}


def clear_project_media(project: ProjectConfig) -> Dict[str, int]:
    """Delete captured frames, previews and exports. Keep project.yaml / logs."""
    deleted = {"frames": 0, "thumbnails": 0, "previews": 0, "exports": 0}
    for name in ("frames", "thumbnails", "previews", "exports"):
        path = project.project_dir / name
        if path.exists():
            deleted[name] = sum(1 for p in path.rglob("*") if p.is_file())
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
    return deleted


def list_exports(project: ProjectConfig) -> list[Dict[str, Any]]:
    folder = project.project_dir / "exports"
    if not folder.exists():
        return []
    items: list[Dict[str, Any]] = []
    for path in folder.glob("*.mp4"):
        stat = path.stat()
        items.append(
            {
                "filename": path.name,
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            }
        )
    return sorted(items, key=lambda item: item["modified_at"], reverse=True)


def export_file(project: ProjectConfig, filename: str) -> Path:
    if Path(filename).name != filename or not filename.lower().endswith(".mp4"):
        raise StorageError("导出文件名无效")
    path = (project.project_dir / "exports" / filename).resolve()
    exports = (project.project_dir / "exports").resolve()
    try:
        path.relative_to(exports)
    except ValueError as exc:
        raise StorageError("导出路径无效") from exc
    if not path.is_file():
        raise StorageError("导出文件不存在")
    return path
