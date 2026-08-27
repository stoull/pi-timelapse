from pathlib import Path

import pytest

from timelapse.project import ProjectError, ProjectManager


PRESETS = Path(__file__).resolve().parents[1] / "config" / "presets"


def test_create_register_and_activate_project(tmp_path):
    manager = ProjectManager(PRESETS, tmp_path / "registry")
    project = manager.create(
        preset_id="life-activity",
        project_id="kitchen-test",
        name="厨房测试",
        storage_root=str(tmp_path / "media"),
        overrides={"interval_sec": 3, "resolution": [1920, 1080]},
    )
    manager.register(project)
    manager.activate(project)

    assert project.project_dir.joinpath("project.yaml").is_file()
    assert project.project_dir.joinpath("frames").is_dir()
    assert manager.active().project_id == "kitchen-test"
    assert manager.get("kitchen-test").capture.interval_sec == 3


def test_duplicate_project_directory_is_rejected(tmp_path):
    manager = ProjectManager(PRESETS, tmp_path / "registry")
    args = {
        "preset_id": "sky-cloud",
        "project_id": "duplicate",
        "name": "重复",
        "storage_root": str(tmp_path / "media"),
    }
    manager.create(**args)
    with pytest.raises(ProjectError, match="项目目录已存在"):
        manager.create(**args)


def test_set_interval_and_delete_project(tmp_path):
    manager = ProjectManager(PRESETS, tmp_path / "registry")
    project = manager.create(
        preset_id="life-activity",
        project_id="to-delete",
        name="待删除",
        storage_root=str(tmp_path / "media"),
        overrides={"interval_sec": 5},
    )
    manager.activate(project)
    (project.project_dir / "frames" / "keep.jpg").parent.mkdir(parents=True, exist_ok=True)
    (project.project_dir / "frames" / "keep.jpg").write_bytes(b"x")

    updated = manager.set_interval("to-delete", 12)
    assert updated.capture.interval_sec == 12
    assert manager.get("to-delete").capture.interval_sec == 12

    manager.delete("to-delete")
    assert manager.active() is None
    assert not project.project_dir.exists()
    with pytest.raises(ProjectError, match="项目不存在"):
        manager.get("to-delete")


def test_create_applies_preset_camera_settings(tmp_path):
    manager = ProjectManager(PRESETS, tmp_path / "registry")
    project = manager.create(
        preset_id="sky-night",
        project_id="night-sky",
        name="星空",
        storage_root=str(tmp_path / "media"),
    )
    assert project.camera.af_mode == "manual"
    assert project.camera.ae_enable is False
    assert project.camera.exposure_time_us == 1_000_000
    assert project.camera.analogue_gain == 6
    traffic = manager.create(
        preset_id="life-traffic",
        project_id="traffic",
        name="车流",
        storage_root=str(tmp_path / "media"),
    )
    assert traffic.camera.exposure_time_us == 400_000
    assert traffic.camera.ae_enable is False
