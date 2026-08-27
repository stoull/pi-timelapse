import importlib
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image


def test_web_console_and_bootstrap(monkeypatch, tmp_path):
    monkeypatch.setenv("TIMELAPSE_STATE_DIR", str(tmp_path / "state"))
    sys.modules.pop("timelapse.web.app", None)
    web = importlib.import_module("timelapse.web.app")

    with TestClient(web.app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "时光延时" in page.text
        assert "相机设置" in page.text

        bootstrap = client.get("/api/bootstrap")
        assert bootstrap.status_code == 200
        body = bootstrap.json()
        assert len(body["presets"]) == 15
        assert body["runtime"]["status"] == "idle"
        assert "cpu_percent" in body["system"]
        assert "cpu_temp_c" in body["system"]
        assert "memory_used_bytes" in body["system"]
        assert "window_open" in body["runtime"]
        assert "window_label" in body["runtime"]

        storage_root = tmp_path / "media"
        created = client.post(
            "/api/projects",
            json={
                "preset_id": "life-room-day",
                "project_id": "gallery-test",
                "name": "画廊测试",
                "storage_root": str(storage_root),
            },
        )
        assert created.status_code == 201
        frame = (
            Path(storage_root)
            / "gallery-test"
            / "frames"
            / "2026-08-25"
            / "frame_20260825_120000_000.jpg"
        )
        frame.parent.mkdir(parents=True)
        Image.new("RGB", (640, 360), "green").save(frame)

        photos = client.get("/api/photos").json()
        assert photos["total"] == 1
        photo = photos["items"][0]
        assert client.get(photo["thumbnail_url"]).status_code == 200
        assert client.get(photo["full_url"]).status_code == 200

        deleted = client.request(
            "DELETE",
            "/api/photos",
            json={"photo_id": photo["id"]},
        )
        assert deleted.status_code == 200
        assert not frame.exists()

        other = frame.with_name("frame_20260825_120100_000.jpg")
        other.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (640, 360), "blue").save(other)
        batch = client.post(
            "/api/photos/delete",
            json={"photo_ids": ["2026-08-25/frame_20260825_120100_000.jpg"]},
        )
        assert batch.status_code == 200
        assert batch.json()["result"]["count"] == 1
        assert not other.exists()

        second = client.post(
            "/api/projects",
            json={
                "preset_id": "sky-cloud",
                "project_id": "detail-test",
                "name": "详情测试",
                "storage_root": str(storage_root),
                "interval_sec": 8,
            },
        )
        assert second.status_code == 201
        project_dir = storage_root / "detail-test"
        export = project_dir / "exports" / "clip.mp4"
        export.parent.mkdir(parents=True, exist_ok=True)
        export.write_bytes(b"fake-mp4")
        frame = project_dir / "frames" / "2026-08-25" / "frame_20260825_080000_000.jpg"
        frame.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (640, 360), "red").save(frame)
        later = frame.with_name("frame_20260825_090000_000.jpg")
        Image.new("RGB", (640, 360), "blue").save(later)

        detail = client.get("/api/projects/detail-test")
        assert detail.status_code == 200
        body = detail.json()
        assert body["is_active"] is True
        assert body["project"]["name"] == "详情测试"
        assert body["exports"][0]["filename"] == "clip.mp4"
        assert body["first_photo"]["id"].endswith("frame_20260825_080000_000.jpg")
        cover = client.get(body["first_photo"]["thumbnail_url"])
        assert cover.status_code == 200
        assert cover.headers["content-type"].startswith("image/")
        latest_thumb = client.get("/api/latest-thumbnail.jpg")
        assert latest_thumb.status_code == 200
        assert latest_thumb.headers["content-type"].startswith("image/")
        listed_projects = client.get("/api/bootstrap").json()["projects"]
        listed_detail = next(
            item for item in listed_projects if item["project_id"] == "detail-test"
        )
        assert listed_detail["first_photo"]["id"].endswith(
            "frame_20260825_080000_000.jpg"
        )

        exported = {}

        async def fake_export_start(project, watermark, *, fps=None):
            exported.update(
                project_id=project.project_id,
                watermark_type=watermark.type,
                fps=fps,
            )

        monkeypatch.setattr(web.encoder, "start", fake_export_start)
        started = client.post(
            "/api/export",
            json={"watermark_type": "none", "fps": 30},
        )
        assert started.status_code == 200
        assert exported == {
            "project_id": "detail-test",
            "watermark_type": "none",
            "fps": 30,
        }

        patched = client.patch("/api/projects/detail-test", json={"interval_sec": 15})
        assert patched.status_code == 200
        assert patched.json()["project"]["capture"]["interval_sec"] == 15

        listed = client.get("/api/projects/detail-test/exports")
        assert listed.status_code == 200
        assert listed.json()["exports"][0]["filename"] == "clip.mp4"
        downloaded = client.get("/api/projects/detail-test/exports/file", params={"name": "clip.mp4"})
        assert downloaded.status_code == 200
        assert downloaded.content == b"fake-mp4"

        gallery = client.get("/api/projects/gallery-test")
        assert gallery.status_code == 200
        assert gallery.json()["is_active"] is False
        assert gallery.json()["first_photo"] is None

        switched = client.post("/api/projects/switch", json={"project_id": "gallery-test"})
        assert switched.status_code == 200

        removed = client.delete("/api/projects/detail-test")
        assert removed.status_code == 200
        assert not project_dir.exists()
        assert client.get("/api/projects/detail-test").status_code == 404

        from datetime import datetime, timedelta

        when = (datetime.now() + timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M")
        scheduled = client.post("/api/capture/schedule", json={"start_at": when})
        assert scheduled.status_code == 200
        assert scheduled.json()["runtime"]["scheduled_start_at"]
        assert scheduled.json()["runtime"]["scheduled_stop_at"] is None
        cancelled = client.delete("/api/capture/schedule")
        assert cancelled.status_code == 200
        assert cancelled.json()["runtime"]["scheduled_start_at"] is None
        assert cancelled.json()["runtime"]["scheduled_stop_at"] is None

        from timelapse.camera import CameraService

        class WebFakeCamera(CameraService):
            def __init__(self):
                super().__init__()
                self.started = 0

            @property
            def available(self):
                return True

            def start(self, project, **_kwargs):
                self.started += 1
                self._project = project
                self._camera = object()

            def stop(self):
                self._camera = None

            def apply_controls(self, config):
                if self._project is not None:
                    self._project.camera = config

            def live_metadata(self):
                return {"exposure_time": 2000, "analogue_gain": 1.0, "lens_position": 1.0}

        web.runtime.camera = WebFakeCamera()
        session = client.post("/api/camera/tune/session")
        assert session.status_code == 200
        camera = session.json()["camera"]
        camera["ae_enable"] = False
        camera["exposure_time_us"] = 12000
        patched = client.patch(
            "/api/camera/tune",
            json={"camera": camera, "jpeg_quality": 91},
        )
        assert patched.status_code == 200
        assert patched.json()["dirty"] is True
        committed = client.post("/api/camera/tune/commit")
        assert committed.status_code == 200
        assert committed.json()["tune"]["dirty"] is False
        assert committed.json()["runtime"]["project"]["camera"]["exposure_time_us"] == 12000
        closed = client.post("/api/camera/tune/close", json={"discard": False})
        assert closed.status_code == 200
