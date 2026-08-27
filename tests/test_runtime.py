import asyncio
from pathlib import Path

import pytest

from timelapse.camera import CameraService
from timelapse.config_schema import ProjectConfig
from timelapse.project import ProjectManager
from timelapse.runtime import CaptureRuntime, RuntimeStatus


class FakeCamera(CameraService):
    def __init__(self) -> None:
        super().__init__()
        self.started = 0
        self.stopped = 0
        self.shots = 0
        self.previews = 0

    @property
    def available(self) -> bool:
        return True

    def start(self, project, **_kwargs) -> None:
        self.started += 1
        self._project = project
        self._camera = object()

    def stop(self) -> None:
        self.stopped += 1
        self._camera = None

    def abandon(self) -> None:
        self._camera = None
        self._project = None

    def capture(self, path: Path):
        self.shots += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake")
        return {}

    def preview_jpeg(self) -> bytes:
        self.previews += 1
        return b"fake-jpeg"

    def apply_controls(self, config) -> None:
        self.last_controls = config
        if self._project is not None:
            self._project.camera = config

    def live_metadata(self) -> dict:
        return {
            "exposure_time": 33333,
            "analogue_gain": 1.0,
            "lens_position": 2.5,
            "colour_gains": [1.6, 1.5],
            "lux": 120.0,
        }

    def autofocus_once(self, lock_manual: bool = False) -> float:
        if lock_manual and self._project is not None:
            self._project.camera.af_mode = "manual"
            self._project.camera.lens_position = 3.25
        return 3.25

    def auto_scene(self) -> dict:
        if self._project is not None:
            self._project.camera.ae_enable = True
            self._project.camera.exposure_value = 0.0
            self._project.camera.af_mode = "auto_once"
            self._project.camera.lens_position = 2.4
            self._project.camera.exposure_time_us = 20000
            self._project.camera.analogue_gain = 1.5
        return {"lens_position": 2.4, "exposure_time": 20000, "analogue_gain": 1.5}


@pytest.mark.asyncio
async def test_start_after_stop_works(tmp_path):
    presets = Path(__file__).resolve().parents[1] / "config" / "presets"
    manager = ProjectManager(presets, tmp_path / "registry")
    project = manager.create(
        preset_id="life-activity",
        project_id="restart-test",
        name="重启测试",
        storage_root=str(tmp_path / "media"),
        overrides={"interval_sec": 0.2, "min_free_gb": 0.1},
    )
    manager.activate(project)
    camera = FakeCamera()
    runtime = CaptureRuntime(manager, camera)
    runtime.project = project

    await runtime.start()
    assert runtime.status == RuntimeStatus.CAPTURING
    await asyncio.sleep(0.5)
    await runtime.stop()
    assert runtime.status == RuntimeStatus.IDLE

    await runtime.start()
    assert runtime.status == RuntimeStatus.CAPTURING
    await asyncio.sleep(0.4)
    assert runtime.snapshot()["frames_total"] >= 1
    await runtime.stop()
    assert camera.started >= 2


@pytest.mark.asyncio
async def test_schedule_start_then_cancel(tmp_path):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    presets = Path(__file__).resolve().parents[1] / "config" / "presets"
    manager = ProjectManager(presets, tmp_path / "registry")
    project = manager.create(
        preset_id="life-activity",
        project_id="schedule-cancel",
        name="定时取消",
        storage_root=str(tmp_path / "media"),
        overrides={"interval_sec": 0.2, "min_free_gb": 0.1},
    )
    manager.activate(project)
    runtime = CaptureRuntime(manager, FakeCamera())
    runtime.project = project
    when = datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(seconds=30)
    await runtime.schedule_start(when)
    assert runtime.snapshot()["scheduled_start_at"] is not None
    assert runtime.snapshot()["scheduled_stop_at"] is None
    await runtime.cancel_schedule()
    assert runtime.snapshot()["scheduled_start_at"] is None
    assert runtime.snapshot()["scheduled_stop_at"] is None
    assert runtime.status == RuntimeStatus.IDLE


