from pathlib import Path

import pytest

from timelapse.encode import (
    EncodeError,
    WatermarkOptions,
    _frame_timestamp,
    _write_watermark_ass,
)


def test_timestamp_comes_from_frame_filename(tmp_path):
    frame = tmp_path / "frame_20260826_080012_123.jpg"
    frame.write_bytes(b"jpeg")
    assert _frame_timestamp(frame, "datetime") == "2026-08-26 08:00"
    assert _frame_timestamp(frame, "time") == "08:00"


def test_timestamp_ass_contains_one_label_per_frame(tmp_path):
    frames = [
        tmp_path / "frame_20260826_080000_000.jpg",
        tmp_path / "frame_20260826_080100_000.jpg",
    ]
    for frame in frames:
        frame.write_bytes(b"jpeg")
    output = tmp_path / "watermark.ass"
    _write_watermark_ass(
        output,
        frames,
        fps=24,
        height=1080,
        options=WatermarkOptions(type="timestamp", timestamp_format="datetime"),
    )
    content = output.read_text(encoding="utf-8")
    assert "2026-08-26 08:00" in content
    assert "2026-08-26 08:01" in content
    assert content.count("Dialogue:") == 2
    assert ",2,10,10," in content


def test_watermark_positions_use_ass_numpad_alignment(tmp_path):
    frame = tmp_path / "frame_20260826_080000_000.jpg"
    frame.write_bytes(b"jpeg")
    cases = {
        "top_left": ",7,19,10,19,",
        "top_right": ",9,10,19,19,",
        "bottom_left": ",1,19,10,19,",
        "bottom_right": ",3,10,19,19,",
        "center": ",5,10,10,0,",
        "top_center": ",8,10,10,19,",
        "bottom_center": ",2,10,10,19,",
    }
    for position, token in cases.items():
        output = tmp_path / f"{position}.ass"
        _write_watermark_ass(
            output,
            [frame],
            fps=24,
            height=1080,
            options=WatermarkOptions(type="text", text="测试", position=position),
        )
        assert token in output.read_text(encoding="utf-8")


def test_watermark_font_sizes(tmp_path):
    frame = tmp_path / "frame_20260826_080000_000.jpg"
    frame.write_bytes(b"jpeg")
    base = max(14, round(1080 * 0.018))
    timestamp_size = base * 2
    timestamp_out = tmp_path / "timestamp.ass"
    text_out = tmp_path / "text.ass"
    _write_watermark_ass(
        timestamp_out,
        [frame],
        fps=24,
        height=1080,
        options=WatermarkOptions(type="timestamp", timestamp_format="time"),
    )
    _write_watermark_ass(
        text_out,
        [frame],
        fps=24,
        height=1080,
        options=WatermarkOptions(type="text", text="延时摄影"),
    )
    timestamp_text = timestamp_out.read_text(encoding="utf-8")
    assert f",{timestamp_size},&HCCFFFFFF," in timestamp_text
    assert "PlayResX: 1920" in timestamp_text
    assert f",{base * 4},&HCCFFFFFF," in text_out.read_text(encoding="utf-8")


def test_text_watermark_is_limited_to_50_characters():
    WatermarkOptions(type="text", text="延时摄影").validate()
    with pytest.raises(EncodeError, match="最多 50"):
        WatermarkOptions(type="text", text="字" * 51).validate()

