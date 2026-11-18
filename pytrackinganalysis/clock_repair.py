"""Repair a DTrack export whose recording clock was set back mid-run.

DTrack stamps every row from the tracking PC's system clock: ``Time`` and
``Millisec`` are the wall-clock reading and ``MSec`` is that reading relative to
the start of the recording. ``Frame`` is a counter and knows nothing about the
clock. If the clock is adjusted while a recording is running — a time-sync
service correcting a time-zone or DST mismatch is the usual culprit — every
clock-derived column jumps backwards by the size of the adjustment while
``Frame`` carries on incrementing.

NOMPc/F-max1 (recorded 2024-07-29) is the reference case: the clock was set
back by exactly one hour at 11:35:47, twice, one hour apart. Elapsed minutes
went negative, the 10–70 min Exposure phase contained no rows at all, the
Valence rule discarded all 36 flies for "fewer than 3 transitions during
Exposure", and QC died inside seaborn with "Number of rows must be a positive
integer, not 0".

Only *elapsed* time matters to the analysis, so the repair adds the lost time
back to every row from each rollback onwards — ``MSec``, ``Time`` and
``Millisec`` together, nothing else touched. The lost time is measured as the
backward step plus one typical frame interval, then snapped to a whole number
of hours when it is within :data:`HOUR_SNAP_TOLERANCE_MS` of one: a correction
of exactly N hours is what a time-zone mismatch produces, and the snapped value
is then exact rather than one frame-interval estimate away from it.

The loader refuses an export with a rollback in it
(:func:`raise_if_rolled_back`), naming the frames and pointing here. Repairing
the files in place, with the originals kept beside ``data/``, keeps what is on
disk the single source of truth: the analysis never quietly disagrees with what
a spreadsheet would show for the same file.

A recording read through ``data_folder:`` (ADR-0018) is never rewritten in
place — it may be locked, and other analyses may read it. The **out-of-place**
mode (``out_dir=`` / ``--out``) writes a complete repaired copy of the data
folder somewhere new instead, leaves the originals untouched, and the
replicate is pointed at the copy. It works for any replicate.

Command line::

    python -m pytrackinganalysis.clock_repair <experiment or data directory> [--dry-run]
    python -m pytrackinganalysis.clock_repair <experiment or data directory> --out <new folder>
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
import pandas as pd
from natsort import natsorted

from .io_utils import atomic_write_text

HOUR_MS = 3_600_000.0

#: A backward step smaller than this is timestamp jitter, not a rollback.
ROLLBACK_TOLERANCE_MS = 1_000.0

#: Lost time this close to a whole number of hours is taken to be exactly that.
HOUR_SNAP_TOLERANCE_MS = 2_000.0

#: The files a DTrack export spreads its rows over — the pattern Arena loads.
DATA_GLOB = "*_Data_*.csv"

#: Where the untouched originals go, beside ``data/``.
DEFAULT_BACKUP_DIRNAME = "data_original"

#: The notes an out-of-place repair leaves in its copy.
README_NAME = "README.txt"
#: ...or under this name, when the export already carries a README.txt that
#: must be copied byte for byte like every other file.
FALLBACK_README_NAME = "README_clock_repair.txt"

#: The out-of-place option, as every refusal and the loader's error spell it.
OUT_OF_PLACE_HINT = ("    python -m pytrackinganalysis.clock_repair "
                     "<experiment directory> --out <new folder>")

#: ``Time`` column layouts we know how to shift. Each pairs the ``strptime``
#: format with a renderer that reproduces the original padding (DTrack writes
#: ``7/29/2024 11:27:47`` — no zero padding on month, day or hour).
_TIME_LAYOUTS: tuple[tuple[str, Callable[[datetime], str]], ...] = (
    ("%m/%d/%Y %H:%M:%S",
     lambda t: f"{t.month}/{t.day}/{t.year} {t.hour}:{t.minute:02d}:{t.second:02d}"),
    ("%Y-%m-%d %H:%M:%S",
     lambda t: t.strftime("%Y-%m-%d %H:%M:%S")),
)


class ClockRollbackError(ValueError):
    """The recording clock was set back mid-run; see this module's docstring."""


