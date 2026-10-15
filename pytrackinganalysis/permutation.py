"""The resampling engine behind the Permutation Test and the Sign-Flip Test.

Pure numerics — no experiments, no factors, no phases. :mod:`.comparisons`
decides *what* is compared; this module answers "where does the observed
statistic fall among its relabellings?".

* :func:`permutation_test` — shuffle group labels (within strata) and locate
  the observed **replicate-adjusted Welch t**: each pair's contrast in the
  model Value ~ Stratum + Group (strata as fixed effects), divided by its
  heteroscedasticity-robust HC2 standard error. With one stratum that is
  Welch's t exactly. Signed, so a one-sided test is a one-sided count. With
  more than one pair it is single-step **max-T** (each shuffle keeps its most
  extreme pair), the family-wise control Tukey HSD gives. Every shuffle keeps
  the real group sizes, in every stratum.
* :func:`sign_flip_test` — flip the signs of deviations from a null value
  and locate the observed one-sample t.
* :func:`parametric_family` — the parametric companion: the same t referred
  to a t distribution (Satterthwaite df built per stratum × group cell, which
  is Welch–Satterthwaite with one stratum), Holm-adjusted when a comparison
  holds more than one pair.

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
# The statistic: replicate-adjusted Welch t (blocked contrasts, HC2 SEs)
# ---------------------------------------------------------------------------

class _BlockedContrasts:
    """Pairwise group contrasts in Value ~ Stratum + Group with HC2 SEs.

    Built once per test. Shuffles move labels only within a stratum, so every
    stratum × group cell count — and with it (X'X)^-1, each cell's leverage
    and each contrast's weight on a cell — is the same for every relabelling;
    a shuffle costs a few matrix products. With one stratum the contrast is
    the difference of the two group means and its HC2 variance is
    s²_A/n_A + s²_B/n_B: Welch's t exactly.
    """

    def __init__(self, x, groups, strata, pairs):
        self.x = np.asarray(x, dtype=float)
        self.rep = np.asarray(strata)
        self.pairs = list(pairs)
        g = np.asarray(groups)
        n_strata = int(self.rep.max()) + 1
        k = int(g.max()) + 1
        self.n_strata, self.k = n_strata, k
        counts = np.zeros((n_strata, k))
        np.add.at(counts, (self.rep, g), 1)
        self.counts = counts
        n_par = n_strata + k - 1           # stratum intercepts + group effects 1..k-1
        xtx = np.zeros((n_par, n_par))
        xtx[np.arange(n_strata), np.arange(n_strata)] = counts.sum(1)
        for lv in range(1, k):
            col = n_strata + lv - 1
            xtx[col, col] = counts[:, lv].sum()
            xtx[:n_strata, col] = xtx[col, :n_strata] = counts[:, lv]
        self.xtx_inv = np.linalg.pinv(xtx)
        # The design vector of a cell, and each pair's contrast vector.
        cell_x = np.zeros((n_strata, k, n_par))
        for r in range(n_strata):
            cell_x[r, :, r] = 1.0
            for lv in range(1, k):
                cell_x[r, lv, n_strata + lv - 1] = 1.0
        contrasts = np.zeros((len(self.pairs), n_par))
        for c, (i, j) in enumerate(self.pairs):
            if j > 0:
                contrasts[c, n_strata + j - 1] += 1.0
            if i > 0:
                contrasts[c, n_strata + i - 1] -= 1.0
        self.contrasts = contrasts
        ## A pair never recorded in a shared stratum has no within-stratum
        ## difference to estimate: its contrast is not estimable.
        self.estimable = np.all(np.isclose(contrasts @ self.xtx_inv @ xtx,
                                           contrasts, atol=1e-8), axis=1)
        leverage = np.einsum("rkp,pq,rkq->rk", cell_x, self.xtx_inv, cell_x)
        weight = np.einsum("cp,pq,rkq->crk", contrasts, self.xtx_inv, cell_x)
        free = 1.0 - leverage
        ## A cell whose flies alone determine a parameter has leverage 1 and
        ## residual 0: it carries no variance information.
        self.omega = np.where((counts > 0) & (free > 1e-12),
                              weight ** 2 / np.where(free > 1e-12, free, 1.0), 0.0)
        self.rep_sums = np.bincount(self.rep, weights=self.x, minlength=n_strata)
        self.rep_onehot = np.eye(n_strata)[self.rep]

    def _fit(self, labelling):
        """Contrast estimates and per-cell residual sums of squares, per row."""
        rows = labelling.shape[0]
        masks = [(labelling == lv).astype(float) for lv in range(self.k)]
        xty = np.empty((rows, self.n_strata + self.k - 1))
        xty[:, :self.n_strata] = self.rep_sums
        for lv in range(1, self.k):
            xty[:, self.n_strata + lv - 1] = masks[lv] @ self.x
        beta = xty @ self.xtx_inv
        alpha = beta[:, :self.n_strata]
        gamma = np.column_stack([np.zeros(rows), beta[:, self.n_strata:]])
        fitted = alpha[:, self.rep] + np.take_along_axis(
            gamma, labelling.astype(np.intp), axis=1)
        resid2 = (self.x[None, :] - fitted) ** 2
        sse = np.stack([(masks[lv] * resid2) @ self.rep_onehot
                        for lv in range(self.k)], axis=2)      # rows x strata x k
        return beta @ self.contrasts.T, sse

    def t(self, labelling):
        """Robust t per labelling row and pair (rows × pairs), oriented j − i."""
        est, sse = self._fit(labelling)
        var = np.einsum("crk,nrk->nc", self.omega, sse)
        se = np.sqrt(np.maximum(var, 0.0))
        with np.errstate(divide="ignore", invalid="ignore"):
            t = np.where(se > 0, est / np.where(se > 0, se, 1.0),
                         np.sign(est) * np.inf)
        ## No spread at all: identical estimates read t = 0, never a division
        ## artefact that outranks a real relabelling.
        t = np.where((se == 0) & (np.abs(est) < 1e-12), 0.0, t)
        return np.where(self.estimable[None, :], t, np.nan)

    def satterthwaite_df(self, labelling):
        """Per pair: df from each cell's share of the variance, every cell
        taken as χ² with n − 1 df — Welch–Satterthwaite with one stratum."""
        _est, sse = self._fit(labelling[None, :])
        parts = self.omega * sse[0][None, :, :]               # pairs x strata x k
        dof = np.maximum(self.counts - 1, 1)[None, :, :]
        num = parts.sum(axis=(1, 2)) ** 2
        den = (parts ** 2 / dof).sum(axis=(1, 2))
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(den > 0, num / np.where(den > 0, den, 1.0), np.inf)


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
    """Studentized permutation test of group effects, max-T across *pairs*.

    *values* (no NaN), *groups* (integer codes ``0..k-1``) and *strata*
    (codes; labels move only within a stratum, and each stratum is a fixed
    effect in the statistic) are aligned 1-D arrays. *pairs* lists ``(i, j)``
    code pairs, t oriented ``j − i`` (default: every pair, ``i < j``); *sides*
    gives each pair's sidedness. Every group needs at least two values.

    Returns ``{t, p, estimable, exact, relabellings, p_floor}`` — ``t``, ``p``
    and ``estimable`` per pair (p family-wise adjusted when there is more than
    one pair). A pair never recorded in a shared stratum is not estimable: its
    ``t`` and ``p`` are NaN and it is left out of the family.
    """
    x = np.asarray(values, dtype=float)
    g = np.asarray(groups, dtype=np.int16)
    st = (np.zeros(len(x), dtype=np.int32) if strata is None
          else np.unique(np.asarray(strata), return_inverse=True)[1])
    k = int(g.max()) + 1
    pairs = list(pairs or itertools.combinations(range(k), 2))
    sides = list(sides or [0] * len(pairs))
    ## Centring keeps the sums of squares well conditioned; it changes no t.
    model = _BlockedContrasts(x - x.mean(), g, st, pairs)
    live = [c for c in range(len(pairs)) if model.estimable[c]]

    t_obs = model.t(g[None, :])[0]
    obs = np.array([_oriented(t, side) for t, side in zip(t_obs, sides)])

    def max_stat(t):
        return np.max(np.column_stack(
            [_oriented(t[:, c], sides[c]) for c in live]), axis=1)

    def result(p, exact, relabellings, floor):
        return {"t": [float(v) for v in t_obs],
                "p": [float(p[c]) if model.estimable[c] else float("nan")
                      for c in range(len(pairs))],
                "estimable": [bool(e) for e in model.estimable],
                "exact": exact, "relabellings": int(relabellings),
                "p_floor": floor}

    if not live:
        return result(np.full(len(pairs), np.nan), False, 0, float("nan"))
    total = count_relabellings(g, st)
    hits = np.zeros(len(pairs), dtype=np.int64)
    if total <= shuffles:
        m = max_stat(model.t(_all_labellings(g, st)))
        for c in live:
            hits[c] = int(np.count_nonzero(_at_least(m, obs[c])))
        floor = float(np.count_nonzero(_at_least(m, m.max())) / total)
        return result(hits / total, True, total, floor)

    rng = np.random.default_rng(seed)
    done = 0
    while done < shuffles:
        size = min(_CHUNK, shuffles - done)
        m = max_stat(model.t(_shuffled_labellings(g, st, size, rng)))
        for c in live:
            hits[c] += int(np.count_nonzero(_at_least(m, obs[c])))
        done += size
    return result((hits + 1) / (shuffles + 1), False, shuffles,
                  1.0 / (shuffles + 1))


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

def parametric_family(values, groups, strata, pairs, sides) -> list[float]:
    """The parametric p of each pair's replicate-adjusted Welch t — a t
    distribution with Satterthwaite df, in the pair's sidedness, Holm-adjusted
    across the pairs when there is more than one. With one stratum this is
    Welch's t-test exactly. NaN for a pair that is not estimable."""
    from scipy import stats as sstats

    x = np.asarray(values, dtype=float)
    g = np.asarray(groups, dtype=np.int16)
    st = (np.zeros(len(x), dtype=np.int32) if strata is None
          else np.unique(np.asarray(strata), return_inverse=True)[1])
    model = _BlockedContrasts(x - x.mean(), g, st, pairs)
    t = model.t(g[None, :])[0]
    df = model.satterthwaite_df(g)
    raw = []
    for c, side in enumerate(sides):
        if not model.estimable[c] or np.isnan(t[c]):
            raw.append(float("nan"))
            continue
        if side > 0:
            p = sstats.t.sf(t[c], df[c])
        elif side < 0:
            p = sstats.t.cdf(t[c], df[c])
        else:
            p = 2 * sstats.t.sf(abs(t[c]), df[c])
        raw.append(float(p))
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
