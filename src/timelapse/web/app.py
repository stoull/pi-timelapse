from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Optional, Tuple
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.requests import Request

from ..camera import CameraError, CameraService
from ..config_schema import CameraConfig
from ..encode import WATERMARK_POSITIONS, EncodeError, EncodeManager, WatermarkOptions
from ..project import ProjectError, ProjectManager
from ..runtime import CaptureRuntime, RuntimeStatus
from ..storage import (
    StorageError,
    count_frames,
    create_thumbnail,
    delete_photo,
    delete_photos,
    ensure_thumbnails,
    export_file,
    first_photo,
    latest_frame,
    list_exports,
    list_photos,
    parse_filter_datetime,
    photo_path,
)
from ..system import monitor as system_monitor

ROOT = Path(os.environ.get("TIMELAPSE_APP_ROOT", Path(__file__).resolve().parents[3]))
PRESETS = Path(os.environ.get("TIMELAPSE_PRESETS", ROOT / "config" / "presets"))
REGISTRY = Path(os.environ.get("TIMELAPSE_STATE_DIR", "/var/lib/pi-timelapse"))
WEB_ROOT = Path(__file__).parent

projects = ProjectManager(PRESETS, REGISTRY)
runtime = CaptureRuntime(projects, CameraService())
encoder = EncodeManager()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if runtime.resume_required:
        try:
            await runtime.start()
        except Exception:
            pass
    runtime.arm_schedule()
    system_monitor.snapshot()
    yield
    await runtime.stop()


app = FastAPI(title="Pi 延时摄影", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB_ROOT / "static"), name="static")
templates = Jinja2Templates(directory=WEB_ROOT / "templates")


class CreateProjectRequest(BaseModel):
    preset_id: str
    project_id: str
    name: str
    storage_root: str
    interval_sec: Optional[float] = Field(None, gt=0)
    resolution: Optional[Tuple[int, int]] = None
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    min_free_gb: float = Field(5, ge=0.1)


class SwitchRequest(BaseModel):
    project_id: str


class UpdateProjectRequest(BaseModel):
    interval_sec: Optional[float] = Field(None, gt=0, le=86400)
    window_type: Optional[str] = None
    clock_start: Optional[str] = None
    clock_end: Optional[str] = None


class DeletePhotoRequest(BaseModel):
    photo_id: str


class DeletePhotosRequest(BaseModel):
    photo_ids: list[str] = Field(default_factory=list, min_length=1)


class EnsureThumbnailsRequest(BaseModel):
    photo_ids: list[str] = Field(default_factory=list, min_length=1)


class ScheduleStartRequest(BaseModel):
    start_at: str
    stop_at: Optional[str] = None


class CameraTuneRequest(BaseModel):
    camera: CameraConfig
    jpeg_quality: Optional[int] = Field(None, ge=70, le=100)
    main_size: Optional[Tuple[int, int]] = None


class CameraTuneCloseRequest(BaseModel):
    discard: bool = True


class FocusRequest(BaseModel):
    lock_manual: bool = False


class ExportRequest(BaseModel):
    watermark_type: str = "none"
    timestamp_format: str = "datetime"
    position: str = "bottom_center"
    text: str = Field("", max_length=50)
    fps: Literal[24, 25, 30] = 24


def fail(exc: Exception, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail=str(exc))


def runtime_payload() -> dict[str, Any]:
    return {
        "runtime": runtime.snapshot(),
        "encode": encoder.snapshot(),
        "system": system_monitor.snapshot(),
    }


def project_first_photo_payload(project) -> Optional[dict[str, str]]:
    first = first_photo(project)
    if first is None:
        return None
    photo_id = first.relative_to(project.project_dir / "frames").as_posix()
    encoded_project = quote(project.project_id, safe="")
    encoded_photo = quote(photo_id, safe="")
    return {
        "id": photo_id,
        "thumbnail_url": (
            f"/api/projects/{encoded_project}/photos/thumbnail?photo_id={encoded_photo}"
        ),
    }


def project_list_payload(project) -> dict[str, Any]:
    return {
        **project.model_dump(mode="json"),
        "first_photo": project_first_photo_payload(project),
    }


def parse_schedule_time(value: str, timezone: str) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise RuntimeError("时间格式无效") from exc
    zone = ZoneInfo(timezone)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/bootstrap")
async def bootstrap() -> dict[str, Any]:
    return {
        "runtime": runtime.snapshot(),
        "presets": projects.presets(),
        "projects": [project_list_payload(project) for project in projects.list_projects()],
        "encode": encoder.snapshot(),
        "system": system_monitor.snapshot(),
    }


@app.get("/api/presets/{preset_id}")
async def preset_detail(preset_id: str) -> dict[str, Any]:
    try:
        return projects.preset(preset_id)
    except ProjectError as exc:
        raise fail(exc, 404)


