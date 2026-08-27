from datetime import datetime
from zoneinfo import ZoneInfo

from PIL import Image

from timelapse.config_schema import ProjectConfig
from timelapse.storage import (
    clear_project_media,
    count_frames,
    create_thumbnail,
    delete_photo,
    export_file,
    frame_path,
    first_photo,
    latest_frame,
    list_exports,
    list_photos,
)


def make_project(tmp_path) -> ProjectConfig:
    return ProjectConfig.model_validate(
        {
            "project_id": "storage-test",
            "name": "存储测试",
            "mode": "sky",
            "preset": "sky-cloud",
            "capture": {"interval_sec": 8},
            "window": {"type": "always"},
            "camera": {},
            "storage": {"root": str(tmp_path)},
        }
    )


def test_frame_path_is_sorted_and_partitioned_by_day(tmp_path):
    project = make_project(tmp_path)
    tz = ZoneInfo("Asia/Shanghai")
    first = frame_path(project, datetime(2026, 8, 23, 9, 1, 2, 123000, tzinfo=tz))
    second = frame_path(project, datetime(2026, 8, 23, 9, 1, 3, 123000, tzinfo=tz))
    assert first.parent.name == "2026-08-23"
    assert first.name < second.name

    first.parent.mkdir(parents=True)
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    assert count_frames(project) == 2
    assert latest_frame(project) == second
    assert first_photo(project) == first


def test_clear_project_media_removes_frames_and_keeps_contract(tmp_path):
    project = make_project(tmp_path)
    frames = project.project_dir / "frames" / "2026-08-23"
    frames.mkdir(parents=True)
    (frames / "frame_1.jpg").write_bytes(b"a")
    (project.project_dir / "previews").mkdir(parents=True)
    (project.project_dir / "previews" / "test.jpg").write_bytes(b"b")
    (project.project_dir / "thumbnails").mkdir(parents=True)
    (project.project_dir / "thumbnails" / "thumb.jpg").write_bytes(b"t")
    (project.project_dir / "exports").mkdir(parents=True)
    (project.project_dir / "exports" / "out.mp4").write_bytes(b"c")
    (project.project_dir / "project.yaml").write_text("ok", encoding="utf-8")

    deleted = clear_project_media(project)
    assert deleted["frames"] == 1
    assert deleted["thumbnails"] == 1
    assert deleted["previews"] == 1
    assert deleted["exports"] == 1
    assert count_frames(project) == 0
    assert (project.project_dir / "project.yaml").is_file()
    assert (project.project_dir / "frames").is_dir()


def test_list_and_resolve_export_files(tmp_path):
    project = make_project(tmp_path)
    folder = project.project_dir / "exports"
    folder.mkdir(parents=True)
    clip = folder / "out.mp4"
    clip.write_bytes(b"mp4")
    (folder / "notes.txt").write_text("ignore", encoding="utf-8")
    items = list_exports(project)
    assert [item["filename"] for item in items] == ["out.mp4"]
    assert export_file(project, "out.mp4") == clip.resolve()


def test_thumbnail_listing_and_delete_are_kept_in_sync(tmp_path):
    project = make_project(tmp_path)
    day = project.project_dir / "frames" / "2026-08-23"
    day.mkdir(parents=True)
    first = day / "frame_20260823_090100_000.jpg"
    second = day / "frame_20260823_090200_000.jpg"
    Image.new("RGB", (1920, 1080), "red").save(first)
    Image.new("RGB", (1920, 1080), "blue").save(second)

    thumbnail = create_thumbnail(project, second)
    assert thumbnail.is_file()
    with Image.open(thumbnail) as image:
        assert image.width <= 320
        assert image.height <= 180

    page = list_photos(project, limit=1)
    assert page["total"] == 2
    assert page["has_more"] is True
    assert page["items"][0]["id"] == "2026-08-23/frame_20260823_090200_000.jpg"
    assert page["items"][0]["has_thumbnail"] is True

    from timelapse.storage import delete_photos

    result = delete_photos(project, [page["items"][0]["id"], "missing.jpg"])
    assert result["count"] == 1
    assert len(result["errors"]) == 1
    assert not second.exists()
    assert not thumbnail.exists()
    assert count_frames(project) == 1


def test_list_photos_filters_by_five_minute_window(tmp_path):
    project = make_project(tmp_path)
    day = project.project_dir / "frames" / "2026-08-23"
    day.mkdir(parents=True)
    (day / "frame_20260823_090000_000.jpg").write_bytes(b"a")
    (day / "frame_20260823_090400_000.jpg").write_bytes(b"b")
    (day / "frame_20260823_091000_000.jpg").write_bytes(b"c")

    page = list_photos(
        project,
        start=datetime(2026, 8, 23, 9, 0),
        end=datetime(2026, 8, 23, 9, 5),
    )
    assert page["total"] == 2
    assert [item["filename"] for item in page["items"]] == [
        "frame_20260823_090400_000.jpg",
        "frame_20260823_090000_000.jpg",
    ]
    assert page["range"]["start_min"] == "2026-08-23T09:00"
    assert page["range"]["end_max"] == "2026-08-23T09:10"
