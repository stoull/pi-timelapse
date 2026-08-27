from pathlib import Path

import yaml
from pydantic import ValidationError
import pytest

from timelapse.config_schema import ProjectConfig
from timelapse.project import ProjectManager

PRESETS = Path(__file__).resolve().parents[1] / "config" / "presets"


def project_data() -> dict:
    return {
        "project_id": "test-project",
        "name": "测试",
        "mode": "life",
        "preset": "life-activity",
        "capture": {"interval_sec": 2},
        "window": {"type": "event", "timezone": "Asia/Shanghai"},
        "camera": {},
        "storage": {"root": "/mnt/ssd/timelapse"},
    }


def test_life_event_config_is_valid():
    project = ProjectConfig.model_validate(project_data())
    assert project.mode == "life"
    assert project.capture.still_config.main_size == (2304, 1296)


def test_life_always_window_is_allowed():
    data = project_data()
    data["window"]["type"] = "always"
    project = ProjectConfig.model_validate(data)
    assert project.window.type == "always"


def test_camera_rotation_only_allows_upright_or_inverted():
    data = project_data()
    data["camera"]["rotation"] = 180
    assert ProjectConfig.model_validate(data).camera.rotation == 180
    data["camera"]["rotation"] = 90
    assert ProjectConfig.model_validate(data).camera.rotation == 0
    data["camera"]["rotation"] = 45
    with pytest.raises(ValidationError):
        ProjectConfig.model_validate(data)


@pytest.mark.parametrize("root", ["/", "/etc", "relative/path"])
def test_dangerous_storage_path_is_rejected(root):
    data = project_data()
    data["storage"]["root"] = root
    with pytest.raises(ValidationError):
        ProjectConfig.model_validate(data)


def test_all_presets_include_camera_settings():
    manager = ProjectManager(PRESETS, Path("/tmp/unused-registry"))
    names = {item["id"]: item["name"] for item in manager.presets()}
    assert "sky-sunset" in names
    assert "sky-night" in names
    assert "life-traffic" in names
    assert "grow-bloom" in names
    for path in PRESETS.glob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data.pop("summary", None)
        data.update(
            {
                "project_id": f"preset-{path.stem}",
                "preset": path.stem,
                "created_at": "2026-08-27T12:00:00+08:00",
                "storage": {"root": "/mnt/ssd/timelapse"},
            }
        )
        project = ProjectConfig.model_validate(data)
        camera = project.camera
        assert camera.af_mode in {"manual", "auto_once"}
        assert camera.awb_mode
        assert camera.exposure_time_us >= 100
        assert 1.0 <= camera.analogue_gain <= 16