@app.post("/api/projects", status_code=201)
async def create_project(body: CreateProjectRequest) -> dict[str, Any]:
    overrides = body.model_dump(
        include={"interval_sec", "resolution", "window_start", "window_end", "min_free_gb"},
        exclude_none=True,
    )
    try:
        project = projects.create(
            preset_id=body.preset_id,
            project_id=body.project_id,
            name=body.name,
            storage_root=body.storage_root,
            overrides=overrides,
        )
        projects.register(project)
        await runtime.switch(project)
        return project.model_dump(mode="json")
    except Exception as exc:
        raise fail(exc)


@app.post("/api/projects/switch")
async def switch_project(body: SwitchRequest) -> dict[str, Any]:
    try:
        project = projects.get(body.project_id)
        await runtime.switch(project)
        return runtime_payload()
    except Exception as exc:
        raise fail(exc)


@app.get("/api/projects/{project_id}")
async def project_detail(project_id: str) -> dict[str, Any]:
    try:
        project = projects.get(project_id)
    except ProjectError as exc:
        raise fail(exc, 404)
    active = runtime.project is not None and runtime.project.project_id == project_id
    return {
        "project": project.model_dump(mode="json"),
        "frames_total": count_frames(project),
        "exports": list_exports(project),
        "is_active": active,
        "runtime_status": runtime.status.value if active else None,
        "first_photo": project_first_photo_payload(project),
    }


@app.patch("/api/projects/{project_id}")
async def update_project(project_id: str, body: UpdateProjectRequest) -> dict[str, Any]:
    try:
        project = projects.get(project_id)
        if body.interval_sec is not None:
            project = projects.set_interval(project_id, body.interval_sec)
        if body.window_type is not None:
            project = projects.set_window(
                project_id,
                window_type=body.window_type,
                clock_start=body.clock_start,
                clock_end=body.clock_end,
            )
        if runtime.project is not None and runtime.project.project_id == project_id:
            runtime.project = project
            runtime._persist()
        return {
            "project": project.model_dump(mode="json"),
            "runtime": runtime.snapshot(),
        }
    except Exception as exc:
        raise fail(exc)


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str) -> dict[str, Any]:
    try:
        project = projects.get(project_id)
    except ProjectError as exc:
        raise fail(exc, 404)
    if encoder.status == "encoding" and runtime.project and runtime.project.project_id == project_id:
        raise HTTPException(409, "视频正在导出，请完成后再删除项目")
    if runtime.project is not None and runtime.project.project_id == project_id:
        await runtime.drop_project()
    try:
        await asyncio.to_thread(projects.delete, project_id)
    except Exception as exc:
        raise fail(exc)
    return {
        **runtime_payload(),
        "projects": [project_list_payload(item) for item in projects.list_projects()],
        "deleted_project_id": project.project_id,
    }


@app.get("/api/projects/{project_id}/exports")
async def project_exports(project_id: str) -> dict[str, Any]:
    try:
        project = projects.get(project_id)
    except ProjectError as exc:
        raise fail(exc, 404)
    return {"exports": list_exports(project)}


@app.get("/api/projects/{project_id}/exports/file")
async def download_project_export(project_id: str, name: str) -> FileResponse:
    try:
        project = projects.get(project_id)
        path = export_file(project, name)
    except (ProjectError, StorageError) as exc:
        raise fail(exc, 404)
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@app.get("/api/projects/{project_id}/photos/thumbnail")
async def project_photo_thumbnail(project_id: str, photo_id: str) -> FileResponse:
    try:
        project = projects.get(project_id)
        frame = photo_path(project, photo_id)
        thumbnail = await asyncio.to_thread(create_thumbnail, project, frame)
        return FileResponse(
            thumbnail,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=86400"},
        )
    except ProjectError as exc:
        raise fail(exc, 404)
    except StorageError as exc:
        raise fail(exc, 404)


@app.post("/api/capture/start")
async def start_capture() -> dict[str, Any]:
    try:
        await runtime.start()
        return runtime_payload()
    except Exception as exc:
        raise fail(exc)


@app.post("/api/capture/schedule")
async def schedule_capture(body: ScheduleStartRequest) -> dict[str, Any]:
    if runtime.project is None:
        raise HTTPException(400, "请先选择项目")
    try:
        when = parse_schedule_time(body.start_at, runtime.project.window.timezone)
        stop_at = None
        if body.stop_at and body.stop_at.strip():
            stop_at = parse_schedule_time(body.stop_at, runtime.project.window.timezone)
        await runtime.schedule_start(when, stop_at)
        return runtime_payload()
    except Exception as exc:
        raise fail(exc)


@app.delete("/api/capture/schedule")
async def cancel_scheduled_capture() -> dict[str, Any]:
    await runtime.cancel_schedule()
    return runtime_payload()


