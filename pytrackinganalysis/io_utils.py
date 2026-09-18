"""Filesystem helpers shared by every code path that rewrites a user's file.

``tracking_config.yaml`` is the single source of truth for a project, and the
settings file records the user's recent projects. Both were previously written
with a plain ``open(path, "w")``, which truncates the target before the first
byte is written — an exception, a full disk or a power loss mid-write left the
user with a half-serialised file and no copy of the original.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Callable, TextIO, Union


def atomic_write_text(path: Union[str, Path], render: Callable[[TextIO], None],
                      *, encoding: str = "utf-8", newline: Union[str, None] = None) -> None:
    """Write ``path`` via a temp file in the same directory plus ``os.replace``.

    ``render`` is called with an open text handle and should write the complete
    contents. The destination is only touched by the final atomic rename, so a
    failure anywhere in ``render`` leaves the existing file untouched.

    The temp file is created in the destination's directory (never the system
    temp dir) so the rename stays within one filesystem and is therefore atomic.

    ``newline`` is passed straight to the file object; the ``csv`` module wants
    ``""`` so the line terminator it writes is not translated on the way out.
    """
    path = Path(path)
    directory = path.parent if str(path.parent) else Path(".")
    directory.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=f".{path.name}.",
                                    suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline=newline) as handle:
            render(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
