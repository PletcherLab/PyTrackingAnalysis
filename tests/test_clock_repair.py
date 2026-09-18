"""Detection and repair of a recording clock that was set back mid-run."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from conftest import FakeDesign, make_raw, make_regions
from pytrackinganalysis import Counter, Parameters, Tracker, clock_repair
from pytrackinganalysis.clock_repair import (
    HOUR_MS,
    ClockRollbackError,
    find_rollbacks,
    raise_if_rolled_back,
    repair_export,
    snap_to_hours,
)

STEP_MS = 140.0
START = datetime(2024, 7, 29, 11, 28, 51, 515000)
COLUMNS = ["ObjectID", "TrackingRegion", "X", "Y", "RelX", "RelY", "Frame", "MSec",
           "CountingRegion", "DataQuality", "Time", "Millisec", "Indicator", "NObjects"]


def clock(n_frames, rollbacks=None):
    """Per-frame MSec at STEP_MS per frame, set back at *rollbacks* (``{frame: lost_ms}``)."""
    frames = np.arange(n_frames)
    msec = frames * STEP_MS
    for frame, lost in (rollbacks or {}).items():
        msec = np.where(frames >= frame, msec - lost, msec)
    return frames, msec


def render_time(stamp):
    return f"{stamp.month}/{stamp.day}/{stamp.year} {stamp.hour}:{stamp.minute:02d}:{stamp.second:02d}"


def write_export(root, n_frames, rollbacks, frames_per_file, trackers=("T_0", "T_1")):
    """A two-tracker DTrack-style export under ``root/data``, CRLF like DTrack writes."""
    data_dir = root / "data"
    data_dir.mkdir(parents=True)
    _, msec = clock(n_frames, rollbacks)
    rows = []
    for frame in range(n_frames):
        recorded = START + timedelta(milliseconds=float(msec[frame]))
        for k, tracker in enumerate(trackers):
            rows.append([0, tracker, frame + k, 0, frame + k, 0, frame, f"{msec[frame]:g}",
                         "Light" if frame % 2 else "NoLight", "High",
                         render_time(recorded), recorded.microsecond // 1000, 0, 1])
    per_file = frames_per_file * len(trackers)
    files = []
    for i, start in enumerate(range(0, len(rows), per_file), start=1):
        path = data_dir / f"Synthetic_Data_{i}.csv"
        with open(path, "w", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\r\n")
            writer.writerow(COLUMNS)
            writer.writerows(rows[start:start + per_file])
        files.append(path)
    return files


def read_export(files):
    return pd.concat([pd.read_csv(f) for f in files], ignore_index=True)


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------

def test_an_hour_lost_mid_recording_is_one_rollback():
    frames, msec = clock(100, {40: HOUR_MS})
    [rollback] = find_rollbacks(frames, msec)
    assert (rollback.previous_frame, rollback.frame) == (39, 40)
    assert rollback.lost_ms == pytest.approx(HOUR_MS)
    assert rollback.offset_ms == HOUR_MS
    assert rollback.snapped_hours == 1


def test_rollbacks_are_reported_in_recording_order():
    frames, msec = clock(100, {40: HOUR_MS, 80: HOUR_MS})
    assert [r.frame for r in find_rollbacks(frames, msec)] == [40, 80]


def test_sub_second_jitter_is_not_a_rollback():
    frames, msec = clock(100)
    msec[50] -= 50
    assert find_rollbacks(frames, msec) == []


def test_a_clean_clock_has_no_rollbacks():
    frames, msec = clock(100)
    assert find_rollbacks(frames, msec) == []
    assert raise_if_rolled_back("T_1", frames, msec) is None


def test_a_correction_that_is_not_whole_hours_keeps_the_measured_value():
    lost = 47 * 60_000
    frames, msec = clock(100, {40: lost})
    [rollback] = find_rollbacks(frames, msec)
    assert rollback.snapped_hours is None
    assert rollback.offset_ms == pytest.approx(lost)


def test_snap_to_hours():
    assert snap_to_hours(2 * HOUR_MS + 500) == (2 * HOUR_MS, 2)
    assert snap_to_hours(HOUR_MS / 2) == (HOUR_MS / 2, None)
    assert snap_to_hours(HOUR_MS + 5_000) == (HOUR_MS + 5_000, None)


def test_the_loader_error_names_the_frame_and_the_repair_command():
    frames, msec = clock(100, {40: HOUR_MS})
    with pytest.raises(ClockRollbackError, match="60.0 min at frame 40") as excinfo:
        raise_if_rolled_back("T_0_0", frames, msec)
    assert "python -m pytrackinganalysis.clock_repair" in str(excinfo.value)


def test_a_tracker_refuses_a_rolled_back_clock(parameters):
    _, msec = clock(6, {3: HOUR_MS})
    with pytest.raises(ClockRollbackError, match="T_1_0.*frame 3"):
        Tracker.Tracker(
            "T_1", 0, make_regions(), pd.DataFrame(), parameters, FakeDesign(),
            make_raw(minutes=tuple(msec / 60_000), xs=tuple(range(6))),
        )


def test_a_counter_refuses_a_rolled_back_clock():
    params = Parameters.Parameters(tracking_type=Parameters.TrackingType.COUNTER)
    params.mm_per_pixel = 1.0
    params.fps = 0
    _, msec = clock(6, {3: HOUR_MS})
    with pytest.raises(ClockRollbackError, match="frame 3"):
        Counter.Counter(
            "T_1", make_regions(), pd.DataFrame(), params, FakeDesign(),
            make_raw(minutes=tuple(msec / 60_000), xs=tuple(range(6))),
        )


# --------------------------------------------------------------------------
# Repairing the files
# --------------------------------------------------------------------------

def test_repair_restores_a_monotonic_clock_and_keeps_the_originals(tmp_path):
    files = write_export(tmp_path, n_frames=50, rollbacks={30: HOUR_MS}, frames_per_file=20)
    originals = {f.name: f.read_bytes() for f in files}

    report = repair_export(tmp_path, log=None)

    assert [r.frame for r in report.rollbacks] == [30]
    assert report.rollbacks[0].snapped_hours == 1
    assert report.rows_shifted == 20 * 2
    assert report.minutes_after == pytest.approx(49 * STEP_MS / 60_000)

    # The untouched originals sit beside data/, byte for byte, with a note.
    assert report.backup_dir == tmp_path / "data_original"
    for f in files:
        assert (report.backup_dir / f.name).read_bytes() == originals[f.name]
    assert "clock went back" in (report.backup_dir / "README.txt").read_text()

    repaired = read_export(files)
    for _, rows in repaired.groupby("TrackingRegion"):
        rows = rows.sort_values("Frame")
        assert np.allclose(np.diff(rows["MSec"]), STEP_MS)
        stamps = pd.to_datetime(rows["Time"]) + pd.to_timedelta(rows["Millisec"], unit="ms")
        assert (stamps.diff().dropna() == pd.Timedelta(milliseconds=STEP_MS)).all()
        assert stamps.iloc[30] == pd.Timestamp(START + timedelta(milliseconds=30 * STEP_MS))

    # A file entirely before the rollback is byte-identical; line endings survive.
    assert files[0].read_bytes() == originals[files[0].name]
    assert b"\r\n" in files[1].read_bytes()

    # Nothing but the clock columns changed.
    before = pd.concat([pd.read_csv(io.BytesIO(originals[f.name])) for f in files], ignore_index=True)
    others = [c for c in COLUMNS if c not in ("MSec", "Time", "Millisec")]
    pd.testing.assert_frame_equal(before[others], repaired[others])


def test_two_rollbacks_accumulate(tmp_path):
    files = write_export(tmp_path, n_frames=60, rollbacks={20: HOUR_MS, 40: HOUR_MS}, frames_per_file=25)
    report = repair_export(tmp_path, log=None)
    assert [r.frame for r in report.rollbacks] == [20, 40]
    rows = read_export(files).query("TrackingRegion == 'T_0'").sort_values("Frame")
    assert np.allclose(np.diff(rows["MSec"]), STEP_MS)
    assert rows["MSec"].iloc[59] == pytest.approx(59 * STEP_MS)


def test_a_second_run_finds_nothing_to_do(tmp_path):
    files = write_export(tmp_path, n_frames=50, rollbacks={30: HOUR_MS}, frames_per_file=20)
    repair_export(tmp_path, log=None)
    repaired = {f.name: f.read_bytes() for f in files}

    again = repair_export(tmp_path, log=None)

    assert again.rollbacks == []
    assert again.backup_dir is None
    assert {f.name: f.read_bytes() for f in files} == repaired


def test_dry_run_changes_nothing(tmp_path):
    files = write_export(tmp_path, n_frames=50, rollbacks={30: HOUR_MS}, frames_per_file=20)
    originals = {f.name: f.read_bytes() for f in files}

    report = repair_export(tmp_path, dry_run=True, log=None)

    assert [r.frame for r in report.rollbacks] == [30]
    assert report.minutes_before < 0 < report.minutes_after
    assert {f.name: f.read_bytes() for f in files} == originals
    assert not (tmp_path / "data_original").exists()


def test_refuses_to_overwrite_an_existing_backup(tmp_path):
    files = write_export(tmp_path, n_frames=50, rollbacks={30: HOUR_MS}, frames_per_file=20)
    originals = {f.name: f.read_bytes() for f in files}
    (tmp_path / "data_original").mkdir()
    (tmp_path / "data_original" / "Synthetic_Data_1.csv").write_text("the real originals")

    with pytest.raises(FileExistsError, match="already exists"):
        repair_export(tmp_path, log=None)

    assert {f.name: f.read_bytes() for f in files} == originals
    assert (tmp_path / "data_original" / "Synthetic_Data_1.csv").read_text() == "the real originals"


def test_msec_only_leaves_the_wall_clock_as_recorded(tmp_path):
    files = write_export(tmp_path, n_frames=50, rollbacks={30: HOUR_MS}, frames_per_file=20)
    before = read_export(files)
    repair_export(tmp_path, msec_only=True, log=None)
    after = read_export(files)
    assert np.allclose(np.diff(after.query("TrackingRegion == 'T_0'")["MSec"]), STEP_MS)
    pd.testing.assert_series_equal(before["Time"], after["Time"])
    pd.testing.assert_series_equal(before["Millisec"], after["Millisec"])


def test_the_data_folder_itself_is_accepted(tmp_path):
    write_export(tmp_path, n_frames=50, rollbacks={30: HOUR_MS}, frames_per_file=20)
    report = repair_export(tmp_path / "data", log=None)
    assert report.backup_dir == tmp_path / "data_original"


def test_a_repaired_export_loads_as_a_tracker(tmp_path, parameters):
    files = write_export(tmp_path, n_frames=50, rollbacks={30: HOUR_MS}, frames_per_file=20)
    repair_export(tmp_path, log=None)
    raw = read_export(files).query("TrackingRegion == 'T_0'").reset_index(drop=True)

    tracker = Tracker.Tracker(
        "T_0", 0, make_regions("T_0"), pd.DataFrame(), parameters,
        FakeDesign(region_name="T_0"), raw,
    )

    minutes = tracker.rawdata["Minutes"]
    assert minutes.is_monotonic_increasing
    assert minutes.iloc[-1] == pytest.approx(49 * STEP_MS / 60_000)


def test_cli_dry_run_reports_and_exits_zero(tmp_path, capsys):
    write_export(tmp_path, n_frames=50, rollbacks={30: HOUR_MS}, frames_per_file=20)
    assert clock_repair.main([str(tmp_path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "clock went back 60.00 min" in out
    assert "Dry run" in out
    assert not (tmp_path / "data_original").exists()


def test_cli_reports_a_missing_directory(tmp_path, capsys):
    assert clock_repair.main([str(tmp_path / "nowhere")]) == 1
    assert "Error" in capsys.readouterr().err