@dataclass(frozen=True)
class Rollback:
    """One backward jump of the recording clock."""

    frame: int
    """First frame stamped with the rolled-back clock."""
    previous_frame: int
    """Last frame before the jump."""
    msec_before: float
    msec_after: float
    step_ms: float
    """Typical interval between consecutive frames in this recording."""
    lost_ms: float
    """How far the clock went back — the measured step plus one frame interval."""
    offset_ms: float
    """What the repair adds to every row from ``frame`` onwards."""
    snapped_hours: int | None
    """Whole hours ``offset_ms`` was snapped to, or None when it is ``lost_ms``."""

    def describe(self) -> str:
        if self.snapped_hours is not None:
            how = f"snapped to {self.snapped_hours} h"
        else:
            how = "not a whole number of hours, so the measured value is used"
        return (f"frame {self.previous_frame} -> {self.frame}: clock went back "
                f"{self.lost_ms / 60_000:.2f} min ({how}); "
                f"adding {self.offset_ms / 60_000:.2f} min from frame {self.frame} on")


@dataclass
class RepairReport:
    data_dir: Path
    files: list[Path]
    rollbacks: list[Rollback]
    dry_run: bool
    backup_dir: Path | None = None
    out_dir: Path | None = None
    """Where an out-of-place repair wrote its copy; None in place."""
    rows_shifted: int = 0
    minutes_before: float = 0.0
    """Elapsed minutes the export claims as recorded (last minus first frame)."""
    minutes_after: float = 0.0
    """Elapsed minutes once the lost time is put back."""
    messages: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _typical_step_ms(msec: np.ndarray) -> float:
    steps = np.diff(msec)
    positive = steps[steps > 0]
    return float(np.median(positive)) if len(positive) else 0.0


def snap_to_hours(lost_ms: float, tolerance_ms: float = HOUR_SNAP_TOLERANCE_MS):
    """``(offset_ms, hours)``: *lost_ms* rounded to whole hours when it is within
    *tolerance_ms* of one, otherwise unchanged with ``hours`` None."""
    hours = int(round(lost_ms / HOUR_MS))
    if hours >= 1 and abs(lost_ms - hours * HOUR_MS) <= tolerance_ms:
        return hours * HOUR_MS, hours
    return float(lost_ms), None


def find_rollbacks(frames, msec, tolerance_ms: float = ROLLBACK_TOLERANCE_MS) -> list[Rollback]:
    """Backward clock steps in a recording.

    *frames* and *msec* are parallel sequences in recording order — one
    tracker's rows, or the per-frame series of a whole export. A step counts
    as a rollback when the clock moves back by more than *tolerance_ms*.
    """
    frames = np.asarray(frames)
    msec = np.asarray(msec, dtype=float)
    if len(msec) < 2:
        return []
    step_ms = _typical_step_ms(msec)
    found = []
    for i in np.flatnonzero(np.diff(msec) < -tolerance_ms) + 1:
        lost_ms = float(msec[i - 1] - msec[i]) + step_ms
        offset_ms, hours = snap_to_hours(lost_ms)
        found.append(Rollback(
            frame=int(frames[i]), previous_frame=int(frames[i - 1]),
            msec_before=float(msec[i - 1]), msec_after=float(msec[i]),
            step_ms=step_ms, lost_ms=lost_ms, offset_ms=offset_ms,
            snapped_hours=hours,
        ))
    return found


