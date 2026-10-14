"""The resampling engine behind the Permutation Test and the Sign-Flip Test.

Pure numerics — no experiments, no factors, no phases. :mod:`.comparisons`
decides *what* is compared; this module answers "where does the observed
statistic fall among its relabellings?".

* :func:`permutation_test` — shuffle group labels (within strata) and locate
  the observed Welch t. With more than one pair it is single-step **max-T**
  (each shuffle keeps its most extreme pair), the family-wise control Tukey
  HSD gives. Every shuffle keeps the real group sizes, in every stratum.
* :func:`sign_flip_test` — flip the signs of deviations from a null value
  and locate the observed one-sample t.
* :func:`welch_family` — the parametric companion: pairwise Welch t-tests,
  Holm-adjusted when a comparison holds more than one pair.

Both resampling tests are reproducible to the digit: every test starts from
the same fixed seed, so the p depends only on the data (callers sort their
data canonically first), and the same comparison computed for Stats.txt, a
report table and a figure bracket gives the same number. When the number of
distinct relabellings is at most :data:`SHUFFLES` they are all enumerated and
the p is exact; ``p_floor`` then says how small a p the data can reach at all
(3 vs 3 on one plate has 20 relabellings, so a two-sided p cannot fall below
0.1).

Sidedness is per pair: ``0`` two-sided, ``+1`` predicts mean(B) > mean(A),
``-1`` predicts mean(B) < mean(A). t is always oriented B − A.
"""

from __future__ import annotations

import itertools
import math

import numpy as np

#: Random shuffles drawn when the relabellings are too many to enumerate.
SHUFFLES = 100_000
#: Every test starts from this seed, so a re-run reproduces each p exactly.
SEED = 20261014
#: Shuffles evaluated per vectorised block (bounds memory on large Projects).
_CHUNK = 4_000
#: Relative tolerance when counting relabellings "at least as extreme": the
#: observed labelling, recomputed by matrix arithmetic, must count itself.
_TIE = 1e-9

SIDE_NAMES = {0: "two-sided", 1: "greater", -1: "less"}


# ---------------------------------------------------------------------------
# Welch t from sufficient statistics
# ---------------------------------------------------------------------------

def _welch_t(n_a, s_a, q_a, n_b, s_b, q_b):
    """Welch t (B − A) from counts, sums and sums of squares; arrays broadcast.

    No spread in either group leaves no standard error: identical means give
    t = 0, different means ±inf — so an all-identical relabelling never
    outranks a real one by a division artefact.
    """
    m_a, m_b = s_a / n_a, s_b / n_b
    v_a = np.maximum(q_a - s_a * m_a, 0.0) / (n_a - 1)
    v_b = np.maximum(q_b - s_b * m_b, 0.0) / (n_b - 1)
    se = np.sqrt(v_a / n_a + v_b / n_b)
    diff = m_b - m_a
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(se > 0, diff / np.where(se > 0, se, 1.0),
                     np.sign(diff) * np.inf)
    return np.where((se == 0) & (diff == 0), 0.0, t)


def _oriented(t, side):
    """The statistic whose large values count against the null for *side*."""
    if side > 0:
        return t
    if side < 0:
        return -t
    return np.abs(t)


def _at_least(stat, observed):
    return stat >= observed - _TIE * max(1.0, abs(float(observed))) \
        if np.isfinite(observed) else stat >= observed


# ---------------------------------------------------------------------------
# Relabellings
# ---------------------------------------------------------------------------

def _multinomial(counts) -> int:
    total, out = 0, 1
    for c in counts:
        total += c
        out *= math.comb(total, c)
    return out


def count_relabellings(groups, strata=None) -> int:
    """Distinct labellings reachable by shuffling *groups* within *strata*."""
    groups = np.asarray(groups)
    strata = np.zeros(len(groups), int) if strata is None else np.asarray(strata)
    total = 1
    for s in np.unique(strata):
        _, counts = np.unique(groups[strata == s], return_counts=True)
        total *= _multinomial(counts)
    return total


def _arrangements(length, k, levels, counts):
    """Rows of every arrangement of levels ``k..`` over *length* slots."""
    if k == len(levels) - 1:
        return np.full((1, length), levels[k], dtype=levels.dtype)
    rest = _arrangements(length - counts[k], k + 1, levels, counts)
    combos = np.array(list(itertools.combinations(range(length), counts[k])),
                      dtype=np.int64).reshape(-1, counts[k])
    n_c, n_r = len(combos), len(rest)
    chosen = np.zeros((n_c, length), dtype=bool)
    chosen[np.arange(n_c)[:, None], combos] = True
    others = np.nonzero(~chosen)[1].reshape(n_c, length - counts[k])
    which = np.repeat(np.arange(n_c), n_r)
    rows = np.arange(n_c * n_r)[:, None]
    out = np.empty((n_c * n_r, length), dtype=levels.dtype)
    out[rows, combos[which]] = levels[k]
    out[rows, others[which]] = np.tile(rest, (n_c, 1))
    return out


