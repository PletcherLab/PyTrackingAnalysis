"""Open-field measures: centrophobism, exploration and walking structure (ADR-0015).

Pure functions over one fly's frame data and its arena geometry, so any
tracker type can use them without inheriting them. ``PairwiseInteractionTracker``
uses them today; a single-fly Open Field type can reuse them unchanged.

Conventions shared by every function here:

* Positions are in mm, relative to the ROI centre (DTrack's ``RelX``/``RelY``
  times ``mm_per_pixel``).
* ``valid`` marks frames whose position can be trusted (High quality). Every
  measure ignores the rest.
* Times are in minutes, like the ``Minutes`` column, unless a name says
  ``_s`` / ``_sec``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

#: ρ below which a position is in the centre zone. A scaled copy of the arena
#: by 1/√2 holds exactly half the area for an ellipse and a rectangle alike, so
#: a fly using the arena uniformly spends half its time in the centre zone.
CENTER_ZONE_RHO = 1.0 / np.sqrt(2.0)

#: The per-fly columns :func:`fly_measures` produces, in summary order.
FLY_MEASURES = (
    "WalkingSpeed_mm_s", "WalkingBoutsPerMin", "MeanWalkingBoutDuration_s",
    "CentrophobismIndex", "CentrophobismIndexAllFrames", "WallZoneFraction",
    "MeanWallDistance_mm",
    "ExploredFraction", "ExploredFractionCenter", "ExploredFractionPeriphery",
    "ExplorationAUC", "TimeTo50PctExplored",
)

#: Orientation bins for the body footprint. An ellipse is symmetric under a
#: half turn, so 36 bins cover [0°, 180°) at 5° — far finer than a heading
#: estimated from pixel-sized steps can resolve.
_N_HEADING_BINS = 36


# --------------------------------------------------------------------------
# Arena geometry
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ArenaGeometry:
    """The arena as drawn: an ellipse or rectangle centred on the origin (mm)."""

    shape: str
    half_width: float
    half_height: float
    mm_per_pixel: float

    @classmethod
    def from_roi(cls, roi, mm_per_pixel):
        """Build from one row of the DTrack ROI sheet (a Series or 1-row frame)."""
        if isinstance(roi, pd.DataFrame):
            if len(roi) == 0:
                raise ValueError("No ROI row for this tracking region.")
            roi = roi.iloc[0]
        shape = str(roi["Shape"])
        if shape not in ("Ellipse", "Rectangle"):
            raise ValueError(f"Unsupported arena shape '{shape}'; expected Ellipse or Rectangle.")
        return cls(shape=shape,
                   half_width=float(roi["Width"]) * mm_per_pixel / 2.0,
                   half_height=float(roi["Height"]) * mm_per_pixel / 2.0,
                   mm_per_pixel=float(mm_per_pixel))

    def normalized_radius(self, x, y):
        """ρ: 0 at the centre, 1 on the wall, >1 outside."""
        u = np.abs(np.asarray(x, dtype=float)) / self.half_width
        v = np.abs(np.asarray(y, dtype=float)) / self.half_height
        if self.shape == "Ellipse":
            return np.hypot(u, v)
        return np.maximum(u, v)

    def wall_distance(self, x, y):
        """Signed distance to the wall in mm: positive inside, negative outside."""
        x = np.abs(np.asarray(x, dtype=float))
        y = np.abs(np.asarray(y, dtype=float))
        if self.shape == "Rectangle":
            inside = np.minimum(self.half_width - x, self.half_height - y)
            outside = -np.hypot(np.maximum(x - self.half_width, 0.0),
                                np.maximum(y - self.half_height, 0.0))
            return np.where(inside >= 0, inside, outside)
        d = _point_ellipse_distance(x, y, self.half_width, self.half_height)
        return np.where(self.normalized_radius(x, y) <= 1.0, d, -d)

    def raster(self):
        """Pixel-centre coordinates (mm) of the ROI's bounding box, and its in-arena mask.

        One cell per camera pixel, so the footprint is counted at the
        resolution it was recorded at.
        """
        nx = max(int(round(2 * self.half_width / self.mm_per_pixel)), 1)
        ny = max(int(round(2 * self.half_height / self.mm_per_pixel)), 1)
        xs = (np.arange(nx) + 0.5) * self.mm_per_pixel - self.half_width
        ys = (np.arange(ny) + 0.5) * self.mm_per_pixel - self.half_height
        gx, gy = np.meshgrid(xs, ys)          # shape (ny, nx), row = y
        return gx, gy, self.normalized_radius(gx, gy) <= 1.0


def _point_ellipse_distance(x, y, a, b, iterations=60):
    """Unsigned distance from first-quadrant points to the ellipse x²/a² + y²/b² = 1.

    Exact (to bisection precision) for points inside or outside; Eberly's
    formulation, vectorised. A circle short-circuits to |R − r|.
    """
    if np.isclose(a, b):
        return np.abs(a - np.hypot(x, y))
    swap = a < b
    if swap:                                   # the algorithm wants a ≥ b
        a, b, x, y = b, a, y, x
    out = np.empty_like(x, dtype=float)

    on_axis = y <= 0
    # Points on the major axis: closest point is either (a, 0) or interior.
    xa = x[on_axis]
    numer = a * xa
    denom = a * a - b * b
    near = numer < denom
    x0 = np.where(near, a * a * xa / np.where(denom > 0, denom, 1.0), a)
    y0 = np.where(near, b * np.sqrt(np.clip(1 - (x0 / a) ** 2, 0, None)), 0.0)
    out[on_axis] = np.hypot(x0 - xa, y0)

    gen = ~on_axis
    xg, yg = x[gen], y[gen]
    on_minor = xg <= 0
    res = np.abs(yg - b)                       # x == 0: closest point is (0, b)
    z0, z1 = xg / a, yg / b
    r0 = (a / b) ** 2
    n0 = r0 * z0
    s0 = z1 - 1.0
    s1 = np.where(np.hypot(z0, z1) - 1.0 < 0, 0.0, np.hypot(n0, z1) - 1.0)
    s1 = np.maximum(s1, s0)
    for _ in range(iterations):
        s = 0.5 * (s0 + s1)
        g = (n0 / (s + r0)) ** 2 + (z1 / (s + 1.0)) ** 2 - 1.0
        s0 = np.where(g > 0, s, s0)
        s1 = np.where(g > 0, s1, s)
    s = 0.5 * (s0 + s1)
    px = r0 * xg / (s + r0)
    py = yg / (s + 1.0)
    res = np.where(on_minor, res, np.hypot(px - xg, py - yg))
    out[gen] = res
    return out


# --------------------------------------------------------------------------
# Bouts
# --------------------------------------------------------------------------

def find_bouts(active, minutes, bridge_s=0.5, min_s=0.5):
    """Runs of ``active`` frames as ``(start, end)`` positional index pairs (inclusive).

    Breaks shorter than *bridge_s* between two runs are bridged; runs shorter
    than *min_s* afterwards are dropped. Duration is measured from a run's
    first frame to its last, so a single-frame run lasts 0 s.
    """
    active = np.asarray(active, dtype=bool)
    t = np.asarray(minutes, dtype=float) * 60.0
    if active.size == 0 or not active.any():
        return np.empty((0, 2), dtype=int)
    edges = np.diff(active.astype(np.int8), prepend=0, append=0)
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1) - 1
    if len(starts) > 1:
        gaps = t[starts[1:]] - t[ends[:-1]]
        keep_break = gaps >= bridge_s
        starts = np.concatenate([starts[:1], starts[1:][keep_break]])
        ends = np.concatenate([ends[:-1][keep_break], ends[-1:]])
    long_enough = (t[ends] - t[starts]) >= min_s
    return np.column_stack([starts[long_enough], ends[long_enough]])


def bout_summary(bouts, minutes, observed_min, window_start=None):
    """Rate (per minute of *observed_min*), mean duration (s) and latency (min)."""
    t = np.asarray(minutes, dtype=float)
    n = len(bouts)
    rate = n / observed_min if observed_min and observed_min > 0 else np.nan
    if n == 0:
        return rate, np.nan, np.nan
    durations = (t[bouts[:, 1]] - t[bouts[:, 0]]) * 60.0
    start = t[0] if window_start is None else window_start
    return rate, float(durations.mean()), float(t[bouts[0, 0]] - start)


# --------------------------------------------------------------------------
# Centrophobism and the ROI check
# --------------------------------------------------------------------------

def centrophobism(x, y, valid, walking, delta_sec, geometry, wall_zone_mm=2.5,
                  min_walking_sec=30.0):
    """Centrophobism Index (walking frames), its all-frames variant, and wall measures."""
    valid = np.asarray(valid, dtype=bool)
    walking = np.asarray(walking, dtype=bool) & valid
    rho = geometry.normalized_radius(x, y)
    periphery = rho >= CENTER_ZONE_RHO

    def _index(mask):
        n = int(mask.sum())
        if n == 0:
            return np.nan
        p = periphery[mask].sum() / n
        return float(p - (1.0 - p))

    walking_sec = float(np.nansum(np.asarray(delta_sec, dtype=float)[walking]))
    ci = _index(walking) if walking_sec >= min_walking_sec else np.nan
    if valid.any():
        wall = np.clip(geometry.wall_distance(np.asarray(x)[valid], np.asarray(y)[valid]), 0, None)
        wall_zone = float((wall < wall_zone_mm).mean())
        mean_wall = float(wall.mean())
    else:
        wall_zone = mean_wall = np.nan
    return {
        "CentrophobismIndex": ci,
        "CentrophobismIndexAllFrames": _index(valid),
        "WallZoneFraction": wall_zone,
        "MeanWallDistance_mm": mean_wall,
    }


def reach_gap(x, y, valid, geometry, quantile=0.005):
    """How close the flies come to the ROI edge (mm): the *quantile* of wall distance.

    About +half a body width for a well-drawn ROI; much larger means the ROI
    is too big (or the flies barely moved), negative means positions outside it.
    """
    valid = np.asarray(valid, dtype=bool)
    if not valid.any():
        return np.nan
    d = geometry.wall_distance(np.asarray(x)[valid], np.asarray(y)[valid])
    return float(np.quantile(d, quantile))


def terminal_spread(x, y, valid, minutes, final_min=20.0, quantile=0.99):
    """How far (mm) a fly ranged from its median position over the final
    *final_min* minutes of the recording — at *quantile*, so one glitch frame
    cannot make a motionless fly look alive. NaN with no valid frames there."""
    t = np.asarray(minutes, dtype=float)
    valid = np.asarray(valid, dtype=bool)
    if t.size == 0:
        return np.nan
    late = valid & (t >= t[-1] - final_min)
    if not late.any():
        return np.nan
    lx = np.asarray(x, dtype=float)[late]
    ly = np.asarray(y, dtype=float)[late]
    r = np.hypot(lx - np.median(lx), ly - np.median(ly))
    return float(np.quantile(r, quantile))


# --------------------------------------------------------------------------
# Walking structure
# --------------------------------------------------------------------------

def walking_measures(speed, walking, valid, minutes, observed_min, bridge_s=0.5, min_s=0.5):
    """Speed while walking, and how often / how long the fly walks."""
    walking = np.asarray(walking, dtype=bool) & np.asarray(valid, dtype=bool)
    speed = np.asarray(speed, dtype=float)
    walking_speed = float(np.nanmean(speed[walking])) if walking.any() else np.nan
    bouts = find_bouts(walking, minutes, bridge_s, min_s)
    rate, duration, _ = bout_summary(bouts, minutes, observed_min)
    return {
        "WalkingSpeed_mm_s": walking_speed,
        "WalkingBoutsPerMin": rate,
        "MeanWalkingBoutDuration_s": duration,
    }


# --------------------------------------------------------------------------
# Exploration
# --------------------------------------------------------------------------

def smoothed_heading(x, y, valid, minutes, window_s=0.5, min_move_mm=0.2):
    """Body-axis estimate (radians, mod π) from the path, held while still.

    The displacement across a centred *window_s* gives the direction of
    travel; where the fly moved less than *min_move_mm* across it the last
    known heading is held (and the first known one before that).
    """
    t = np.asarray(minutes, dtype=float) * 60.0
    xs = pd.Series(np.where(valid, x, np.nan)).interpolate(limit_area="inside")
    ys = pd.Series(np.where(valid, y, np.nan)).interpolate(limit_area="inside")
    half = window_s / 2.0
    lo = np.searchsorted(t, t - half, side="left")
    hi = np.clip(np.searchsorted(t, t + half, side="right") - 1, 0, len(t) - 1)
    dx = xs.to_numpy()[hi] - xs.to_numpy()[lo]
    dy = ys.to_numpy()[hi] - ys.to_numpy()[lo]
    heading = np.where(np.hypot(dx, dy) >= min_move_mm, np.arctan2(dy, dx), np.nan)
    heading = pd.Series(heading).ffill().bfill().fillna(0.0).to_numpy()
    return np.mod(heading, np.pi)


def _stamps(geometry, length_mm, width_mm):
    """Pixel offsets (drow, dcol) inside the body ellipse, per orientation bin."""
    a, b = length_mm / 2.0, width_mm / 2.0
    r = int(np.ceil(a / geometry.mm_per_pixel)) + 1
    off = np.arange(-r, r + 1)
    dc, dr = np.meshgrid(off, off)
    px, py = dc * geometry.mm_per_pixel, dr * geometry.mm_per_pixel
    stamps = []
    for k in range(_N_HEADING_BINS):
        th = (k + 0.5) * np.pi / _N_HEADING_BINS
        u = px * np.cos(th) + py * np.sin(th)
        v = -px * np.sin(th) + py * np.cos(th)
        inside = (u / a) ** 2 + (v / b) ** 2 <= 1.0
        if not inside.any():                       # body smaller than a pixel
            inside = (dr == 0) & (dc == 0)
        stamps.append((dr[inside], dc[inside]))
    return stamps


def footprint_first_visits(x, y, valid, minutes, geometry, heading, *,
                           length_mm=2.5, width_mm=1.0, max_gap_s=1.0,
                           max_speed_mm_s=50.0, chunk=4000):
    """First time (minutes) the body footprint touched each in-arena pixel.

    Returns a 1-D array over the arena mask's pixels (``inf`` = never). The
    body is stamped at every valid frame and at points interpolated every
    half body-width along each step between consecutive valid frames — the
    sweep — unless the step spans more than *max_gap_s* or is faster than
    *max_speed_mm_s* (a tracking glitch, not a path).
    """
    gx, _gy, mask = geometry.raster()
    ny, nx = mask.shape
    first = np.full(ny * nx, np.inf)

    valid = np.asarray(valid, dtype=bool)
    idx = np.flatnonzero(valid)
    if idx.size == 0:
        return first[mask.ravel()]
    px = np.asarray(x, dtype=float)[idx]
    py = np.asarray(y, dtype=float)[idx]
    pt = np.asarray(minutes, dtype=float)[idx]
    ph = np.asarray(heading, dtype=float)[idx]

    # The sweep: interpolate along each plausible step at ≤ half-width spacing.
    spacing = max(width_mm / 2.0, geometry.mm_per_pixel)
    step = np.hypot(np.diff(px), np.diff(py))
    dt = np.diff(pt) * 60.0
    with np.errstate(divide="ignore", invalid="ignore"):
        plausible = (dt > 0) & (dt <= max_gap_s) & (step / dt <= max_speed_mm_s)
    n_extra = np.where(plausible, np.ceil(step / spacing).astype(int) - 1, 0)
    n_extra = np.clip(n_extra, 0, None)
    if n_extra.sum():
        seg = np.repeat(np.arange(len(step)), n_extra)
        k = np.concatenate([np.arange(1, n + 1) for n in n_extra if n]) \
            / np.repeat(n_extra + 1, n_extra)
        ix = px[seg] + k * (px[seg + 1] - px[seg])
        iy = py[seg] + k * (py[seg + 1] - py[seg])
        it = pt[seg] + k * (pt[seg + 1] - pt[seg])
        ih = ph[seg + 1]
        px = np.concatenate([px, ix]); py = np.concatenate([py, iy])
        pt = np.concatenate([pt, it]); ph = np.concatenate([ph, ih])
        order = np.argsort(pt, kind="stable")
        px, py, pt, ph = px[order], py[order], pt[order], ph[order]

    col = np.round((px + geometry.half_width) / geometry.mm_per_pixel - 0.5).astype(np.int64)
    row = np.round((py + geometry.half_height) / geometry.mm_per_pixel - 0.5).astype(np.int64)
    hbin = np.minimum((ph / np.pi * _N_HEADING_BINS).astype(int), _N_HEADING_BINS - 1)

    # A still fly stamps the same pixels frame after frame; keep the first.
    key = (row * (nx + 1) + col) * _N_HEADING_BINS + hbin
    new = np.concatenate([[True], key[1:] != key[:-1]])
    row, col, hbin, pt = row[new], col[new], hbin[new], pt[new]

    for k, (dr, dc) in enumerate(_stamps(geometry, length_mm, width_mm)):
        sel = np.flatnonzero(hbin == k)
        for s in range(0, sel.size, chunk):
            part = sel[s:s + chunk]
            rr = (row[part, None] + dr[None, :]).ravel()
            cc = (col[part, None] + dc[None, :]).ravel()
            tt = np.repeat(pt[part], dr.size)
            ok = (rr >= 0) & (rr < ny) & (cc >= 0) & (cc < nx)
            np.minimum.at(first, rr[ok] * nx + cc[ok], tt[ok])
    return first[mask.ravel()]


def fly_measures(data, geometry, parameters):
    """Every open-field measure for one fly over one window, as a Series.

    *data* is the window of a tracker's frames (``Tracker.get_data_subset``),
    carrying ``Xpos_mm``/``Ypos_mm`` (relative to the ROI centre),
    ``DataQuality``, ``IsWalking``, ``Speed_mm_sec``, ``DeltaSec``, ``Minutes``
    and ``Heading``. Without an arena geometry or any frames every measure is
    NA — visibly missing, never a misleading zero.
    """
    out = pd.Series(np.nan, index=list(FLY_MEASURES), dtype=float)
    if data is None or len(data) == 0:
        return out
    minutes = data["Minutes"].to_numpy(dtype=float)
    valid = (data["DataQuality"] == "High").to_numpy()
    walking = data["IsWalking"].fillna(False).to_numpy(dtype=bool)
    observed = float(minutes[-1] - minutes[0])
    for key, value in walking_measures(
            data["Speed_mm_sec"].to_numpy(dtype=float), walking, valid, minutes,
            observed).items():
        out[key] = value
    if geometry is None:
        return out
    x = data["Xpos_mm"].to_numpy(dtype=float)
    y = data["Ypos_mm"].to_numpy(dtype=float)
    for key, value in centrophobism(
            x, y, valid, walking, data["DeltaSec"].to_numpy(dtype=float), geometry,
            wall_zone_mm=parameters.wall_zone_mm).items():
        out[key] = value
    first = footprint_first_visits(
        x, y, valid, minutes, geometry, data["Heading"].to_numpy(dtype=float),
        length_mm=parameters.fly_length_mm, width_mm=parameters.fly_width_mm)
    for key, value in exploration(first, geometry, minutes[0], minutes[-1]).items():
        out[key] = value
    return out


def coverage_curve(first_visits, times):
    """Share of the arena covered by each of *times* (minutes) — the curve
    whose mean over a window is ``ExplorationAUC``."""
    visits = np.sort(np.asarray(first_visits, dtype=float))
    if visits.size == 0:
        return np.full(len(times), np.nan)
    return np.searchsorted(visits, np.asarray(times, dtype=float), side="right") / visits.size


def exploration(first_visits, geometry, window_start, window_end):
    """Coverage measures for one window from :func:`footprint_first_visits`.

    *window_start*/*window_end* are the first and last frame times (minutes)
    of the window, over which ``ExplorationAUC`` averages coverage.
    """
    gx, gy, mask = geometry.raster()
    center = (geometry.normalized_radius(gx, gy) < CENTER_ZONE_RHO)[mask]
    visited = np.isfinite(first_visits)
    n = first_visits.size
    out = {
        "ExploredFraction": visited.mean() if n else np.nan,
        "ExploredFractionCenter": visited[center].mean() if center.any() else np.nan,
        "ExploredFractionPeriphery": visited[~center].mean() if (~center).any() else np.nan,
    }
    span = window_end - window_start
    if n == 0 or not span or span <= 0:
        out["ExplorationAUC"] = np.nan
        out["TimeTo50PctExplored"] = np.nan
        return out
    # ∫coverage(t)dt / T = mean over pixels of the time each spent visited.
    out["ExplorationAUC"] = float(np.clip(window_end - first_visits[visited], 0, None).sum() / (n * span))
    half = int(np.ceil(n / 2))
    out["TimeTo50PctExplored"] = (float(np.sort(first_visits[visited])[half - 1] - window_start)
                                  if visited.sum() >= half else np.nan)
    return out
