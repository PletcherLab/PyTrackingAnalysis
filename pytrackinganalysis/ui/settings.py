"""Persisted UI preferences (theme mode, recent projects).

Stored as JSON at ``$XDG_CONFIG_HOME/pytrackinganalysis/ui.json`` (by
default ``~/.config/pytrackinganalysis/ui.json``).  No external deps;
failures are silent so the apps keep working even when the config dir is
unwritable.

The location is worked out on every call rather than once at import, so a
caller that points ``XDG_CONFIG_HOME`` elsewhere — the test suite does, for
every test — is obeyed whenever this module was first imported.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..io_utils import atomic_write_text

def config_dir() -> Path:
    """Where the preferences live. An empty ``XDG_CONFIG_HOME`` counts as
    unset, as the XDG rule says — never as the current directory."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "pytrackinganalysis"


def config_file() -> Path:
    return config_dir() / "ui.json"

_DEFAULTS: dict[str, Any] = {
    "theme": "auto",
    "recent_projects": [],
}


def load() -> dict[str, Any]:
    path = config_file()
    if not path.is_file():
        return dict(_DEFAULTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return dict(_DEFAULTS)
        merged = dict(_DEFAULTS)
        merged.update(data)
        return merged
    except Exception:  # noqa: BLE001
        return dict(_DEFAULTS)


def save(data: dict[str, Any]) -> None:
    # Written through a temp file + rename: a plain write truncates the file
    # first, so an interruption used to leave a half-written ui.json that
    # load() then silently replaced with the bare defaults, losing the theme
    # and every recent project.
    try:
        atomic_write_text(
            config_file(),
            lambda handle: handle.write(json.dumps(data, indent=2, sort_keys=True)),
        )
    except Exception:  # noqa: BLE001
        pass


def get(key: str, default: Any = None) -> Any:
    return load().get(key, default if default is not None else _DEFAULTS.get(key))


def set_value(key: str, value: Any) -> None:
    data = load()
    data[key] = value
    save(data)


def add_recent_project(path: str | Path, *, max_items: int = 8) -> None:
    p = str(Path(path).expanduser().resolve())
    data = load()
    recents = [r for r in data.get("recent_projects", []) if r != p]
    recents.insert(0, p)
    data["recent_projects"] = recents[:max_items]
    save(data)