def _stratum_labellings(labels):
    """Every distinct arrangement of the multiset *labels*, as rows."""
    levels, counts = np.unique(labels, return_counts=True)
    return _arrangements(len(labels), 0, levels, counts)


def _all_labellings(groups, strata):
    """The full relabelling matrix (one row per labelling) — only called when
    :func:`count_relabellings` is at most :data:`SHUFFLES`."""
    n = len(groups)
    per_stratum = []
    for s in np.unique(strata):
        idx = np.flatnonzero(strata == s)
        per_stratum.append((idx, _stratum_labellings(groups[idx])))
    total = int(np.prod([len(rows) for _, rows in per_stratum]))
    out = np.empty((total, n), dtype=groups.dtype)
    ## Cartesian product across strata, laid out like itertools.product.
    repeat = total
    tile = 1
    for idx, rows in per_stratum:
        repeat //= len(rows)
        block = np.repeat(rows, repeat, axis=0)
        out[:, idx] = np.tile(block, (tile, 1))
        tile *= len(rows)
    return out


def _shuffled_labellings(groups, strata, size, rng):
    out = np.empty((size, len(groups)), dtype=groups.dtype)
    for s in np.unique(strata):
        idx = np.flatnonzero(strata == s)
        out[:, idx] = rng.permuted(np.tile(groups[idx], (size, 1)), axis=1)
    return out


# ---------------------------------------------------------------------------
# Permutation Test (max-T over pairs)
# ---------------------------------------------------------------------------

def permutation_test(values, groups, strata=None, pairs=None, sides=None,
                     shuffles: int = SHUFFLES, seed: int = SEED) -> dict:
    """Studentized permutation test of group means, max-T across *pairs*.

    *values* (no NaN), *groups* (integer codes ``0..k-1``) and *strata*
    (codes; labels move only within a stratum) are aligned 1-D arrays.
    *pairs* lists ``(i, j)`` code pairs, t oriented ``mean(j) − mean(i)``
    (default: every pair, ``i < j``); *sides* gives each pair's sidedness.
    Every group needs at least two values.

    Returns ``{t, p, exact, relabellings, p_floor}`` — ``t`` and ``p`` per
    pair (p family-wise adjusted when there is more than one pair).
    """
    x = np.asarray(values, dtype=float)
    g = np.asarray(groups, dtype=np.int16)
    st = (np.zeros(len(x), dtype=np.int32) if strata is None
          else np.unique(np.asarray(strata), return_inverse=True)[1])
    k = int(g.max()) + 1
    pairs = list(pairs or itertools.combinations(range(k), 2))
    sides = list(sides or [0] * len(pairs))
    x = x - x.mean()            # centring keeps sums of squares well conditioned
    x2 = x * x
    n = np.bincount(g, minlength=k).astype(float)

    def t_of(labelling):
        """Per-pair Welch t for each labelling row (rows × pairs)."""
        s = np.empty((labelling.shape[0], k))
        q = np.empty_like(s)
        for level in range(k):
            mask = (labelling == level).astype(float)
            s[:, level] = mask @ x
            q[:, level] = mask @ x2
        return np.column_stack([
            _welch_t(n[i], s[:, i], q[:, i], n[j], s[:, j], q[:, j])
            for i, j in pairs])

    t_obs = t_of(g[None, :])[0]
    obs = np.array([_oriented(t, side) for t, side in zip(t_obs, sides)])

    def max_stat(t):
        return np.max(np.column_stack(
            [_oriented(t[:, c], side) for c, side in enumerate(sides)]), axis=1)

    total = count_relabellings(g, st)
    hits = np.zeros(len(pairs), dtype=np.int64)
    if total <= shuffles:
        m = max_stat(t_of(_all_labellings(g, st)))
        for c in range(len(pairs)):
            hits[c] = int(np.count_nonzero(_at_least(m, obs[c])))
        p = hits / total
        floor = float(np.count_nonzero(_at_least(m, m.max())) / total)
        return {"t": [float(v) for v in t_obs], "p": [float(v) for v in p],
                "exact": True, "relabellings": int(total), "p_floor": floor}

    rng = np.random.default_rng(seed)
    done = 0
    while done < shuffles:
        size = min(_CHUNK, shuffles - done)
        m = max_stat(t_of(_shuffled_labellings(g, st, size, rng)))
        for c in range(len(pairs)):
            hits[c] += int(np.count_nonzero(_at_least(m, obs[c])))
        done += size
    p = (hits + 1) / (shuffles + 1)
    return {"t": [float(v) for v in t_obs], "p": [float(v) for v in p],
            "exact": False, "relabellings": int(shuffles),
            "p_floor": 1.0 / (shuffles + 1)}


