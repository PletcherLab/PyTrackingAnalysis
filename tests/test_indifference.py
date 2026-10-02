"""The single-treatment test against indifference (PI = 0, Percentage = 0.5)."""

from __future__ import annotations

import numpy as np
import pytest

from pytrackinganalysis import indifference


def test_each_preference_metric_has_its_own_null():
    assert indifference.null_for("FinalPI") == 0.0
    assert indifference.null_for("FinalPercentage") == 0.5
    # A metric with no natural null is never tested against one.
    assert indifference.null_for("TotalDistancePerMin") is None
    assert indifference.one_sample([1.0, 2.0, 3.0], "TotalDistancePerMin") is None


def test_one_sample_detects_a_preference_and_reports_its_parts():
    from scipy import stats as sstats

    vals = np.array([0.42, 0.55, 0.61, 0.38, 0.50, np.nan])   # NaN is dropped
    result = indifference.one_sample(vals, "FinalPI")
    _t, p = sstats.ttest_1samp(vals[:-1], 0.0)
    assert result["n"] == 5 and result["null"] == 0.0
    assert result["mean"] == pytest.approx(0.492)
    assert result["diff"] == pytest.approx(0.492)
    assert result["p"] == pytest.approx(p) and result["p"] < 0.001

    # Percentage is tested against 0.5, not 0: an indifferent group is not
    # "significant" just because its fractions are positive.
    result = indifference.one_sample([0.48, 0.52, 0.50, 0.49, 0.51],
                                     "FinalPercentage")
    assert result["null"] == 0.5 and result["p"] > 0.5


def test_one_sample_declines_when_the_test_is_undefined():
    assert indifference.one_sample([0.4], "FinalPI") is None          # n < 2
    assert indifference.one_sample([np.nan, 0.4], "FinalPI") is None
    # Identical values: zero variance, the t statistic is undefined.
    assert indifference.one_sample([0.3, 0.3, 0.3], "FinalPI") is None
