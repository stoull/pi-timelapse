from __future__ import annotations

import asyncio
import json
from datetime import datetime, time
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from .camera import CameraService
from .config_schema import CameraConfig, ProjectConfig
from .project import ProjectManager
from .schedule.ticker import advance_slot, next_slot
from .schedule.window import in_capture_window, window_label
from .storage import (
    clear_project_media,
    count_frames,
    create_thumbnail,
    ensure_space,
    frame_path,
    latest_frame,
    storage_status,
)


class RuntimeStatus(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    CAPTURING = "capturing"
    WAITING = "waiting"
    PAUSED = "paused"
    ERROR = "error"


class CaptureRuntime:
    def __init__(self, projects: ProjectManager, camera: CameraService) -> None:
        self.projects = projects
        self.camera = camera
        self.project: Optional[ProjectConfig] = projects.active()
        self.status = RuntimeStatus.IDLE
        self.error: Optional[str] = None
        self.last_ok_at: Optional[datetime] = None
        self.consecutive_failures = 0
        self._task: Optional[asyncio.Task[None]] = None
        self._stop = asyncio.Event()
        self._event_active = False
        self._night_active = False
        self._operation_lock = asyncio.Lock()
        self.resume_required = False
        self.scheduled_start_at: Optional[datetime] = None
        self.scheduled_stop_at: Optional[datetime] = None
        self._schedule_start_task: Optional[asyncio.Task[None]] = None
        self._schedule_stop_task: Optional[asyncio.Task[None]] = None
        self._tune_session = False
        self._tune_camera_baseline: Optional[CameraConfig] = None
        self._tune_still_baseline: Optional[tuple[int, int, int]] = None
        self._restore_state()

    @property
    def camera_tune_open(self) -> bool:
        return self._tune_session

    def _restore_state(self) -> None:
        if self.project is None:
            return
        path = self.project.project_dir / "state.json"
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.last_ok_at = (
                datetime.fromisoformat(data["last_ok_at"]) if data.get("last_ok_at") else None
            )
            self.consecutive_failures = int(data.get("consecutive_failures", 0))
            self.resume_required = data.get("status") in {
                RuntimeStatus.CAPTURING,
                RuntimeStatus.WAITING,
                RuntimeStatus.STARTING,
            }
            zone = ZoneInfo(self.project.window.timezone)
            now = datetime.now(zone)
            start_at = self._parse_saved_time(data.get("scheduled_start_at"), zone)
            stop_at = self._parse_saved_time(data.get("scheduled_stop_at"), zone)
            if stop_at is not None and stop_at <= now:
                start_at = None
                stop_at = None
            if self.resume_required:
                self.scheduled_start_at = None
                self.scheduled_stop_at = stop_at
            else:
                self.scheduled_start_at = start_at
                self.scheduled_stop_at = stop_at
        except (OSError, ValueError, TypeError):
            self.resume_required = False

    async def switch(self, project: ProjectConfig) -> None:
        async with self._operation_lock:
            await self._stop_locked()
            self.project = project
            self.projects.activate(project)
            self.error = None
            self.status = RuntimeStatus.IDLE
            self._cancel_schedule(persist=False)
            self._tune_session = False
            self._tune_camera_baseline = None
            self._tune_still_baseline = None
            self._persist()

    async def drop_project(self) -> None:
        async with self._operation_lock:
            await self._stop_locked()
            self.project = None
            self.error = None
            self.status = RuntimeStatus.IDLE
            self.last_ok_at = None
            self.consecutive_failures = 0
            self._cancel_schedule(persist=False)

    async def start(self) -> None:
        async with self._operation_lock:
            if self.project is None:
                raise RuntimeError("请先选择项目")
            if self._tune_session:
                raise RuntimeError("正在相机设置中，请先关闭后再开始拍摄")
            self._clear_scheduled_start(persist=False)

            # Pause -> continue without tearing down the camera.
            if (
                self._task is not None
                and not self._task.done()
                and self.status == RuntimeStatus.PAUSED
            ):
                self.status = RuntimeStatus.CAPTURING
                self.error = None
                self._persist()
                return

            # A finished/cancelled task must not block a fresh start.
            if self._task is not None and self._task.done():
                self._task = None

            if self._task is not None and not self._task.done():
                # Already capturing / waiting / starting.
                return

            was_running = self.camera.is_running
            await self._stop_camera_only()
            if was_running:
                # Give libcamera time to release after live preview kept it open.
                await asyncio.sleep(1.0)
            self.status = RuntimeStatus.STARTING
            self.error = None
            self.consecutive_failures = 0
            self._night_active = False
            self._stop = asyncio.Event()
            self._event_active = True
            try:
                ensure_space(self.project)
                await asyncio.to_thread(self.camera.start, self.project)
            except Exception as exc:
                self.status = RuntimeStatus.ERROR
                self.error = str(exc)
                self._event_active = False
                self._persist()
                raise
            self._task = asyncio.create_task(self._run(), name="capture-loop")
            self.status = RuntimeStatus.CAPTURING
            self._persist()

    async def pause(self) -> None:
        if self.status in {RuntimeStatus.CAPTURING, RuntimeStatus.WAITING, RuntimeStatus.STARTING}:
            self.status = RuntimeStatus.PAUSED
            self._persist()

    async def resume(self) -> None:
        if self.status == RuntimeStatus.PAUSED:
            if self._task is None or self._task.done():
                await self.start()
                return
            self.status = RuntimeStatus.CAPTURING
            self._persist()

    async def stop(self) -> None:
        async with self._operation_lock:
            await self._stop_locked()

    async def clear_and_restart(self) -> dict[str, int]:
        async with self._operation_lock:
            if self.project is None:
                raise RuntimeError("请先选择项目")
            await self._stop_locked()
            self._cancel_schedule(persist=False)
            deleted = clear_project_media(self.project)
            self.last_ok_at = None
            self.consecutive_failures = 0
            self.error = None
            self._persist()
            self.status = RuntimeStatus.STARTING
            self._stop = asyncio.Event()
            self._event_active = True
            self._night_active = False
            try:
                ensure_space(self.project)
                await asyncio.to_thread(self.camera.start, self.project)
            except Exception as exc:
                self.status = RuntimeStatus.ERROR
                self.error = str(exc)
                self._event_active = False
                self._persist()
                raise
            self._task = asyncio.create_task(self._run(), name="capture-loop")
            self.status = RuntimeStatus.CAPTURING
            self._persist()
            return deleted

    async def _stop_camera_only(self) -> None:
        try:
            await asyncio.wait_for(asyncio.to_thread(self.camera.stop), timeout=5)
        except asyncio.TimeoutError:
            # abandon() is lock-free and safe on the event loop.
            self.camera.abandon()
        except Exception:
            self.camera.abandon()

    async def _stop_locked(self, clear_schedule: bool = True) -> None:
        self._stop.set()
        self._event_active = False
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=3)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        await self._stop_camera_only()
        self.status = RuntimeStatus.IDLE
        self.error = None
        if clear_schedule:
            self._cancel_schedule(persist=False)
        self._persist()

    def _busy_capturing(self) -> bool:
        return self.status in {
            RuntimeStatus.CAPTURING,
            RuntimeStatus.WAITING,
            RuntimeStatus.STARTING,
            RuntimeStatus.PAUSED,
        } or (self._task is not None and not self._task.done())

    def _assert_can_tune(self) -> None:
        if self.project is None:
            raise RuntimeError("请先选择项目")
        if self._busy_capturing():
            raise RuntimeError("拍摄进行中，请先停止后再打开相机设置")

    def _tune_dirty(self) -> bool:
        if self.project is None or self._tune_camera_baseline is None:
            return False
        still = self.project.capture.still_config
        current_still = (still.main_size[0], still.main_size[1], still.jpeg_quality)
        return (
            self.project.camera.model_dump() != self._tune_camera_baseline.model_dump()
            or current_still != self._tune_still_baseline
        )

    def camera_tune_state(self) -> dict[str, Any]:
        from .camera.tuning import CONTROL_SPEC

        project = self.project
        live: dict[str, Any] = {}
        if self.camera.is_running:
            try:
                live = self.camera.live_metadata()
            except Exception:
                live = {}
        return {
            "project_id": project.project_id if project else None,
            "session": self._tune_session,
            "dirty": self._tune_dirty(),
            "capturing": self.status
            in {
                RuntimeStatus.CAPTURING,
                RuntimeStatus.WAITING,
                RuntimeStatus.STARTING,
                RuntimeStatus.PAUSED,
            },
            "camera": project.camera.model_dump(mode="json") if project else None,
            "still": project.capture.still_config.model_dump(mode="json") if project else None,
            "live": live,
            "spec": CONTROL_SPEC,
        }

    async def open_camera_tune(self) -> dict[str, Any]:
        self._assert_can_tune()
        async with self._operation_lock:
            self._assert_can_tune()
            self._tune_camera_baseline = self.project.camera.model_copy(deep=True)
            still = self.project.capture.still_config
            self._tune_still_baseline = (still.main_size[0], still.main_size[1], still.jpeg_quality)
            if not self.camera.is_running:
                await asyncio.to_thread(self.camera.start, self.project, skip_startup_af=True)
            self._tune_session = True
        return self.camera_tune_state()

    async def apply_camera_tune(
        self,
        camera: CameraConfig,
        *,
        jpeg_quality: Optional[int] = None,
        main_size: Optional[tuple[int, int]] = None,
    ) -> dict[str, Any]:
        self._assert_can_tune()
        async with self._operation_lock:
            self._assert_can_tune()
            size_changed = False
            rotation_changed = self.project.camera.rotation != camera.rotation
            if main_size is not None:
                size_changed = tuple(self.project.capture.still_config.main_size) != tuple(main_size)
                self.project.capture.still_config.main_size = main_size
            if jpeg_quality is not None:
                self.project.capture.still_config.jpeg_quality = jpeg_quality
            self.project.camera = camera
            if not self.camera.is_running:
                await asyncio.to_thread(self.camera.start, self.project, skip_startup_af=True)
            elif size_changed or rotation_changed:
                await asyncio.to_thread(self.camera.stop)
                await asyncio.to_thread(self.camera.start, self.project, skip_startup_af=True)
            else:
                await asyncio.to_thread(self.camera.apply_controls, camera)
            self._tune_session = True
        return self.camera_tune_state()

    async def commit_camera_tune(self) -> dict[str, Any]:
        self._assert_can_tune()
        async with self._operation_lock:
            self._assert_can_tune()
            self.projects.save(self.project)
            still = self.project.capture.still_config
            self._tune_camera_baseline = self.project.camera.model_copy(deep=True)
            self._tune_still_baseline = (still.main_size[0], still.main_size[1], still.jpeg_quality)
        return self.camera_tune_state()

    async def close_camera_tune(self, discard: bool = True) -> None:
        async with self._operation_lock:
            if discard and self.project is not None and self._tune_camera_baseline is not None:
                self.project.camera = self._tune_camera_baseline.model_copy(deep=True)
                if self._tune_still_baseline is not None:
                    width, height, quality = self._tune_still_baseline
                    self.project.capture.still_config.main_size = (width, height)
                    self.project.capture.still_config.jpeg_quality = quality
                if self.camera.is_running:
                    try:
                        await asyncio.to_thread(self.camera.apply_controls, self.project.camera)
                    except Exception:
                        pass
            self._tune_session = False
            self._tune_camera_baseline = None
            self._tune_still_baseline = None
            await self._stop_camera_only()

    async def test_shot(self) -> Path:
        if self.project is None:
            raise RuntimeError("请先选择项目")
        async with self._operation_lock:
            temporary_start = not self.camera.is_running
            if temporary_start:
                await asyncio.to_thread(self.camera.start, self.project)
            path = self.project.project_dir / "previews" / (
                f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            )
            try:
                await asyncio.to_thread(self.camera.capture, path)
                return path
            finally:
                if temporary_start and not self._tune_session:
                    await self._stop_camera_only()

    async def live_preview_jpeg(self) -> bytes:
        if self.project is None:
            raise RuntimeError("请先选择项目")
        async with self._operation_lock:
            if (
                not self._tune_session
                and self.status
                in {
                    RuntimeStatus.STARTING,
                    RuntimeStatus.CAPTURING,
                    RuntimeStatus.WAITING,
                    RuntimeStatus.PAUSED,
                }
            ):
                raise RuntimeError("拍摄中使用最新已保存画面")
            if not self.camera.is_running:
                await asyncio.to_thread(
                    self.camera.start,
                    self.project,
                    skip_startup_af=True,
                )
            return await asyncio.to_thread(self.camera.preview_jpeg)

    async def autofocus(self, lock_manual: bool = False) -> float:
        if self.project is None:
            raise RuntimeError("请先选择项目")
        async with self._operation_lock:
            if self.status in {
                RuntimeStatus.CAPTURING,
                RuntimeStatus.WAITING,
                RuntimeStatus.STARTING,
            }:
                raise RuntimeError("拍摄进行中，请先停止后再自动对焦")
            temporary_start = not self.camera.is_running
            if temporary_start:
                await asyncio.to_thread(self.camera.start, self.project)
            try:
                position = await asyncio.wait_for(
                    asyncio.to_thread(self.camera.autofocus_once, lock_manual),
                    timeout=20,
                )
                self.project.camera.lens_position = position
                if lock_manual:
                    self.project.camera.af_mode = "manual"
                else:
                    self.project.camera.af_mode = "auto_once"
                return position
            finally:
                if temporary_start and not self._tune_session:
                    await self._stop_camera_only()

    async def auto_scene(self) -> dict[str, Any]:
        self._assert_can_tune()
        async with self._operation_lock:
            self._assert_can_tune()
            if not self.camera.is_running:
                await asyncio.to_thread(self.camera.start, self.project, skip_startup_af=True)
            measured = await asyncio.wait_for(
                asyncio.to_thread(self.camera.auto_scene),
                timeout=25,
            )
            self._tune_session = True
            state = self.camera_tune_state()
            state["measured"] = measured
            return state

    async def _run(self) -> None:
        assert self.project is not None
        project = self.project
        zone = ZoneInfo(project.window.timezone)
        slot = next_slot(datetime.now(zone), project.capture.interval_sec)
        try:
            while not self._stop.is_set():
                wait = max(0.0, (slot - datetime.now(zone)).total_seconds())
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=wait if wait > 0 else 0.01)
                    break
                except asyncio.TimeoutError:
                    pass
                if self._stop.is_set():
                    break
                now = datetime.now(zone)
                if self.status == RuntimeStatus.PAUSED:
                    slot = advance_slot(slot, project.capture.interval_sec, now)
                    continue
                if not in_capture_window(project.window, now, self._event_active):
                    self.status = RuntimeStatus.WAITING
                    slot = advance_slot(slot, project.capture.interval_sec, now)
                    self._persist()
                    continue
                self.status = RuntimeStatus.CAPTURING
                try:
                    ensure_space(project)
                    await self._apply_day_night_controls(project, now)
                    path = frame_path(project, now)
                    # Bound capture so a wedged libcamera call cannot stall the loop.
                    # Single AF + AE can take several seconds per still.
                    timeout = 35.0 if project.camera.af_mode == "auto_once" else 20.0
                    if not project.camera.ae_enable:
                        timeout = max(
                            timeout,
                            project.camera.exposure_time_us / 1_000_000.0 * 6.0 + 10.0,
                        )
                    await asyncio.wait_for(
                        asyncio.to_thread(self.camera.capture, path),
                        timeout=timeout,
                    )
                    try:
                        await asyncio.to_thread(create_thumbnail, project, path)
                    except Exception:
                        # The original frame is authoritative; thumbnails can
                        # always be generated lazily by the gallery endpoint.
                        pass
                    self.last_ok_at = now
                    self.consecutive_failures = 0
                    self.error = None
                except Exception as exc:
                    self.consecutive_failures += 1
                    self.error = str(exc)
                    if self.consecutive_failures in {3, 5, 8}:
                        try:
                            await asyncio.wait_for(
                                asyncio.to_thread(self.camera.reopen),
                                timeout=15,
                            )
                        except Exception as reopen_exc:
                            self.error = str(reopen_exc)
                    if self.consecutive_failures >= 10:
                        self.status = RuntimeStatus.ERROR
                        self._persist()
                        return
                self._persist()
                slot = advance_slot(slot, project.capture.interval_sec, datetime.now(zone))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.status = RuntimeStatus.ERROR
            self.error = str(exc)
            self._persist()
        finally:
            if self._task is asyncio.current_task():
                self._task = None

    async def _apply_day_night_controls(self, project: ProjectConfig, now: datetime) -> None:
        if project.camera_night is None or project.night_switch_at is None:
            return
        hour, minute = (int(value) for value in project.night_switch_at.split(":"))
        should_use_night = now.time().replace(tzinfo=None) >= time(hour, minute)
        if should_use_night == self._night_active:
            return
        config = project.camera_night if should_use_night else project.camera
        await asyncio.to_thread(self.camera.apply_controls, config)
        self._night_active = should_use_night
        await asyncio.sleep(0.2)

    def _project_zone(self) -> ZoneInfo:
        name = "Asia/Shanghai"
        if self.project is not None:
            name = self.project.window.timezone or name
        return ZoneInfo(name)

    def _parse_saved_time(self, raw: Any, zone: ZoneInfo) -> Optional[datetime]:
        if not raw:
            return None
        when = datetime.fromisoformat(str(raw))
        if when.tzinfo is None:
            return when.replace(tzinfo=zone)
        return when.astimezone(zone)

    def _aware(self, when: datetime, zone: ZoneInfo) -> datetime:
        if when.tzinfo is None:
            return when.replace(tzinfo=zone)
        return when.astimezone(zone)

    def _cancel_task(self, task: Optional[asyncio.Task[None]]) -> None:
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

    def _clear_scheduled_start(self, persist: bool = False) -> None:
        self.scheduled_start_at = None
        task, self._schedule_start_task = self._schedule_start_task, None
        self._cancel_task(task)
        if persist:
            self._persist()

    def _cancel_schedule(self, persist: bool = True) -> None:
        self._clear_scheduled_start(persist=False)
        self.scheduled_stop_at = None
        task, self._schedule_stop_task = self._schedule_stop_task, None
        self._cancel_task(task)
        if persist:
            self._persist()

    async def cancel_schedule(self) -> None:
        self._cancel_schedule(persist=True)

    def arm_schedule(self) -> None:
        if self.project is None:
            return
        now = datetime.now(self._project_zone())
        if self.scheduled_stop_at is not None and self.scheduled_stop_at <= now:
            self._cancel_schedule(persist=True)
            return
        capturing = self.status in {
            RuntimeStatus.CAPTURING,
            RuntimeStatus.WAITING,
            RuntimeStatus.STARTING,
            RuntimeStatus.PAUSED,
        }
        if self.scheduled_start_at is not None and not capturing:
            if self._schedule_start_task is None or self._schedule_start_task.done():
                self._schedule_start_task = asyncio.create_task(
                    self._wait_for_start(), name="scheduled-start"
                )
        if self.scheduled_stop_at is not None:
            if self._schedule_stop_task is None or self._schedule_stop_task.done():
                self._schedule_stop_task = asyncio.create_task(
                    self._wait_for_stop(), name="scheduled-stop"
                )

    async def schedule_start(self, when: datetime, stop_at: Optional[datetime] = None) -> None:
        if self.project is None:
            raise RuntimeError("请先选择项目")
        if self.status in {
            RuntimeStatus.CAPTURING,
            RuntimeStatus.WAITING,
            RuntimeStatus.STARTING,
            RuntimeStatus.PAUSED,
        }:
            raise RuntimeError("拍摄进行中，请先停止后再设定时拍摄")
        zone = self._project_zone()
        when = self._aware(when, zone)
        now = datetime.now(zone)
        wait = (when - now).total_seconds()
        if wait <= 0:
            raise RuntimeError("开始时间必须是未来的时间点")
        if wait > 90 * 24 * 3600:
            raise RuntimeError("定时开拍最多可预约 90 天")
        if stop_at is not None:
            stop_at = self._aware(stop_at, zone)
            if (stop_at - when).total_seconds() < 1:
                raise RuntimeError("结束时间必须晚于开始时间")
            if (stop_at - now).total_seconds() > 90 * 24 * 3600:
                raise RuntimeError("结束时间最多可预约 90 天")
        self._cancel_schedule(persist=False)
        self.scheduled_start_at = when
        self.scheduled_stop_at = stop_at
        self.error = None
        self._persist()
        self.arm_schedule()

    async def _wait_for_start(self) -> None:
        try:
            while self.scheduled_start_at is not None:
                wait = (
                    self.scheduled_start_at - datetime.now(self.scheduled_start_at.tzinfo)
                ).total_seconds()
                if wait <= 0:
                    break
                await asyncio.sleep(min(wait, 15.0))
            if self.scheduled_start_at is None:
                return
            self.scheduled_start_at = None
            self._schedule_start_task = None
            self._persist()
            await self._begin_scheduled_capture()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.status = RuntimeStatus.ERROR
            self.error = f"定时开拍失败：{exc}"
            self._clear_scheduled_start(persist=False)
            self._persist()

    async def _begin_scheduled_capture(self) -> None:
        if self._tune_session:
            await self.close_camera_tune(discard=True)
        async with self._operation_lock:
            if (
                self._task is not None
                and not self._task.done()
                and self.status in {
                    RuntimeStatus.CAPTURING,
                    RuntimeStatus.WAITING,
                    RuntimeStatus.STARTING,
                    RuntimeStatus.PAUSED,
                }
            ):
                if self.status == RuntimeStatus.PAUSED:
                    self.status = RuntimeStatus.CAPTURING
                self.error = None
                self._persist()
                return
            await self._stop_locked(clear_schedule=False)
        await asyncio.sleep(1.0)
        await self.start()

    async def _wait_for_stop(self) -> None:
        try:
            while self.scheduled_stop_at is not None:
                wait = (
                    self.scheduled_stop_at - datetime.now(self.scheduled_stop_at.tzinfo)
                ).total_seconds()
                if wait <= 0:
                    break
                await asyncio.sleep(min(wait, 15.0))
            if self.scheduled_stop_at is None:
                return
            self.scheduled_stop_at = None
            self._schedule_stop_task = None
            self._persist()
            await self.stop()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.status = RuntimeStatus.ERROR
            self.error = f"定时停止失败：{exc}"
            self.scheduled_stop_at = None
            self._schedule_stop_task = None
            self._persist()

    def snapshot(self) -> dict[str, Any]:
        project = self.project
        latest = latest_frame(project) if project else None
        zone = ZoneInfo(project.window.timezone) if project else ZoneInfo("Asia/Shanghai")
        now = datetime.now(zone)
        window_open = (
            in_capture_window(project.window, now, self._event_active) if project else False
        )
        return {
            "status": self.status,
            "error": self.error,
            "camera_available": self.camera.available,
            "project": project.model_dump(mode="json") if project else None,
            "frames_total": count_frames(project) if project else 0,
            "last_frame": str(latest) if latest else None,
            "last_ok_at": self.last_ok_at.isoformat() if self.last_ok_at else None,
            "scheduled_start_at": (
                self.scheduled_start_at.isoformat() if self.scheduled_start_at else None
            ),
            "scheduled_stop_at": (
                self.scheduled_stop_at.isoformat() if self.scheduled_stop_at else None
            ),
            "window_open": window_open,
            "window_label": window_label(project.window) if project else None,
            "consecutive_failures": self.consecutive_failures,
            "storage": storage_status(project) if project else None,
            "camera_tune_open": self._tune_session,
            "camera_tune_dirty": self._tune_dirty(),
        }

    def _persist(self) -> None:
        if self.project is None:
            return
        path = self.project.project_dir / "state.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self.snapshot(), ensure_ascii=False, default=str, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)
