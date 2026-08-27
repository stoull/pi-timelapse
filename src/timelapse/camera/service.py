from __future__ import annotations

import io
import logging
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional

from ..config_schema import CameraConfig, ProjectConfig

logger = logging.getLogger(__name__)


class CameraError(RuntimeError):
    pass


class CameraService:
    """Thread-safe Picamera2 adapter. Picamera2 is imported only on Raspberry Pi."""

    def __init__(self) -> None:
        self._camera: Any = None
        self._project: Optional[ProjectConfig] = None
        self._lock = threading.RLock()
        self._busy = threading.Event()
        self._close_thread: Optional[threading.Thread] = None

    def _wait_close(self, timeout: float = 6.0) -> None:
        thread = self._close_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    def _release_hardware(self) -> None:
        self._wait_close()
        with self._lock:
            camera = self._detach_locked()
        self._close_camera(camera)
        self._wait_close()
        self.abandon()

    @property
    def available(self) -> bool:
        try:
            import picamera2  # noqa: F401
        except ImportError:
            return False
        return True

    @property
    def is_running(self) -> bool:
        return self._camera is not None

    def start(self, project: ProjectConfig, *, skip_startup_af: bool = False) -> None:
        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                self._start_once(project, skip_startup_af=skip_startup_af)
                return
            except Exception as exc:
                last_error = exc
                logger.warning("camera start attempt %s failed: %s", attempt + 1, exc)
                self._release_hardware()
                if attempt < 2:
                    import time

                    time.sleep(1.5 * (attempt + 1))
        raise CameraError(f"相机启动失败：{last_error}") from last_error

    def _start_once(self, project: ProjectConfig, *, skip_startup_af: bool = False) -> None:
        self._release_hardware()
        camera = None
        try:
            with self._lock:
                from libcamera import Transform
                from picamera2 import Picamera2

                camera = Picamera2()
                still = project.capture.still_config
                camera.options["quality"] = still.jpeg_quality
                transform = {
                    0: Transform(),
                    180: Transform(hflip=True, vflip=True),
                }.get(int(project.camera.rotation), Transform())
                config = camera.create_still_configuration(
                    main={"size": tuple(still.main_size), "format": "RGB888"},
                    # Older libcamera on Bullseye requires YUV for lores.
                    lores={"size": (960, 540), "format": "YUV420"},
                    buffer_count=2,
                    transform=transform,
                )
                camera.configure(config)
                camera.start()
                self._camera = camera
                self._project = project
                self._apply_controls(project.camera)
                if project.camera.af_mode == "auto_once" and not skip_startup_af:
                    # Module 3 single AF once after the pipeline is live.
                    # Failure here must not leak the running camera.
                    try:
                        self._autofocus_cycle_locked()
                    except Exception as exc:
                        logger.warning("startup AF skipped: %s", exc)
        except Exception as exc:
            self._camera = None
            self._project = None
            self._close_camera(camera)
            raise

    def _enum(self, namespace: Any, name: str, member: str) -> Any:
        enum = getattr(namespace, name, None)
        if enum is None:
            return None
        return getattr(enum, member, None)

    def _apply_controls(self, config: CameraConfig) -> None:
        if self._camera is None:
            raise CameraError("相机未启动")
        try:
            from libcamera import controls

            values: dict[str, Any] = {
                "AeEnable": bool(config.ae_enable),
                "AwbEnable": bool(config.awb_enable),
                "Brightness": float(config.brightness),
                "Contrast": float(config.contrast),
                "Saturation": float(config.saturation),
                "Sharpness": float(config.sharpness),
            }
            if config.ae_enable:
                values["FrameDurationLimits"] = (100, 1_000_000)
                values["ExposureValue"] = float(config.exposure_value)
            else:
                exposure = int(config.exposure_time_us)
                values.update(
                    ExposureTime=exposure,
                    AnalogueGain=float(config.analogue_gain),
                    FrameDurationLimits=(exposure, max(exposure, 100_000)),
                )
            if not config.awb_enable:
                values["ColourGains"] = tuple(float(x) for x in config.colour_gains)
            else:
                awb = {
                    "auto": "Auto",
                    "tungsten": "Tungsten",
                    "fluorescent": "Fluorescent",
                    "indoor": "Indoor",
                    "daylight": "Daylight",
                    "cloudy": "Cloudy",
                }.get(config.awb_mode, "Auto")
                awb_enum = self._enum(controls, "AwbModeEnum", awb)
                if awb_enum is not None:
                    values["AwbMode"] = awb_enum
            if config.af_mode == "manual":
                values["AfMode"] = controls.AfModeEnum.Manual
                values["LensPosition"] = float(config.lens_position)
            else:
                values["AfMode"] = controls.AfModeEnum.Auto
            af_range = self._enum(
                controls,
                "AfRangeEnum",
                {"normal": "Normal", "macro": "Macro", "full": "Full"}[config.af_range],
            )
            if af_range is not None:
                values["AfRange"] = af_range
            af_speed = self._enum(
                controls,
                "AfSpeedEnum",
                {"normal": "Normal", "fast": "Fast"}[config.af_speed],
            )
            if af_speed is not None:
                values["AfSpeed"] = af_speed
            hdr = self._enum(
                controls,
                "HdrModeEnum",
                "Night" if config.hdr else "Off",
            )
            if hdr is not None:
                values["HdrMode"] = hdr
            self._set_controls_best_effort(values)
            if self._project is not None:
                self._project.camera = config
        except CameraError:
            raise
        except Exception as exc:
            raise CameraError(f"应用相机参数失败：{exc}") from exc

    def _set_controls_best_effort(self, values: dict[str, Any]) -> None:
        try:
            self._camera.set_controls(values)
            return
        except Exception:
            pass
        optional = {
            "HdrMode",
            "AfRange",
            "AfSpeed",
            "AwbMode",
            "ExposureValue",
            "Brightness",
            "Contrast",
            "Saturation",
            "Sharpness",
        }
        required = {key: value for key, value in values.items() if key not in optional}
        try:
            self._camera.set_controls(required)
        except Exception as exc:
            raise CameraError(f"应用相机参数失败：{exc}") from exc
        for key, value in values.items():
            if key in optional:
                try:
                    self._camera.set_controls({key: value})
                except Exception as exc:
                    logger.warning("camera control %s skipped: %s", key, exc)

    def apply_controls(self, config: CameraConfig) -> None:
        with self._lock:
            self._apply_controls(config)
            if self._camera is not None and self._project is not None:
                self._camera.options["quality"] = self._project.capture.still_config.jpeg_quality

    def live_metadata(self) -> dict[str, Any]:
        with self._lock:
            if self._camera is None:
                return {}
            try:
                metadata = self._camera.capture_metadata() or {}
            except Exception:
                return {}
            gains = metadata.get("ColourGains")
            return {
                "exposure_time": metadata.get("ExposureTime"),
                "analogue_gain": metadata.get("AnalogueGain"),
                "lens_position": metadata.get("LensPosition"),
                "colour_gains": list(gains) if gains else None,
                "lux": metadata.get("Lux"),
                "ae_locked": metadata.get("AeLocked"),
            }

    def _autofocus_cycle_locked(self) -> float:
        """Run one Module 3 AF cycle; keep AfMode=Auto (do not lock to manual)."""
        if self._camera is None:
            raise CameraError("相机未启动")
        from libcamera import controls

        self._camera.set_controls({"AfMode": controls.AfModeEnum.Auto})
        success = self._camera.autofocus_cycle()
        metadata = self._camera.capture_metadata()
        if not success:
            raise CameraError("自动对焦未成功")
        position = float(metadata.get("LensPosition") or 0.0)
        # Stay in Auto so later single-AF triggers remain available.
        self._camera.set_controls({"AfMode": controls.AfModeEnum.Auto})
        return position

    def capture(self, path: Path) -> dict[str, Any]:
        if not self._lock.acquire(timeout=8):
            raise CameraError("相机忙（无法获得拍摄锁）")
        self._busy.set()
        try:
            if self._camera is None or self._project is None:
                raise CameraError("相机未启动")
            path.parent.mkdir(parents=True, exist_ok=True)
            # Capture to local disk first so a slow/NTFS destination cannot
            # stall libcamera while a request is outstanding.
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as handle:
                tmp_local = Path(handle.name)
            try:
                if self._project.camera.af_mode == "auto_once":
                    try:
                        self._autofocus_cycle_locked()
                    except CameraError as exc:
                        logger.warning("capture-time AF skipped: %s", exc)
                request = self._camera.capture_request()
                metadata = request.get_metadata()
                request.save("main", str(tmp_local))
                request.release()
                shutil.move(str(tmp_local), str(path))
                return {
                    "exposure_time": metadata.get("ExposureTime"),
                    "analogue_gain": metadata.get("AnalogueGain"),
                    "lens_position": metadata.get("LensPosition"),
                }
            except Exception as exc:
                tmp_local.unlink(missing_ok=True)
                if isinstance(exc, CameraError):
                    raise
                raise CameraError(f"拍摄失败：{exc}") from exc
        finally:
            self._busy.clear()
            self._lock.release()

    def preview_jpeg(self) -> bytes:
        if self._busy.is_set():
            raise CameraError("相机正在拍摄")
        if not self._lock.acquire(timeout=0.4):
            raise CameraError("相机忙")
        try:
            if self._camera is None:
                raise CameraError("相机未启动")
            try:
                stream = io.BytesIO()
                # Preview can use a lighter JPEG to keep the tuning panel responsive.
                previous_quality = self._camera.options.get("quality")
                self._camera.options["quality"] = min(int(previous_quality or 90), 75)
                try:
                    # lores is YUV420 on this stack and cannot be JPEG-encoded by PIL;
                    # use the main still stream for occasional live previews only.
                    self._camera.capture_file(stream, format="jpeg", name="main")
                finally:
                    if previous_quality is not None:
                        self._camera.options["quality"] = previous_quality
                return stream.getvalue()
            except Exception as exc:
                raise CameraError(f"预览失败：{exc}") from exc
        finally:
            self._lock.release()

    def autofocus_once(self, lock_manual: bool = False) -> float:
        with self._lock:
            try:
                position = self._autofocus_cycle_locked()
                if lock_manual:
                    from libcamera import controls

                    self._camera.set_controls(
                        {
                            "AfMode": controls.AfModeEnum.Manual,
                            "LensPosition": float(position),
                        }
                    )
                    if self._project is not None:
                        self._project.camera.af_mode = "manual"
                        self._project.camera.lens_position = position
                return position
            except CameraError:
                raise
            except Exception as exc:
                raise CameraError(f"自动对焦失败：{exc}") from exc

    def auto_scene(self) -> dict[str, Any]:
        """Enable AE + AF for the current view and return measured values."""
        with self._lock:
            if self._camera is None:
                raise CameraError("相机未启动")
            from libcamera import controls
            import time

            self._camera.set_controls(
                {
                    "AeEnable": True,
                    "ExposureValue": 0.0,
                    "AfMode": controls.AfModeEnum.Auto,
                    "FrameDurationLimits": (100, 1_000_000),
                }
            )
            try:
                position = self._autofocus_cycle_locked()
            except CameraError as exc:
                logger.warning("auto-scene AF skipped: %s", exc)
                metadata = self._camera.capture_metadata() or {}
                position = float(metadata.get("LensPosition") or 0.0)
            time.sleep(0.8)
            metadata = self._camera.capture_metadata() or {}
            exposure = int(metadata.get("ExposureTime") or 33333)
            gain = float(metadata.get("AnalogueGain") or 1.0)
            position = float(metadata.get("LensPosition") or position)
            exposure = max(100, min(exposure, 10_000_000))
            gain = max(1.0, min(gain, 16.0))
            position = max(0.0, min(position, 32.0))
            if self._project is not None:
                camera = self._project.camera
                camera.ae_enable = True
                camera.exposure_value = 0.0
                camera.af_mode = "auto_once"
                camera.lens_position = position
                camera.exposure_time_us = exposure
                camera.analogue_gain = gain
            return {
                "lens_position": position,
                "exposure_time": exposure,
                "analogue_gain": gain,
            }

    def reopen(self) -> None:
        project = self._project
        if project is None:
            raise CameraError("没有可恢复的项目")
        self.stop()
        self.start(project)

    def _detach_locked(self) -> Any:
        camera = self._camera
        self._camera = None
        self._project = None
        return camera

    @staticmethod
    def _close_camera(camera: Any) -> None:
        if camera is None:
            return
        try:
            camera.stop()
        except Exception as exc:
            logger.warning("camera.stop failed: %s", exc)
        try:
            camera.close()
        except Exception as exc:
            logger.warning("camera.close failed: %s", exc)

    def stop(self) -> None:
        """Detach and close camera without holding the lock during hardware teardown."""
        with self._lock:
            camera = self._detach_locked()
        if camera is None:
            self._wait_close()
            return
        closer = threading.Thread(
            target=self._close_camera,
            args=(camera,),
            name="camera-close",
            daemon=True,
        )
        self._close_thread = closer
        closer.start()
        closer.join(timeout=6.0)
        if self._close_thread is closer:
            self._close_thread = None

    def abandon(self) -> None:
        """Drop camera handle without waiting for a clean stop (last resort)."""
        self._camera = None
        self._project = None
        self._busy.clear()