def raise_if_rolled_back(name: str, frames, msec,
                         tolerance_ms: float = ROLLBACK_TOLERANCE_MS) -> None:
    """Refuse a tracker or counter whose clock ran backwards, legibly.

    Called from ``calculate_minutes`` while the symptom is still recognisable.
    Left to run, a rollback surfaces much later as an empty phase, a wholesale
    exclusion and a matplotlib error about a grid with zero rows.
    """
    rollbacks = find_rollbacks(frames, msec, tolerance_ms)
    if not rollbacks:
        return
    where = "; ".join(f"{r.lost_ms / 60_000:.1f} min at frame {r.frame}" for r in rollbacks)
    raise ClockRollbackError(
        f"{name}: the recording clock ran backwards {len(rollbacks)} time(s) ({where}). "
        "Elapsed time comes from the export's clock columns, so every frame after "
        "a rollback looks earlier than it is: phases empty out and flies are "
        "excluded for no reason. Put the lost time back (the originals are kept) with:\n"
        "    python -m pytrackinganalysis.clock_repair <experiment directory>\n"
        "or, to leave the originals untouched — the only way for an experiment "
        "that reads its recording through data_folder: — write a repaired copy "
        "elsewhere and point data_folder: at it:\n" + OUT_OF_PLACE_HINT
    )


# ---------------------------------------------------------------------------
# Repair
# ---------------------------------------------------------------------------

def _referenced_location(target):
    """The data location when *target* is an Experiment Directory that names
    its data folder with ``data_folder:``; None for a data folder itself or
    a directory that reads its own ``data/``."""
    from . import layout   # lazy: the trackers import this module

    if export_files(target):
        return None
    location = layout.data_location(target)
    return location if location.referenced else None


def resolve_data_dir(target) -> Path:
    """Accept an Experiment Directory or its data folder: ``data/`` (any
    case), or the folder its ``data_folder:`` names — the loader's own
    answer (ADR-0018)."""
    from . import layout   # lazy: the trackers import this module

    target = Path(target)
    if not target.is_dir():
        raise FileNotFoundError(f"Not a directory: {target}")
    if export_files(target):
        return target
    location = layout.data_location(target)
    if location.problem:
        raise layout.DataLocationError(location.problem)
    if os.path.isdir(location.path):
        return Path(location.path)
    raise FileNotFoundError(
        f"No DTrack export ({DATA_GLOB}) in {target} or a data/ folder beneath it.")


def export_files(data_dir) -> list[Path]:
    pattern = os.path.join(glob.escape(str(data_dir)), DATA_GLOB)
    return [Path(p) for p in natsorted(glob.glob(pattern))]


def load_frame_clock(files: Sequence[Path]) -> pd.DataFrame:
    """One row per frame — ``Frame`` and ``MSec`` — across the whole export.

    Every tracker in a frame carries the same stamp, so a disagreement inside a
    frame means something other than a clock adjustment happened and the
    export is left alone.
    """
    parts = [pd.read_csv(path, usecols=["Frame", "MSec"]) for path in files]
    rows = pd.concat(parts, ignore_index=True)
    per_frame = rows.groupby("Frame")["MSec"].agg(["min", "max"])
    spread = per_frame["max"] - per_frame["min"]
    if (spread > 1.0).any():
        examples = per_frame.index[spread > 1.0][:5].tolist()
        raise ValueError(
            "MSec differs between trackers within a frame (for example at frames "
            f"{examples}); that is not a clock rollback and is not repaired automatically.")
    clock = per_frame["min"].rename("MSec").reset_index()
    return clock.sort_values("Frame").reset_index(drop=True)


def offset_for_frame(rollbacks: Sequence[Rollback]) -> Callable[[int], float]:
    """The cumulative milliseconds to add to a row stamped at a given frame."""
    starts = np.array([r.frame for r in rollbacks], dtype=float)
    cumulative = np.cumsum([r.offset_ms for r in rollbacks])

    def offset(frame: int) -> float:
        k = int(np.searchsorted(starts, frame, side="right"))
        return float(cumulative[k - 1]) if k else 0.0

    return offset