@app.post("/api/capture/pause")
async def pause_capture() -> dict[str, Any]:
    await runtime.pause()
    return runtime_payload()


@app.post("/api/capture/resume")
async def resume_capture() -> dict[str, Any]:
    await runtime.resume()
    return runtime_payload()


@app.post("/api/capture/stop")
async def stop_capture() -> dict[str, Any]:
    try:
        await runtime.stop()
        return runtime_payload()
    except Exception as exc:
        raise fail(exc)


@app.post("/api/capture/clear-restart")
async def clear_and_restart() -> dict[str, Any]:
    try:
        deleted = await runtime.clear_and_restart()
        payload = runtime_payload()
        payload["deleted"] = deleted
        return payload
    except Exception as exc:
        raise fail(exc)


@app.post("/api/capture/test")
async def test_capture() -> dict[str, str]:
    try:
        path = await runtime.test_shot()
        return {"path": str(path), "url": f"/api/test-shot/{path.name}"}
    except Exception as exc:
        raise fail(exc)


@app.get("/api/test-shot/{filename}")
async def test_shot_file(filename: str):
    project = runtime.project
    if project is None or Path(filename).name != filename:
        raise HTTPException(404)
    path = project.project_dir / "previews" / filename
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/capture/focus")
async def autofocus(body: Optional[FocusRequest] = None) -> dict[str, Any]:
    try:
        lock_manual = bool(body.lock_manual) if body else False
        position = await runtime.autofocus(lock_manual=lock_manual)
        return {"lens_position": position, "camera": runtime.camera_tune_state()}
    except Exception as exc:
        raise fail(exc)


@app.post("/api/camera/tune/auto")
async def camera_tune_auto() -> dict[str, Any]:
    try:
        return await runtime.auto_scene()
    except Exception as exc:
        raise fail(exc)


@app.get("/api/camera/tune")
async def camera_tune_get() -> dict[str, Any]:
    if runtime.project is None:
        raise HTTPException(400, "请先选择项目")
    return runtime.camera_tune_state()


@app.post("/api/camera/tune/session")
async def camera_tune_open() -> dict[str, Any]:
    try:
        return await runtime.open_camera_tune()
    except Exception as exc:
        raise fail(exc)


@app.patch("/api/camera/tune")
async def camera_tune_apply(body: CameraTuneRequest) -> dict[str, Any]:
    try:
        return await runtime.apply_camera_tune(
            body.camera,
            jpeg_quality=body.jpeg_quality,
            main_size=body.main_size,
        )
    except Exception as exc:
        raise fail(exc)


@app.post("/api/camera/tune/commit")
async def camera_tune_commit() -> dict[str, Any]:
    try:
        state = await runtime.commit_camera_tune()
        return {"tune": state, **runtime_payload()}
    except Exception as exc:
        raise fail(exc)


@app.post("/api/camera/tune/close")
async def camera_tune_close(body: CameraTuneCloseRequest) -> dict[str, Any]:
    try:
        await runtime.close_camera_tune(discard=body.discard)
        return runtime_payload()
    except Exception as exc:
        raise fail(exc)


@app.get("/api/status")
async def status() -> dict[str, Any]:
    return runtime_payload()


