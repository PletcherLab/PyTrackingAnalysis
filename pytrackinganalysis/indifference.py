"""Tests against indifference, for an experiment with a single treatment.

With one treatment level there is no pair to compare, but a two-choice
preference metric has a built-in null: PI = 0 and Percentage = 0.5 (50%) both
mean "no preference between the counting regions". So the single-treatment
question becomes "did the animals prefer one side at all?", answered per phase
with a two-sided one-sample t-test against that null.

One module so the three places that report statistics — the experiment's
``*_Stats.txt`` (``Arena``), the experiment report's table
(``report_figures``) and the Project's pooled statistics (``project``) — run
the same test and cannot drift apart.
"""

from __future__ import annotations

import numpy as np

#: Metric -> the value that means "no preference". A metric absent here has
#: no natural null and is never tested against one.
INDIFFERENCE = {
    "FinalPI": 0.0,
    "FinalPercentage": 0.5,
}

#: Reader-facing form of each null, for tables and Stats.txt.
_NULL_LABEL = {
    "FinalPI": "0",
    "FinalPercentage": "0.5 (50%)",
}

TEST_NAME = "one-sample t-test (two-sided)"


def null_for(metric: str) -> float | None:
    """The indifference value for *metric*, or None when it has none."""
    return INDIFFERENCE.get(metric)


def null_label(metric: str) -> str:
    return _NULL_LABEL.get(metric, f"{INDIFFERENCE.get(metric, '')}")


def one_sample(values, metric: str) -> dict | None:
    """Test *values* of *metric* against its indifference value.

    Returns ``{n, mean, sd, null, diff, t, p}`` or None when the metric has
    no null, fewer than two values remain after dropping NaN, or every value
    is identical — with no spread there is no standard error, and scipy's
    t = inf, p = 0 would report a fake certainty (every fly at PI = 1.0 is
    possible with a handful of animals).
    """
    null = null_for(metric)
    if null is None:
        return None
    vals = np.asarray(values, dtype=float)
    vals = vals[~np.isnan(vals)]
    if len(vals) < 2 or np.ptp(vals) == 0:
        return None
    from scipy import stats as sstats

    t_stat, p = sstats.ttest_1samp(vals, null)
    if not np.isfinite(p):
        return None
    mean = float(np.mean(vals))
    return {"n": int(len(vals)), "mean": mean,
            "sd": float(np.std(vals, ddof=1)), "null": float(null),
            "diff": mean - float(null), "t": float(t_stat), "p": float(p)}
