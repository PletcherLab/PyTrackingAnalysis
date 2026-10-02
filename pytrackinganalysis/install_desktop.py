"""Install freedesktop launcher entries + icons for the PyTrackingAnalysis apps.

On Linux (especially Wayland/GNOME) the taskbar icon comes from a matching
``.desktop`` file rather than the Qt window icon, so without this step the
apps show a generic placeholder. Run once per environment:

    pytrack-install-desktop

Writes the badge PNGs to ``~/.local/share/icons/hicolor`` and one ``.desktop``
entry per app to ``~/.local/share/applications``. Re-run after moving the
project or recreating the virtualenv (the entries embed absolute paths).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .gui_env import sanitize_input_method_environment

_APPS = [
    ("pytrack-hub", "PyTrackingAnalysis Hub",
     "Load experiments, run analyses, view plots"),
    ("pytrack-config", "PyTrackingAnalysis Config Editor",
     "Edit tracking_config.yaml and saved script recipes"),
    ("pytrack-qc", "PyTrackingAnalysis QC Viewer",
     "Per-tracker data-quality tables and plots"),
    ("pytrack-plots", "PyTrackingAnalysis Plot Editor",
     "Style and export publication figures"),
]

_ICON_NAME = "pytrackinganalysis"

# Characters the freedesktop spec reserves inside an Exec= field. Any argument
# containing one of them has to be quoted, or a venv living under e.g.
# "~/My Projects/" produces an entry that launches nothing.
_EXEC_RESERVED = set(' \t\n"\'\\><~|&;$*?#()`')


def _exec_arg(value: str) -> str:
    """Quote *value* for a ``.desktop`` ``Exec=`` field.

    The spec asks for double quotes, with backslash-escapes for ``"``, ``\\``,
    ``$`` and a backtick inside them.
    """
    if not value:
        return '""'
    if not (_EXEC_RESERVED & set(value)):
        return value
    escaped = value
    for ch in ('\\', '"', '$', '`'):
        escaped = escaped.replace(ch, '\\' + ch)
    return f'"{escaped}"'


def main() -> int:
    sanitize_input_method_environment()
    # Render icons without needing a display.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    _ = QApplication.instance() or QApplication([])
    from .ui.icons import render_app_badge

    icon_root = Path.home() / ".local/share/icons/hicolor"
    apps_dir = Path.home() / ".local/share/applications"
    apps_dir.mkdir(parents=True, exist_ok=True)

    for size in (48, 128, 256):
        d = icon_root / f"{size}x{size}" / "apps"
        d.mkdir(parents=True, exist_ok=True)
        out = d / f"{_ICON_NAME}.png"
        render_app_badge(size).save(str(out), "PNG")
        print(f"Wrote {out}")

    for cmd, name, comment in _APPS:
        exe = shutil.which(cmd) or str(Path(sys.executable).with_name(cmd))
        entry = apps_dir / f"{cmd}.desktop"
        entry.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={name}\n"
            f"Comment={comment}\n"
            f"Exec={_exec_arg(exe)} %f\n"
            f"Icon={_ICON_NAME}\n"
            "Terminal=false\n"
            "Categories=Science;Education;\n"
            f"StartupWMClass={cmd}\n",
            encoding="utf-8",
        )
        print(f"Wrote {entry}")

    # Best-effort cache refresh; harmless if the tools aren't installed.
    for refresh in (
        ["update-desktop-database", str(apps_dir)],
        ["gtk-update-icon-cache", "-f", "-t", str(icon_root)],
    ):
        try:
            subprocess.run(refresh, check=False, capture_output=True)
        except FileNotFoundError:
            pass

    print("Done. If the taskbar icon doesn't update immediately, log out and back in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