@pytest.mark.asyncio
async def test_schedule_start_fires(tmp_path):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    presets = Path(__file__).resolve().parents[1] / "config" / "presets"
    manager = ProjectManager(presets, tmp_path / "registry")
    project = manager.create(
        preset_id="life-activity",
        project_id="schedule-fire",
        name="定时开拍",
        storage_root=str(tmp_path / "media"),
        overrides={"interval_sec": 0.2, "min_free_gb": 0.1},
    )
    manager.activate(project)
    camera = FakeCamera()
    runtime = CaptureRuntime(manager, camera)
    runtime.project = project
    when = datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(seconds=0.4)
    await runtime.schedule_start(when)
    await asyncio.sleep(2.5)
    assert runtime.status == RuntimeStatus.CAPTURING
    assert camera.started >= 1
    assert runtime.snapshot()["scheduled_start_at"] is None
    assert runtime.snapshot()["scheduled_stop_at"] is None
    await runtime.stop()


@pytest.mark.asyncio
async def test_schedule_stop_fires(tmp_path):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    presets = Path(__file__).resolve().parents[1] / "config" / "presets"
    manager = ProjectManager(presets, tmp_path / "registry")
    project = manager.create(
        preset_id="life-activity",
        project_id="schedule-stop",
        name="定时停止",
        storage_root=str(tmp_path / "media"),
        overrides={"interval_sec": 0.2, "min_free_gb": 0.1},
    )
    manager.activate(project)
    camera = FakeCamera()
    runtime = CaptureRuntime(manager, camera)
    runtime.project = project
    when = datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(seconds=0.3)
    stop = when + timedelta(seconds=5.0)
    await runtime.schedule_start(when, stop)
    await asyncio.sleep(2.5)
    assert runtime.status == RuntimeStatus.CAPTURING
    await asyncio.sleep(4.0)
    assert runtime.status == RuntimeStatus.IDLE
    assert runtime.snapshot()["scheduled_stop_at"] is None
    await runtime.stop()


@pytest.mark.asyncio
async def test_clear_and_restart_deletes_frames(tmp_path):
    presets = Path(__file__).resolve().parents[1] / "config" / "presets"
    manager = ProjectManager(presets, tmp_path / "registry")
    project = manager.create(
        preset_id="life-activity",
        project_id="clear-test",
        name="清空测试",
        storage_root=str(tmp_path / "media"),
        overrides={"interval_sec": 0.2, "min_free_gb": 0.1},
    )
    manager.activate(project)
    frames = project.project_dir / "frames" / "2026-08-24"
    frames.mkdir(parents=True)
    (frames / "old.jpg").write_bytes(b"old")

    camera = FakeCamera()
    runtime = CaptureRuntime(manager, camera)
    runtime.project = project
    deleted = await runtime.clear_and_restart()
    assert deleted["frames"] == 1
    assert runtime.status == RuntimeStatus.CAPTURING
    await asyncio.sleep(0.4)
    assert runtime.snapshot()["frames_total"] >= 1
    assert not (frames / "old.jpg").exists()
    await runtime.stop()


@pytest.mark.asyncio
async def test_idle_live_preview_yields_safely_to_capture(tmp_path):
    presets = Path(__file__).resolve().parents[1] / "config" / "presets"
    manager = ProjectManager(presets, tmp_path / "registry")
    project = manager.create(
        preset_id="life-activity",
        project_id="live-preview",
        name="实时预览",
        storage_root=str(tmp_path / "media"),
        overrides={"interval_sec": 1, "min_free_gb": 0.1},
    )
    manager.activate(project)
    camera = FakeCamera()
    runtime = CaptureRuntime(manager, camera)
    runtime.project = project

    assert await runtime.live_preview_jpeg() == b"fake-jpeg"
    assert camera.started == 1
    assert camera.previews == 1

    await runtime.start()
    assert runtime.status == RuntimeStatus.CAPTURING
    assert camera.stopped >= 1
    assert camera.started == 2
    with pytest.raises(RuntimeError, match="最新已保存画面"):
        await runtime.live_preview_jpeg()
    assert camera.previews == 1
    await runtime.stop()


