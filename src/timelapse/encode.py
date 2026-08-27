from __future__ import annotations

import asyncio
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from .config_schema import ProjectConfig


class EncodeError(RuntimeError):
    pass


WATERMARK_POSITIONS = {
    "top_left": 7,
    "top_right": 9,
    "bottom_left": 1,
    "bottom_right": 3,
    "center": 5,
    "top_center": 8,
    "bottom_center": 2,
    "top": 8,
    "bottom": 2,
}

WatermarkPosition = Literal[
    "top_left",
    "top_right",
    "bottom_left",
    "bottom_right",
    "center",
    "top_center",
    "bottom_center",
    "top",
    "bottom",
]


@dataclass(frozen=True)
class WatermarkOptions:
    type: Literal["none", "timestamp", "text"] = "none"
    timestamp_format: Literal["datetime", "time"] = "datetime"
    position: WatermarkPosition = "bottom_center"
    text: str = ""

    def validate(self) -> None:
        if self.type == "text":
            value = self.text.strip()
            if not value:
                raise EncodeError("请输入水印文本")
            if len(value) > 50:
                raise EncodeError("水印文本最多 50 个字符")


_FRAME_TIME = re.compile(r"frame_(\d{8})_(\d{6})_(\d{3})\.jpg$", re.IGNORECASE)


def _frame_timestamp(path: Path, timestamp_format: str) -> str:
    match = _FRAME_TIME.search(path.name)
    if not match:
        modified = datetime.fromtimestamp(path.stat().st_mtime)
        return modified.strftime("%H:%M" if timestamp_format == "time" else "%Y-%m-%d %H:%M")
    captured = datetime.strptime(f"{match.group(1)}{match.group(2)}", "%Y%m%d%H%M%S")
    return captured.strftime("%H:%M" if timestamp_format == "time" else "%Y-%m-%d %H:%M")


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    whole_seconds, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{fraction:02d}"


def _ass_text(value: str) -> str:
    return value.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", " ")


def _watermark_font_size(height: int, watermark_type: str) -> int:
    base = max(14, round(height * 0.018))
    if watermark_type == "text":
        return base * 4
    if watermark_type == "timestamp":
        return base * 2
    return base


def _write_watermark_ass(
    path: Path,
    frames: list[Path],
    fps: int,
    height: int,
    options: WatermarkOptions,
) -> None:
    font_size = _watermark_font_size(height, options.type)
    pad = max(10, round(height * 0.018))
    play_res_x = max(2, round(height * 16 / 9 / 2) * 2)
    position = options.position
    alignment = WATERMARK_POSITIONS.get(position, 2)
    margin_l = pad if position in {"top_left", "bottom_left"} else 10
    margin_r = pad if position in {"top_right", "bottom_right"} else 10
    margin_v = 0 if position == "center" else pad
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {height}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Watermark,DejaVu Sans,{font_size},&HCCFFFFFF,&H000000FF,&H99000000,&H66000000,0,0,0,0,100,100,0,0,1,1,0,{alignment},{margin_l},{margin_r},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines: list[str] = []
    duration = 1 / fps
    if options.type == "text":
        lines.append(
            f"Dialogue: 0,{_ass_time(0)},{_ass_time(len(frames) * duration)},Watermark,,0,0,0,,{_ass_text(options.text.strip())}\n"
        )
    elif options.type == "timestamp":
        for index, frame in enumerate(frames):
            lines.append(
                f"Dialogue: 0,{_ass_time(index * duration)},{_ass_time((index + 1) * duration)},Watermark,,0,0,0,,"
                f"{_frame_timestamp(frame, options.timestamp_format)}\n"
            )
    path.write_text(header + "".join(lines), encoding="utf-8")


def _filter_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'")


class EncodeManager:
    def __init__(self) -> None:
        self.status = "idle"
        self.error: str | None = None
        self.output: Path | None = None
        self.frame_count = 0
        self._task: asyncio.Task[None] | None = None

    async def start(
        self,
        project: ProjectConfig,
        watermark: WatermarkOptions | None = None,
        *,
        fps: int | None = None,
    ) -> None:
        if self._task and not self._task.done():
            raise EncodeError("已有视频正在导出")
        if shutil.which("ffmpeg") is None:
            raise EncodeError("未安装 ffmpeg")
        options = watermark or WatermarkOptions()
        options.validate()
        export_fps = fps or project.encode.fps
        if export_fps not in {24, 25, 30}:
            raise EncodeError("导出帧率仅支持 24、25 或 30 fps")
        frames = sorted((project.project_dir / "frames").glob("**/*.jpg"))
        if not frames:
            raise EncodeError("项目还没有可导出的照片")
        self.frame_count = len(frames)
        self.status = "encoding"
        self.error = None
        self.output = None
        self._task = asyncio.create_task(
            self._encode(project, frames, options, export_fps), name="encode-job"
        )

    async def _encode(
        self,
        project: ProjectConfig,
        frames: list[Path],
        watermark: WatermarkOptions,
        fps: int,
    ) -> None:
        exports = project.project_dir / "exports"
        exports.mkdir(parents=True, exist_ok=True)
        output = exports / f"{project.project_id}_{datetime.now():%Y%m%d_%H%M%S}.mp4"
        manifest = exports / ".frames.txt"
        watermark_file = exports / ".watermark.ass"
        duration = 1 / fps

        def quote(path: Path) -> str:
            return str(path.resolve()).replace("'", "'\\''")

        manifest.write_text(
            "".join(f"file '{quote(path)}'\nduration {duration:.8f}\n" for path in frames)
            + f"file '{quote(frames[-1])}'\n",
            encoding="utf-8",
        )
        filters = [f"scale=-2:{project.encode.height}"]
        if watermark.type != "none":
            _write_watermark_ass(
                watermark_file,
                frames,
                fps,
                project.encode.height,
                watermark,
            )
            filters.append(f"ass='{_filter_path(watermark_file)}'")
        filters.append("format=yuv420p")
        command = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(manifest),
            "-vf",
            ",".join(filters),
            "-r",
            str(fps),
            "-c:v",
            "libx264",
            "-crf",
            str(project.encode.crf),
            "-movflags",
            "+faststart",
            str(output),
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()
            if process.returncode:
                detail = stderr.decode("utf-8", errors="replace")[-2000:]
                raise EncodeError(f"ffmpeg 导出失败：{detail}")
            self.output = output
            self.status = "done"
        except Exception as exc:
            self.status = "failed"
            self.error = str(exc)
        finally:
            manifest.unlink(missing_ok=True)
            watermark_file.unlink(missing_ok=True)

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "error": self.error,
            "frame_count": self.frame_count,
            "filename": self.output.name if self.output else None,
        }