# ---------------------------------------------------------------------------
# Sign-Flip Test
# ---------------------------------------------------------------------------

def sign_flip_test(deviations, side: int = 0, shuffles: int = SHUFFLES,
                   seed: int = SEED) -> dict | None:
    """One-sample t of *deviations* (value − null) located among sign flips.

    Assumes only that, under no preference, values are symmetric about the
    null. Returns ``{t, p, exact, relabellings, p_floor}``, or None with
    fewer than two values or no spread (the t-test beside it is undefined
    there too, and a flip test would report a fake certainty).
    """
    d = np.asarray(deviations, dtype=float)
    d = d[~np.isnan(d)]
    n = len(d)
    if n < 2 or np.ptp(d) == 0:
        return None
    ssq = float(np.sum(d * d))

    def t_of(signs):
        mean = (signs @ d) / n
        var = np.maximum(ssq - n * mean * mean, 0.0) / (n - 1)
        se = np.sqrt(var / n)
        with np.errstate(divide="ignore", invalid="ignore"):
            t = np.where(se > 0, mean / np.where(se > 0, se, 1.0),
                         np.sign(mean) * np.inf)
        return np.where((se == 0) & (mean == 0), 0.0, t)

    t_obs = float(t_of(np.ones((1, n)))[0])
    obs = float(_oriented(t_obs, side))
    total = 2 ** n
    if total <= shuffles:
        signs = 1.0 - 2.0 * ((np.arange(total)[:, None] >> np.arange(n)) & 1)
        stat = _oriented(t_of(signs), side)
        p = float(np.count_nonzero(_at_least(stat, obs)) / total)
        floor = float(np.count_nonzero(_at_least(stat, stat.max())) / total)
        return {"t": t_obs, "p": p, "exact": True, "relabellings": total,
                "p_floor": floor}
    rng = np.random.default_rng(seed)
    hits, done = 0, 0
    while done < shuffles:
        size = min(_CHUNK * 4, shuffles - done)
        signs = rng.choice((-1.0, 1.0), size=(size, n))
        hits += int(np.count_nonzero(_at_least(_oriented(t_of(signs), side), obs)))
        done += size
    return {"t": t_obs, "p": (hits + 1) / (shuffles + 1), "exact": False,
            "relabellings": shuffles, "p_floor": 1.0 / (shuffles + 1)}


# ---------------------------------------------------------------------------
# Parametric companion
# ---------------------------------------------------------------------------

def welch_family(samples, pairs, sides) -> list[float]:
    """Welch p for each ``(i, j)`` pair of *samples* (t oriented j − i), in
    the pair's sidedness; Holm-adjusted when there is more than one pair."""
    from scipy import stats as sstats

    alternative = {0: "two-sided", 1: "greater", -1: "less"}
    raw = []
    for (i, j), side in zip(pairs, sides):
        a, b = np.asarray(samples[i], float), np.asarray(samples[j], float)
        p = sstats.ttest_ind(b, a, equal_var=False,
                             alternative=alternative[side]).pvalue
        raw.append(float(p) if np.isfinite(p) else float("nan"))
    return holm(raw)


def holm(pvalues) -> list[float]:
    """Holm step-down adjustment; NaN entries pass through untouched."""
    p = np.asarray(pvalues, dtype=float)
    ok = np.flatnonzero(~np.isnan(p))
    if len(ok) <= 1:
        return [float(v) for v in p]
    order = ok[np.argsort(p[ok], kind="stable")]
    m = len(order)
    adjusted = np.maximum.accumulate(
        np.minimum(1.0, (m - np.arange(m)) * p[order]))
    out = p.copy()
    out[order] = adjusted
    return [float(v) for v in out]


def one_sided_from_two(p_two: float, estimate: float, side: int) -> float:
    """A one-sided p from a symmetric two-sided test's p and its estimate's
    sign (used for the mixed model's Wald test)."""
    if side == 0 or p_two is None or not np.isfinite(p_two):
        return p_two
    agrees = (estimate > 0) if side > 0 else (estimate < 0)
    return p_two / 2 if agrees else 1 - p_two / 2