@pytest.mark.asyncio
async def test_camera_tune_commit_writes_contract(tmp_path):
    presets = Path(__file__).resolve().parents[1] / "config" / "presets"
    manager = ProjectManager(presets, tmp_path / "registry")
    project = manager.create(
        preset_id="life-activity",
        project_id="tune-test",
        name="调参测试",
        storage_root=str(tmp_path / "media"),
        overrides={"interval_sec": 0.5, "min_free_gb": 0.1},
    )
    manager.activate(project)
    camera = FakeCamera()
    runtime = CaptureRuntime(manager, camera)
    runtime.project = project
    await runtime.open_camera_tune()
    updated = project.camera.model_copy(
        update={
            "rotation": 180,
            "ae_enable": False,
            "exposure_time_us": 8000,
            "analogue_gain": 2.0,
        }
    )
    await runtime.apply_camera_tune(updated, jpeg_quality=88)
    assert camera.started == 2
    assert camera.stopped == 1
    assert runtime.camera_tune_state()["dirty"] is True
    await runtime.commit_camera_tune()
    saved = manager.get("tune-test")
    assert saved.camera.exposure_time_us == 8000
    assert saved.camera.ae_enable is False
    assert saved.camera.rotation == 180
    assert saved.capture.still_config.jpeg_quality == 88
    assert runtime.camera_tune_state()["dirty"] is False
    await runtime.close_camera_tune(discard=False)
    assert camera.stopped >= 1


@pytest.mark.asyncio
async def test_autofocus_lock_manual_keeps_mode(tmp_path):
    presets = Path(__file__).resolve().parents[1] / "config" / "presets"
    manager = ProjectManager(presets, tmp_path / "registry")
    project = manager.create(
        preset_id="life-activity",
        project_id="af-lock",
        name="手动对焦锁定",
        storage_root=str(tmp_path / "media"),
        overrides={"interval_sec": 0.5, "min_free_gb": 0.1},
    )
    manager.activate(project)
    camera = FakeCamera()
    runtime = CaptureRuntime(manager, camera)
    runtime.project = project
    await runtime.open_camera_tune()
    position = await runtime.autofocus(lock_manual=True)
    assert position == 3.25
    assert runtime.project.camera.af_mode == "manual"
    assert runtime.project.camera.lens_position == 3.25
    await runtime.close_camera_tune(discard=False)


@pytest.mark.asyncio
async def test_auto_scene_sets_focus_and_exposure(tmp_path):
    presets = Path(__file__).resolve().parents[1] / "config" / "presets"
    manager = ProjectManager(presets, tmp_path / "registry")
    project = manager.create(
        preset_id="life-activity",
        project_id="auto-scene",
        name="自动测光",
        storage_root=str(tmp_path / "media"),
        overrides={"interval_sec": 0.5, "min_free_gb": 0.1},
    )
    manager.activate(project)
    camera = FakeCamera()
    runtime = CaptureRuntime(manager, camera)
    runtime.project = project
    await runtime.open_camera_tune()
    state = await runtime.auto_scene()
    assert state["camera"]["ae_enable"] is True
    assert state["camera"]["af_mode"] == "auto_once"
    assert state["measured"]["exposure_time"] == 20000
    assert state["measured"]["lens_position"] == 2.4
    await runtime.close_camera_tune(discard=False)


@pytest.mark.asyncio
async def test_camera_tune_blocked_while_capturing(tmp_path):
    presets = Path(__file__).resolve().parents[1] / "config" / "presets"
    manager = ProjectManager(presets, tmp_path / "registry")
    project = manager.create(
        preset_id="life-activity",
        project_id="tune-block",
        name="调参拦截",
        storage_root=str(tmp_path / "media"),
        overrides={"interval_sec": 0.5, "min_free_gb": 0.1},
    )
    manager.activate(project)
    camera = FakeCamera()
    runtime = CaptureRuntime(manager, camera)
    runtime.project = project
    await runtime.start()
    assert runtime.status == RuntimeStatus.CAPTURING
    with pytest.raises(RuntimeError, match="拍摄进行中"):
        await runtime.open_camera_tune()
    await runtime.stop()
