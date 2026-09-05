from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class Config:
    history_limit: int = 50000
    ignore_patterns: list[str] = field(
        default_factory=lambda: [r"^\s*tmem(?:\s|$)", r"^\s*tmem-core(?:\s|$)"]
    )


def data_dir() -> Path:
    override = os.environ.get("TMEM_DATA_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg).expanduser() / "tmem"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "tmem"
    return Path.home() / ".local/share/tmem"


def config_dir() -> Path:
    override = os.environ.get("TMEM_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser() / "tmem"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "tmem"
    return Path.home() / ".config/tmem"


def db_path() -> Path:
    override = os.environ.get("TMEM_DB")
    return Path(override).expanduser() if override else data_dir() / "tmem.db"


def load_config() -> Config:
    path = config_dir() / "config.json"
    if not path.exists():
        return Config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"Could not read config {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in config {path}: {error}") from error
    if not isinstance(raw, dict):
        raise ValueError(f"Config {path} must contain a JSON object")

    config = Config()
    if "history_limit" in raw:
        limit = raw["history_limit"]
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("history_limit must be a positive integer")
        config.history_limit = limit
    if "ignore_patterns" in raw:
        patterns = raw["ignore_patterns"]
        if not isinstance(patterns, list) or not all(isinstance(item, str) for item in patterns):
            raise ValueError("ignore_patterns must be a list of strings")
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as error:
                raise ValueError(f"Invalid ignore pattern {pattern!r}: {error}") from error
        config.ignore_patterns = patterns
    return config
