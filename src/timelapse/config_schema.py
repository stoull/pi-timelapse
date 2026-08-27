from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator, model_validator


class Mode(str, Enum):
    SKY = "sky"
    GROW = "grow"
    LIFE = "life"


class StillConfig(BaseModel):
    main_size: tuple[int, int] = (2304, 1296)
    jpeg_quality: int = Field(92, ge=70, le=100)

    @field_validator("main_size")
    @classmethod
    def validate_size(cls, value: tuple[int, int]) -> tuple[int, int]:
        if value not in {(1920, 1080), (2304, 1296), (3840, 2160), (4608, 2592)}:
            raise ValueError("Camera Module 3 不支持该分辨率")
        return value


class CaptureConfig(BaseModel):
    interval_sec: float = Field(gt=0, le=86400)
    still_config: StillConfig = Field(default_factory=StillConfig)


class ClockWindow(BaseModel):
    start: str = "07:00"
    end: str = "23:00"

    @field_validator("start", "end")
    @classmethod
    def validate_clock(cls, value: str) -> str:
        try:
            datetime.strptime(value, "%H:%M")
        except ValueError as exc:
            raise ValueError("时间必须使用 HH:MM") from exc
        return value


class SolarWindow(BaseModel):
    start: Literal["sunrise", "sunset"] = "sunrise"
    start_offset_min: int = -20
    end: Literal["sunrise", "sunset"] = "sunset"
    end_offset_min: int = 20


class WindowConfig(BaseModel):
    type: Literal["always", "clock", "solar", "event"] = "always"
    clock: Optional[ClockWindow] = None
    solar: Optional[SolarWindow] = None
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    timezone: str = "Asia/Shanghai"

    @model_validator(mode="after")
    def validate_detail(self) -> "WindowConfig":
        try:
            ZoneInfo(self.timezone)
        except Exception as exc:
            raise ValueError("无效时区") from exc
        if self.type == "clock" and self.clock is None:
            raise ValueError("clock 窗口缺少 clock 配置")
        if self.type == "solar" and (
            self.solar is None or self.latitude is None or self.longitude is None
        ):
            raise ValueError("solar 窗口需要经纬度和 solar 配置")
        return self


class CameraConfig(BaseModel):
    lens: Literal["standard", "wide"] = "standard"
    rotation: Literal[0, 180] = 0
    af_mode: Literal["manual", "auto_once"] = "auto_once"
    lens_position: float = Field(0.0, ge=0, le=32)
    af_range: Literal["normal", "macro", "full"] = "full"
    af_speed: Literal["normal", "fast"] = "normal"
    ae_enable: bool = True
    exposure_time_us: int = Field(2000, ge=100, le=10_000_000)
    analogue_gain: float = Field(1.0, ge=1, le=16)
    exposure_value: float = Field(0.0, ge=-8, le=8)
    awb_enable: bool = False
    awb_mode: Literal["auto", "tungsten", "fluorescent", "indoor", "daylight", "cloudy"] = "auto"
    colour_gains: tuple[float, float] = (1.6, 1.5)
    brightness: float = Field(0.0, ge=-1, le=1)
    contrast: float = Field(1.0, ge=0, le=32)
    saturation: float = Field(1.0, ge=0, le=32)
    sharpness: float = Field(1.0, ge=0, le=16)
    hdr: bool = False
    camera_led: bool = False

    @field_validator("rotation", mode="before")
    @classmethod
    def only_invert_or_upright(cls, value: object) -> int:
        try:
            angle = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0
        if angle in {90, 270}:
            return 0
        return angle


class StorageConfig(BaseModel):
    root: Path = Path("/mnt/ssd/timelapse")
    min_free_gb: float = Field(5, ge=0.1)
    mkdir_by: Literal["none", "day"] = "day"

    @field_validator("root")
    @classmethod
    def safe_root(cls, value: Path) -> Path:
        value = value.expanduser()
        if not value.is_absolute():
            raise ValueError("存储位置必须是绝对路径")
        if value in {Path("/"), Path("/etc"), Path("/usr"), Path("/boot")}:
            raise ValueError("禁止使用系统关键目录")
        return value


class EncodeConfig(BaseModel):
    fps: Literal[24, 25, 30] = 24
    height: Literal[720, 1080, 2160] = 1080
    crf: int = Field(18, ge=12, le=32)


class ProjectConfig(BaseModel):
    schema_version: int = 1
    project_id: str
    name: str
    mode: Mode
    preset: str
    created_at: datetime = Field(default_factory=datetime.now)
    capture: CaptureConfig
    window: WindowConfig
    camera: CameraConfig = Field(default_factory=CameraConfig)
    camera_night: Optional[CameraConfig] = None
    night_switch_at: Optional[str] = None
    storage: StorageConfig = Field(default_factory=StorageConfig)
    encode: EncodeConfig = Field(default_factory=EncodeConfig)

    @field_validator("project_id")
    @classmethod
    def safe_id(cls, value: str) -> str:
        if not value or not all(ch.isalnum() or ch in "-_" for ch in value):
            raise ValueError("项目 ID 只能包含字母、数字、- 和 _")
        return value

    @model_validator(mode="after")
    def mode_rules(self) -> "ProjectConfig":
        if self.camera_night and not self.night_switch_at:
            raise ValueError("夜景参数缺少切换时刻")
        if self.night_switch_at:
            datetime.strptime(self.night_switch_at, "%H:%M")
        return self

    @property
    def project_dir(self) -> Path:
        return self.storage.root / self.project_id
