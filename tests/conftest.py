"""Synthetic fixtures for the analysis-core tests.

Everything here is built in memory: no CSVs, no xlsx, no Qt. The point is to pin
the numerical behaviour of the tracker/counter classes with inputs small enough
that the expected answer can be worked out by hand.
"""

from __future__ import annotations

import os

# Must be set before anything imports Qt or pyplot: the suite runs without a
# display, and a windowing backend would either fail or block on a dialog.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

import matplotlib
import pandas as pd
import pytest

# The tracker modules import pyplot at module scope.
matplotlib.use("Agg")

from pytrackinganalysis import Parameters  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_ui_settings(tmp_path_factory, monkeypatch):
    """Point the persisted UI preferences (``ui.json``, the AI model cache) at
    a folder of the test's own. Opening a Project in the Hub or the Config
    Editor adds it to the recent list, so without this the suite wrote its
    temporary folders into the user's real
    ``~/.config/pytrackinganalysis/ui.json``."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg")))


DEFAULT_MINUTES = (0.0, 1.0, 2.0)
DEFAULT_XS = (0.0, 10.0, 20.0)


def make_raw(minutes=DEFAULT_MINUTES, xs=DEFAULT_XS, ys=None,
             quality=None, counting_region=None, nobjects=None):
    """A DTrack-shaped frame with one row per sample.

    Positions are given in pixels; the fixtures below use ``mm_per_pixel=1`` so
    that expected distances read directly off *xs* / *ys*.
    """
    n = len(minutes)
    ys = tuple(ys) if ys is not None else (0.0,) * n
    quality = tuple(quality) if quality is not None else ("High",) * n
    counting_region = tuple(counting_region) if counting_region is not None else ("None",) * n
    nobjects = tuple(nobjects) if nobjects is not None else (1,) * n

    return pd.DataFrame({
        "Frame": range(n),
        "MSec": [m * 60_000 for m in minutes],
        "Time": ["00:00:00"] * n,
        "Millisec": [0] * n,
        "RelX": list(xs),
        "RelY": list(ys),
        "X": list(xs),
        "Y": list(ys),
        "DataQuality": list(quality),
        "NObjects": list(nobjects),
        "CountingRegion": list(counting_region),
        "Indicator": [0] * n,
        "TrackingRegion": ["T_1"] * n,
        "ObjectID": [0] * n,
    })


def make_regions(name="T_1"):
    """ROI table rows for one tracking region."""
    return pd.DataFrame({
        "Name": [name],
        "Type": ["Tracking"],
        "Shape": ["Rectangle"],
        "Width": [100],
        "Height": [100],
    })


class FakeDesign:
    """Stand-in for ExperimentalDesign with no file I/O."""

    def __init__(self, treatment="A", counting_regions=None, region_name="T_1"):
        self.tracking_regions = pd.DataFrame({
            "RegionName": [region_name],
            "Treatment": [treatment],
            "XLocationMultiplier": [1],
            "YLocationMultiplier": [1],
        })
        self.counting_regions = counting_regions

    def get_tracking_region(self, region_name):
        return self.tracking_regions[self.tracking_regions["RegionName"] == region_name]


@pytest.fixture
def parameters():
    """mm_per_pixel of 1 keeps expected distances equal to the raw coordinates."""
    p = Parameters.Parameters(tracking_type=Parameters.TrackingType.TRACKER)
    p.mm_per_pixel = 1.0
    p.fps = 0  # minutes come from the MSec column
    return p


@pytest.fixture
def make_tracker(parameters):
    """Build a Tracker over synthetic data."""
    from pytrackinganalysis import Tracker

    def _build(raw=None, design=None, params=None, region="T_1", object_id=0):
        return Tracker.Tracker(
            region, object_id, make_regions(region), pd.DataFrame(),
            params or parameters, design if design is not None else FakeDesign(),
            raw if raw is not None else make_raw(),
        )

    return _build