def _format_msec(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _parse_time(text: str):
    """``(datetime, renderer)`` for a ``Time`` cell, trying each known layout."""
    for layout, render in _TIME_LAYOUTS:
        try:
            return datetime.strptime(text.strip(), layout), render
        except ValueError:
            continue
    raise ValueError(
        f"Cannot parse the Time value {text!r}; rerun with --msec-only to shift "
        "MSec and leave Time/Millisec as recorded.")


def _shift_stamp(time_text: str, millisec_text: str, offset_ms: float) -> tuple[str, str]:
    stamp, render = _parse_time(time_text)
    stamp += timedelta(milliseconds=int(float(millisec_text or 0)))
    shifted = stamp + timedelta(milliseconds=offset_ms)
    return render(shifted), str(shifted.microsecond // 1000)


def _line_terminator(path: Path) -> str:
    with open(path, "rb") as handle:
        first = handle.readline()
    return "\r\n" if first.endswith(b"\r\n") else "\n"


def rewrite_file(path: Path, offset: Callable[[int], float], *, msec_only: bool = False) -> int:
    """Apply *offset* to the clock columns of one export file, in place.

    Rows before the first rollback are written back byte-for-byte; only
    ``MSec`` (and ``Time``/``Millisec`` unless *msec_only*) change on the rest.
    Returns the number of rows shifted.
    """
    terminator = _line_terminator(path)
    with open(path, "r", newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        return 0
    header = rows[0]
    try:
        frame_col = header.index("Frame")
        msec_col = header.index("MSec")
    except ValueError as err:
        raise ValueError(f"{path.name}: no Frame/MSec columns ({err}).") from err
    shift_clock = (not msec_only) and "Time" in header and "Millisec" in header
    time_col = header.index("Time") if shift_clock else -1
    ms_col = header.index("Millisec") if shift_clock else -1

    shifted = 0
    stamps: dict[tuple[str, str, float], tuple[str, str]] = {}
    for row in rows[1:]:
        if not row:
            continue
        add = offset(int(float(row[frame_col])))
        if not add:
            continue
        row[msec_col] = _format_msec(float(row[msec_col]) + add)
        if shift_clock:
            key = (row[time_col], row[ms_col], add)
            if key not in stamps:
                stamps[key] = _shift_stamp(row[time_col], row[ms_col], add)
            row[time_col], row[ms_col] = stamps[key]
        shifted += 1

    def render(handle):
        csv.writer(handle, lineterminator=terminator).writerows(rows)

    atomic_write_text(path, render, newline="")
    return shifted


def backup_originals(files: Sequence[Path], backup_dir: Path, report: RepairReport) -> None:
    """Copy *files* into *backup_dir*, refusing to overwrite an earlier backup."""
    if backup_dir.exists() and any(backup_dir.iterdir()):
        raise FileExistsError(
            f"{backup_dir} already exists and is not empty. It may hold the true "
            "originals from an earlier repair, so nothing was changed. Move it "
            "away, or pass --backup-dir to use another location.")
    backup_dir.mkdir(parents=True, exist_ok=True)
    for path in files:
        shutil.copy2(path, backup_dir / path.name)
    lines = [
        "Untouched DTrack export files, copied here by pytrackinganalysis.clock_repair",
        f"on {datetime.now():%Y-%m-%d %H:%M} before the files in data/ were repaired.",
        "",
        "The recording clock was set back mid-run:",
        *(f"  - {r.describe()}" for r in report.rollbacks),
        "",
        "In data/, MSec, Time and Millisec were shifted forward by the lost time",
        "from each rollback onwards; every other column is unchanged.",
        "To restore the originals, copy these CSV files back over data/.",
        "",
    ]
    (backup_dir / "README.txt").write_text("\n".join(lines), encoding="utf-8")


def _inside(path, folder) -> bool:
    """*path* is *folder* or lies beneath it, compared as the OS resolves them."""
    path = os.path.normcase(os.path.realpath(path))
    folder = os.path.normcase(os.path.realpath(folder))
    try:
        return os.path.commonpath([path, folder]) == folder
    except ValueError:                      # different drives
        return False


def _check_out_dir(out_dir: Path, data_dir: Path) -> None:
    """Refuse an out-of-place destination before anything is read or written."""
    if _inside(out_dir, data_dir):
        raise ValueError(
            f"{out_dir} is inside the data folder {data_dir}. The repaired copy "
            "must go somewhere else: nothing is ever written at a data location.")
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        raise FileExistsError(
            f"{out_dir} already exists and is not empty, so nothing was written. "
            "Name a new folder, or an empty one.")


def _copy_tree(source: Path, destination: Path) -> None:
    """Copy every file under *source* into *destination*, byte for byte.

    Contents and modification times only — not permission bits, so a copy of
    a locked recording is an ordinary, writable folder.
    """
    for directory, _dirs, names in os.walk(source):
        here = destination / os.path.relpath(directory, source)
        here.mkdir(parents=True, exist_ok=True)
        for name in names:
            original = os.path.join(directory, name)
            copied = here / name
            shutil.copyfile(original, copied)
            status = os.stat(original)
            os.utime(copied, ns=(status.st_atime_ns, status.st_mtime_ns))


def write_repaired_copy(data_dir: Path, out_dir: Path, offset, report: RepairReport,
                        *, msec_only: bool = False) -> None:
    """Copy the whole data folder into *out_dir*, then repair the copies.

    Every file is copied byte for byte; the export files are then rewritten
    exactly as an in-place repair rewrites them. The originals are only read,
    so no backup is made.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    _copy_tree(data_dir, out_dir)
    for path in report.files:
        shifted = rewrite_file(out_dir / path.name, offset, msec_only=msec_only)
        report.rows_shifted += shifted
        report.messages.append(f"  rewrote {path.name}: {shifted} row(s) shifted")
    readme = README_NAME if not (data_dir / README_NAME).exists() \
        else FALLBACK_README_NAME
    lines = [
        "A repaired copy of a DTrack export, written by",
        f"pytrackinganalysis.clock_repair on {datetime.now():%Y-%m-%d %H:%M}.",
        "",
        "The source was left untouched:",
        f"  {data_dir}",
        "",
        "The recording clock was set back mid-run:",
        *(f"  - {r.describe()}" for r in report.rollbacks),
        "",
        f"In the {DATA_GLOB} files here, MSec, Time and Millisec were shifted",
        "forward by the lost time from each rollback onwards. Every other column,",
        "and every other file, is an exact copy of the source.",
        "To analyse this copy, point the experiment's data_folder: at this folder.",
        "",
    ]
    (out_dir / readme).write_text("\n".join(lines), encoding="utf-8")


def repair_export(target, *, backup_dir=None, out_dir=None, dry_run: bool = False,
                  msec_only: bool = False,
                  tolerance_ms: float = ROLLBACK_TOLERANCE_MS,
                  log: Callable[[str], None] | None = print) -> RepairReport:
    """Find the rollbacks in an export and put the lost time back.

    *target* is an Experiment Directory or its data folder. Nothing is
    written when there is no rollback or when *dry_run* is set.

    In place (the default), the originals are copied to *backup_dir* (default
    ``<experiment>/data_original``) before any file is rewritten. An
    Experiment Directory that reads its recording through ``data_folder:`` is
    refused: that recording is only ever read (ADR-0018).

    Out of place (*out_dir*), a complete repaired copy of the data folder is
    written to *out_dir*, which must not exist yet or must be empty; the
    originals are untouched, so there is no backup. Works for any target.
    """
    if out_dir is not None and backup_dir is not None:
        raise ValueError("backup_dir and out_dir do not mix: an out-of-place "
                         "repair leaves the originals untouched, so it makes "
                         "no backup.")
    referenced = _referenced_location(Path(target)) if Path(target).is_dir() else None
    if referenced is not None and out_dir is None and not dry_run:
        raise ValueError(
            f"{target} reads its recording from data_folder: "
            f"{referenced.written} ({referenced.path}), and clock repair never "
            "rewrites a referenced recording in place — nothing was changed. "
            "Write a repaired copy elsewhere and point data_folder: at it:\n"
            + OUT_OF_PLACE_HINT.replace("<experiment directory>", str(target)))
    data_dir = resolve_data_dir(target)
    if out_dir is not None:
        out_dir = Path(out_dir)
        _check_out_dir(out_dir, data_dir)
    files = export_files(data_dir)
    clock = load_frame_clock(files)
    frames = clock["Frame"].to_numpy()
    msec = clock["MSec"].to_numpy()
    rollbacks = find_rollbacks(frames, msec, tolerance_ms)
    report = RepairReport(data_dir=data_dir, files=files, rollbacks=rollbacks, dry_run=dry_run)

    def say(message: str) -> None:
        report.messages.append(message)
        if log is not None:
            log(message)

    say(f"Export: {data_dir} — {len(files)} file(s), frames {frames.min()}–{frames.max()}")
    report.minutes_before = float(msec[-1] - msec[0]) / 60_000
    if not rollbacks:
        report.minutes_after = report.minutes_before
        say("The recording clock never ran backwards; nothing to repair.")
        return report

    offset = offset_for_frame(rollbacks)
    corrected = msec + np.array([offset(int(f)) for f in frames])
    report.minutes_after = float(corrected[-1] - corrected[0]) / 60_000
    say(f"Found {len(rollbacks)} rollback(s):")
    for rollback in rollbacks:
        say("  " + rollback.describe())
    say(f"Elapsed time: {report.minutes_before:.1f} min as recorded, "
        f"{report.minutes_after:.1f} min after repair.")
    if dry_run:
        say("Dry run: no files changed.")
        return report

    if out_dir is not None:
        before = len(report.messages)
        write_repaired_copy(data_dir, out_dir, offset, report, msec_only=msec_only)
        report.out_dir = out_dir
        if log is not None:
            for message in report.messages[before:]:
                log(message)
        say(f"Done: {report.rows_shifted} row(s) shifted across {len(files)} "
            f"file(s), written to {out_dir}. The originals in {data_dir} are "
            "untouched.")
        return report

    backup_dir = Path(backup_dir) if backup_dir else data_dir.parent / DEFAULT_BACKUP_DIRNAME
    backup_originals(files, backup_dir, report)
    report.backup_dir = backup_dir
    say(f"Originals copied to {backup_dir}")
    for path in files:
        shifted = rewrite_file(path, offset, msec_only=msec_only)
        report.rows_shifted += shifted
        say(f"  rewrote {path.name}: {shifted} row(s) shifted")
    say(f"Done: {report.rows_shifted} row(s) shifted across {len(files)} file(s).")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pytrackinganalysis.clock_repair",
        description="Put back the time a DTrack export lost when the recording "
                    "clock was set back mid-run. The originals are kept beside data/, "
                    "or, with --out, left untouched while a repaired copy is "
                    "written elsewhere.")
    parser.add_argument("target", help="Experiment Directory (or its data/ folder)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the rollbacks and change nothing")
    parser.add_argument("--backup-dir",
                        help=f"where the originals go (default: <experiment>/{DEFAULT_BACKUP_DIRNAME})")
    parser.add_argument("--out", metavar="FOLDER",
                        help="write a complete repaired copy of the data folder "
                             "here (a new or empty folder) and leave the originals "
                             "untouched; the only way to repair a recording read "
                             "through data_folder:")
    parser.add_argument("--msec-only", action="store_true",
                        help="shift MSec only; leave Time/Millisec as recorded")
    parser.add_argument("--tolerance-ms", type=float, default=ROLLBACK_TOLERANCE_MS,
                        help="smallest backward step treated as a rollback "
                             f"(default {ROLLBACK_TOLERANCE_MS:g} ms)")
    args = parser.parse_args(argv)
    if args.out and args.backup_dir:
        parser.error("--out and --backup-dir do not mix: --out leaves the "
                     "originals untouched, so there is nothing to back up")
    try:
        repair_export(args.target, backup_dir=args.backup_dir, out_dir=args.out,
                      dry_run=args.dry_run, msec_only=args.msec_only,
                      tolerance_ms=args.tolerance_ms)
    except (OSError, ValueError) as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
