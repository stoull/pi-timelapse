from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .config_schema import ProjectConfig


class ProjectError(RuntimeError):
    pass


class ProjectManager:
    def __init__(self, presets_dir: Path, registry_dir: Path) -> None:
        self.presets_dir = presets_dir
        self.registry_dir = registry_dir
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        self.active_file = self.registry_dir / "active_project"
        self.roots_file = self.registry_dir / "project_roots.yaml"

    def presets(self) -> list[dict[str, Any]]:
        result = []
        mode_order = {"sky": 0, "grow": 1, "life": 2}
        for path in sorted(self.presets_dir.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            result.append(
                {
                    "id": path.stem,
                    "name": data["name"],
                    "mode": data["mode"],
                    "interval_sec": data["capture"]["interval_sec"],
                    "summary": data.get("summary") or "",
                }
            )
        result.sort(key=lambda item: (mode_order.get(item["mode"], 9), item["name"]))
        return result

    def preset(self, preset_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[a-z0-9-]+", preset_id):
            raise ProjectError("无效预设")
        path = self.presets_dir / f"{preset_id}.yaml"
        if not path.is_file():
            raise ProjectError("预设不存在")
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def create(
        self,
        *,
        preset_id: str,
        project_id: str,
        name: str,
        storage_root: str,
        overrides: dict[str, Any] | None = None,
    ) -> ProjectConfig:
        data = self.preset(preset_id)
        data.pop("summary", None)
        overrides = overrides or {}
        data.update(
            {
                "project_id": project_id,
                "name": name,
                "preset": preset_id,
                "created_at": datetime.now().astimezone(),
                "storage": {
                    "root": storage_root,
                    "min_free_gb": overrides.get("min_free_gb", 5),
                    "mkdir_by": "day",
                },
            }
        )
        if "interval_sec" in overrides:
            data["capture"]["interval_sec"] = overrides["interval_sec"]
        if "resolution" in overrides:
            data["capture"]["still_config"]["main_size"] = overrides["resolution"]
        if "window_start" in overrides and data["window"]["type"] == "clock":
            data.setdefault("window", {}).setdefault("clock", {})["start"] = overrides[
                "window_start"
            ]
        if "window_end" in overrides and data["window"]["type"] == "clock":
            data.setdefault("window", {}).setdefault("clock", {})["end"] = overrides[
                "window_end"
            ]
        project = ProjectConfig.model_validate(data)
        if project.project_dir.exists():
            raise ProjectError("项目目录已存在")
        self._assert_storage(project.storage.root)
        for child in ("frames", "previews", "exports", "logs"):
            (project.project_dir / child).mkdir(parents=True, exist_ok=True)
        self._write_config(project)
        return project

    def _assert_storage(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".timelapse-write-test"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            raise ProjectError(f"存储目录不可写：{exc}") from exc

    def _write_config(self, project: ProjectConfig) -> None:
        path = project.project_dir / "project.yaml"
        tmp = path.with_suffix(".yaml.tmp")
        tmp.write_text(
            yaml.safe_dump(project.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        tmp.replace(path)

    def save(self, project: ProjectConfig) -> None:
        if not project.project_dir.exists():
            raise ProjectError("项目目录不存在")
        self._write_config(project)

    def list_projects(self) -> list[ProjectConfig]:
        result: list[ProjectConfig] = []
        roots = yaml.safe_load(self.roots_file.read_text()) if self.roots_file.exists() else []
        for raw_path in roots or []:
            path = Path(raw_path) / "project.yaml"
            if path.is_file():
                try:
                    result.append(ProjectConfig.model_validate(yaml.safe_load(path.read_text())))
                except Exception:
                    continue
        return sorted(result, key=lambda item: item.created_at, reverse=True)

    def register(self, project: ProjectConfig) -> None:
        roots = yaml.safe_load(self.roots_file.read_text()) if self.roots_file.exists() else []
        roots = list(dict.fromkeys([*(roots or []), str(project.project_dir)]))
        self.roots_file.write_text(yaml.safe_dump(roots), encoding="utf-8")

    def activate(self, project: ProjectConfig) -> None:
        self.register(project)
        self.active_file.write_text(str(project.project_dir / "project.yaml"), encoding="utf-8")

    def active(self) -> ProjectConfig | None:
        if not self.active_file.exists():
            return None
        path = Path(self.active_file.read_text(encoding="utf-8").strip())
        if not path.is_file():
            return None
        return ProjectConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))

    def get(self, project_id: str) -> ProjectConfig:
        for project in self.list_projects():
            if project.project_id == project_id:
                return project
        raise ProjectError("项目不存在")

    def unregister(self, project: ProjectConfig) -> None:
        roots = yaml.safe_load(self.roots_file.read_text()) if self.roots_file.exists() else []
        wanted = str(project.project_dir)
        roots = [item for item in (roots or []) if item != wanted]
        self.roots_file.write_text(yaml.safe_dump(roots), encoding="utf-8")

    def set_interval(self, project_id: str, interval_sec: float) -> ProjectConfig:
        project = self.get(project_id)
        project.capture.interval_sec = interval_sec
        self.save(project)
        return project

    def set_window(
        self,
        project_id: str,
        *,
        window_type: str,
        clock_start: str | None = None,
        clock_end: str | None = None,
    ) -> ProjectConfig:
        project = self.get(project_id)
        if window_type == "always":
            project.window.type = "always"
            project.window.clock = None
        elif window_type == "clock":
            start = clock_start or (project.window.clock.start if project.window.clock else "07:00")
            end = clock_end or (project.window.clock.end if project.window.clock else "23:00")
            from .config_schema import ClockWindow

            project.window.type = "clock"
            project.window.clock = ClockWindow(start=start, end=end)
        else:
            raise ProjectError("仅支持 always 或 clock 拍摄窗口")
        self.save(project)
        return project

    def delete(self, project_id: str) -> None:
        project = self.get(project_id)
        self.unregister(project)
        active = self.active()
        if active is not None and active.project_id == project_id:
            self.active_file.unlink(missing_ok=True)
        if project.project_dir.exists():
            shutil.rmtree(project.project_dir)