@app.get("/api/photos")
async def photos(
    offset: int = Query(0, ge=0),
    limit: int = Query(24, ge=1, le=100),
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> dict[str, Any]:
    project = runtime.project
    if project is None:
        raise HTTPException(400, "请先选择项目")
    try:
        start_at = parse_filter_datetime(start)
        end_at = parse_filter_datetime(end)
    except StorageError as exc:
        raise fail(exc)
    if start_at and end_at and end_at < start_at:
        raise HTTPException(400, "结束时间不能早于开始时间")
    if start_at and end_at and end_at - start_at < timedelta(minutes=5):
        raise HTTPException(400, "筛选时间段至少为 5 分钟")
    result = await asyncio.to_thread(list_photos, project, offset, limit, start_at, end_at)
    for item in result["items"]:
        encoded = quote(item["id"], safe="")
        item["thumbnail_url"] = f"/api/photos/thumbnail?photo_id={encoded}"
        item["full_url"] = f"/api/photos/full?photo_id={encoded}"
    return result


@app.post("/api/photos/ensure-thumbnails")
async def warm_thumbnails(body: EnsureThumbnailsRequest) -> dict[str, Any]:
    project = runtime.project
    if project is None:
        raise HTTPException(400, "请先选择项目")
    # Cap each request so the Pi stays responsive while opening the gallery.
    photo_ids = body.photo_ids[:48]
    return await asyncio.to_thread(ensure_thumbnails, project, photo_ids)


@app.get("/api/photos/thumbnail")
async def photo_thumbnail(photo_id: str) -> FileResponse:
    project = runtime.project
    if project is None:
        raise HTTPException(400, "请先选择项目")
    try:
        frame = photo_path(project, photo_id)
        thumbnail = await asyncio.to_thread(create_thumbnail, project, frame)
        return FileResponse(
            thumbnail,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=86400"},
        )
    except StorageError as exc:
        raise fail(exc, 404)


@app.get("/api/photos/full")
async def photo_full(
    photo_id: str, download: int = Query(0, ge=0, le=1)
) -> FileResponse:
    project = runtime.project
    if project is None:
        raise HTTPException(400, "请先选择项目")
    try:
        frame = photo_path(project, photo_id)
        headers = {"Cache-Control": "private, max-age=3600"}
        if download:
            return FileResponse(
                frame,
                media_type="image/jpeg",
                filename=frame.name,
                headers=headers,
            )
        return FileResponse(
            frame,
            media_type="image/jpeg",
            headers=headers,
        )
    except StorageError as exc:
        raise fail(exc, 404)


@app.delete("/api/photos")
async def remove_photo(body: DeletePhotoRequest) -> dict[str, Any]:
    project = runtime.project
    if project is None:
        raise HTTPException(400, "请先选择项目")
    if encoder.status == "encoding":
        raise HTTPException(409, "视频正在导出，请完成后再删除照片")
    try:
        deleted = await asyncio.to_thread(delete_photo, project, body.photo_id)
        return {"deleted": deleted, "runtime": runtime.snapshot()}
    except StorageError as exc:
        raise fail(exc, 404)


@app.post("/api/photos/delete")
async def remove_photos(body: DeletePhotosRequest) -> dict[str, Any]:
    project = runtime.project
    if project is None:
        raise HTTPException(400, "请先选择项目")
    if encoder.status == "encoding":
        raise HTTPException(409, "视频正在导出，请完成后再删除照片")
    # Deduplicate while preserving order.
    seen = set()
    photo_ids = []
    for photo_id in body.photo_ids:
        if photo_id not in seen:
            seen.add(photo_id)
            photo_ids.append(photo_id)
    if len(photo_ids) > 200:
        raise HTTPException(400, "一次最多删除 200 张照片")
    result = await asyncio.to_thread(delete_photos, project, photo_ids)
    return {"result": result, "runtime": runtime.snapshot()}


@app.get("/api/preview.jpg")
async def preview() -> Response:
    project = runtime.project
    tune_open = runtime.camera_tune_open
    # While capturing, always serve the latest saved frame. Live preview
    # contends for the camera lock and can stall the still pipeline.
    if (
        not tune_open
        and runtime.status
        in {
            RuntimeStatus.STARTING,
            RuntimeStatus.CAPTURING,
            RuntimeStatus.WAITING,
            RuntimeStatus.PAUSED,
        }
    ):
        path = latest_frame(project) if project else None
        if path is not None:
            return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
        raise HTTPException(404, "暂无画面")
    if project is not None and (tune_open or runtime.status == RuntimeStatus.IDLE):
        try:
            data = await runtime.live_preview_jpeg()
            return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
        except (CameraError, RuntimeError):
            pass
    path = latest_frame(project) if project else None
    if path is None:
        raise HTTPException(404, "暂无画面")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/latest-thumbnail.jpg")
async def latest_thumbnail() -> FileResponse:
    project = runtime.project
    path = latest_frame(project) if project else None
    if project is None or path is None:
        raise HTTPException(404, "暂无最新照片")
    try:
        thumbnail = await asyncio.to_thread(create_thumbnail, project, path)
        return FileResponse(
            thumbnail,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )
    except StorageError as exc:
        raise fail(exc, 404)


@app.post("/api/export")
async def export_video(body: ExportRequest) -> dict[str, Any]:
    if runtime.project is None:
        raise HTTPException(400, "请先选择项目")
    try:
        if body.watermark_type not in {"none", "timestamp", "text"}:
            raise EncodeError("无效的水印类型")
        if body.timestamp_format not in {"datetime", "time"}:
            raise EncodeError("无效的时间戳格式")
        if body.position not in WATERMARK_POSITIONS:
            raise EncodeError("无效的水印位置")
        await encoder.start(
            runtime.project,
            WatermarkOptions(
                type=body.watermark_type,
                timestamp_format=body.timestamp_format,
                position=body.position,
                text=body.text,
            ),
            fps=body.fps,
        )
        return encoder.snapshot()
    except EncodeError as exc:
        raise fail(exc)


@app.get("/api/export/download")
async def download_video():
    path = encoder.output
    if encoder.status != "done" or path is None or not path.is_file():
        raise HTTPException(404, "视频尚未生成")
    return FileResponse(path, media_type="video/mp4", filename=path.name)
