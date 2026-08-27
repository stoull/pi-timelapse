from __future__ import annotations

import argparse
import os
from pathlib import Path


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="timelapse", description="Pi 延时摄影控制系统")
    commands = root.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="启动 Web 控制台")
    serve.add_argument("--host", default=os.environ.get("TIMELAPSE_HOST", "0.0.0.0"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("TIMELAPSE_PORT", "8080")))
    serve.add_argument(
        "--state-dir",
        default=os.environ.get("TIMELAPSE_STATE_DIR", "/var/lib/pi-timelapse"),
    )
    commands.add_parser("check-camera", help="检查 Camera Module 3 与 Picamera2")
    return root


def main() -> None:
    args = parser().parse_args()
    if args.command == "serve":
        os.environ["TIMELAPSE_STATE_DIR"] = str(Path(args.state_dir).resolve())
        import uvicorn

        uvicorn.run("timelapse.web.app:app", host=args.host, port=args.port)
        return
    try:
        from picamera2 import Picamera2

        cameras = Picamera2.global_camera_info()
    except Exception as exc:
        raise SystemExit(f"相机检查失败：{exc}") from exc
    if not cameras:
        raise SystemExit("未检测到相机")
    for camera in cameras:
        print(camera)


if __name__ == "__main__":
    main()
