"""The Valence Experiment Type — the first concrete type.

A two-choice light-preference assay: a two-choice tracker with Light vs NoLight
counting regions on an Arena Max, Colosseum, or Small Arena rig, with a fixed
three-phase structure (acclimation / experiment / cooldown from cutoffs
[10, 70]). See ``CONTEXT.md`` and ADR-0001.
"""

from __future__ import annotations

from .. import Parameters, config_validation
from .base import ExperimentType

# Default counting-region aliases for a fresh Valence project (editable later).
_LIGHT_ALIAS = "Light, Left1, Left2, Left3, Right4, Right5, Right6"
_NOLIGHT_ALIAS = "NoLight, Right1, Right2, Right3, Left4, Left5, Left6"
# Arena Max: the first 18 wells face the opposite way, so their X axis is
# flipped (-1) to orient Light consistently; the rest are +1. Colosseum: all +1.
_MAX_XFLIP_COUNT = 18


def _clean_number(value):
    """Whole numbers as ints (140.0 -> 140) so the yaml stays tidy."""
    return int(value) if float(value) == int(value) else float(value)


class ValenceExperimentType(ExperimentType):
    name = "Valence"
    display_name = "Valence Experiment"

    tracking_type = Parameters.TrackingType.TWOCHOICETRACKER
    allowed_rigs = ("arena_max", "colosseum", "small_arena")
    facet_cutoffs = (10, 70)   # a default; the user may change it (facets_fixed=False)
    facets_fixed = False
    phase_labels = ("Acclimation", "Experiment", "Cooldown")
    required_counting_regions = ("Light", "NoLight")
    allow_calibration_override = False
    # The plate is fixed by the rig: Arena Max has 36 wells (T_0..T_35).
    # The Colosseum is run at two sizes — 24 wells (T_0..T_23) and 18
    # (T_0..T_17) — and both validate; 24 is first, so it is the plate a
    # config built from scratch is laid out with. The Small Arena is one
    # 6-well unit per recording (T_0..T_5); the lab's four physical units
    # share this one configuration and are never named in a config.
    region_counts = {"arena_max": 36, "colosseum": (24, 18),
                     "small_arena": 6}
    # Low-Transition Exclusion (ADR-0003): flies with fewer than this many
    # transitions during the Primary Phase are excluded from every result.
    # yaml `min_transitions` overrides; 0 turns the exclusion off.
    default_min_transitions = 5
    # Low-Movement Flag: flies averaging less than this movement (mm/min)
    # during the first facet window are reported as potentially an issue —
    # never removed. When more than half of the analysed flies are flagged the
    # whole experiment is noted as potentially an issue.
    # yaml `min_movement` overrides; 0 turns the flagging off.
    default_min_movement = 140.0

    def tracking_regions_for_rig(self, rig) -> dict | None:
        """The Valence plate: on Arena Max the first 18 wells (T_0..T_17) face
        the opposite way, so their X axis is flipped to -1 and the remaining
        18 are +1; the Colosseum (either size) and the Small Arena have no
        mirrored wells, so they are all +1."""
        names = self.regions_for_rig(rig)
        if names is None:
            return None
        flip = (_MAX_XFLIP_COUNT
                if config_validation.normalize_rig(rig) == "arena_max" else 0)
        return {name: {"x_location_multiplier": -1 if i < flip else 1,
                       "y_location_multiplier": 1}
                for i, name in enumerate(names)}

    def report_intro(self) -> str:
        return ("A two-choice light-preference (valence) assay. Positive PI means "
                "the animals prefer Light over NoLight. The recording is split into "
                "three phases: Acclimation (0–10 min), Experiment (10–70 min, the "
                "phase the primary result is read from), and Cooldown (70+ min).")

    def output_manifest(self) -> list[str]:
        # The data outputs a Valence run is expected to produce in analysis/.
        # _Excluded.csv is written even when nothing is excluded, so its absence
        # never needs interpreting (ADR-0003).
        return ["_Summary.csv", "_Summary_Facet.csv", "_Stats.txt",
                "_Excluded.csv"]

    def compute_exclusions(self, experiment):
        """The Low-Transition Exclusion (ADR-0003): flies with fewer than
        ``min_transitions`` transitions during the Primary Phase, including
        flies with no data there at all (``Transitions`` NA).

        Returns a DataFrame (possibly empty) of Name / TrackingRegion /
        Treatment / Transitions rows, with the threshold and window recorded in
        ``.attrs`` for the report, Stats preamble, and Excluded.csv.
        """
        import pandas as pd

        from .. import windowing

        global_cfg = (getattr(experiment, "config", None) or {}).get("global") or {}
        threshold = self.resolve_min_transitions(global_cfg)

        cutoffs = getattr(experiment, "facet_cutoffs", None)
        if cutoffs:
            windows = list(windowing.facet_windows(cutoffs))
            primary = windows[1] if len(windows) >= 2 else windows[0]
            label = self.phase_labels_for(windows, global_cfg)[windows.index(primary)]
        else:
            primary, label = (0, 0), "whole recording"

        columns = ["Name", "TrackingRegion", "Treatment", "Transitions"]
        excluded = pd.DataFrame(columns=columns)
        if threshold and threshold > 0:
            summary = experiment.arena.summarize(range_minutes=primary,
                                                 include_excluded=True)
            transitions = pd.to_numeric(summary.get("Transitions"), errors="coerce")
            mask = transitions.isna() | (transitions < threshold)
            kept_cols = [c for c in columns if c in summary.columns]
            excluded = summary.loc[mask, kept_cols].reset_index(drop=True)
        excluded.attrs["min_transitions"] = threshold
        excluded.attrs["window"] = tuple(primary)
        excluded.attrs["phase_label"] = label
        return excluded

    def compute_movement_flags(self, experiment):
        """The Low-Movement Flag: flies whose average movement
        (``TotalDistancePerMin``) during the *first* facet window is below
        ``min_movement`` (default 140 mm/min), including flies with no data
        there. Flagged flies stay in every result — this is a report, not a
        removal — and when more than half of the analysed flies are flagged
        the experiment as a whole is noted as potentially an issue.

        Computed over the analysis population (after the Low-Transition
        Exclusion), so the >50% rule describes the flies the results are
        actually built from. Returns a DataFrame (possibly empty) with the
        threshold, window, flagged fraction, and experiment-level verdict in
        ``.attrs``.
        """
        import pandas as pd

        from .. import windowing

        global_cfg = (getattr(experiment, "config", None) or {}).get("global") or {}
        threshold = self.resolve_min_movement(global_cfg)

        cutoffs = getattr(experiment, "facet_cutoffs", None)
        if cutoffs:
            windows = list(windowing.facet_windows(cutoffs))
            first = windows[0]
            label = self.phase_labels_for(windows, global_cfg)[0]
        else:
            first, label = (0, 0), "whole recording"

        columns = ["Name", "TrackingRegion", "Treatment", "TotalDistancePerMin"]
        flagged = pd.DataFrame(columns=columns)
        total = 0
        if threshold and threshold > 0:
            summary = experiment.arena.summarize(range_minutes=first)
            movement = pd.to_numeric(summary.get("TotalDistancePerMin"),
                                     errors="coerce")
            mask = movement.isna() | (movement < threshold)
            total = len(summary)
            kept_cols = [c for c in columns if c in summary.columns]
            flagged = summary.loc[mask, kept_cols].reset_index(drop=True)
        fraction = (len(flagged) / total) if total else 0.0
        flagged.attrs["min_movement"] = threshold
        flagged.attrs["window"] = tuple(first)
        flagged.attrs["phase_label"] = label
        flagged.attrs["n_total"] = total
        flagged.attrs["fraction"] = fraction
        flagged.attrs["experiment_flagged"] = bool(total and fraction > 0.5)
        return flagged

    def ai_summary_prompt(self) -> str:
        return super().ai_summary_prompt() + (
            "\n"
            "\nAssay context (Valence Experiment): a two-choice "
            "light-preference assay. The Preference Index (PI) runs from -1 "
            "to +1; positive PI means the animals prefer Light over NoLight. "
            "The primary result is the PI during the Experiment phase "
            "(the middle phase); Acclimation precedes it and Cooldown follows. "
            "Flies excluded by the low-transition criterion are absent from "
            "all results; low-movement flags are advisory only (flagged flies "
            "stay in the results)."
        )

    def report_sections(self, experiment) -> list:
        # Valence-first blocks: headline Experiment-phase PI, PI over time,
        # per-animal persistence. Built in report_figures so matplotlib stays
        # out of the experiment_types package; imported lazily for the same
        # reason.
        from .. import report_figures
        return report_figures.build_valence_sections(experiment)

    def validate(self, config: dict) -> list[str]:
        global_cfg = config.get("global") or {}
        problems: list[str] = []
        problems += self._validate_owned_fields(global_cfg)
        problems += self._validate_rig(global_cfg)
        problems += self._validate_min_transitions(global_cfg)
        problems += self._validate_min_movement(global_cfg)
        problems += self._validate_required_counting_regions(config)
        if not config.get("tracking_regions"):
            problems.append(
                f"{self.display_name}: no tracking_regions defined — assign each "
                f"region's treatment before running.")
        else:
            problems += self._validate_required_tracking_regions(config)
        return problems

    def _validate_min_transitions(self, global_cfg: dict) -> list[str]:
        raw = global_cfg.get("min_transitions")
        if raw is None:
            return []
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = -1.0
        if value < 0 or value != int(value):
            return [f"{self.display_name}: min_transitions must be a whole "
                    f"number ≥ 0 (0 turns the exclusion off); got {raw!r}."]
        return []

    def _validate_min_movement(self, global_cfg: dict) -> list[str]:
        raw = global_cfg.get("min_movement")
        if raw is None:
            return []
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = -1.0
        if value < 0:
            return [f"{self.display_name}: min_movement must be a number ≥ 0 "
                    f"in mm/min (0 turns the flagging off); got {raw!r}."]
        return []

    def scaffold_config(self) -> dict:
        # Rig is intentionally left blank: the user must choose one of the
        # allowed rigs, so a freshly scaffolded project fails validation
        # until they do.
        return {
            "global": {
                "experiment_type": self.name,
                "tracking_rig": "",
                "min_transitions": self.default_min_transitions,
                "min_movement": _clean_number(self.default_min_movement),
            },
            "counting_regions": {
                "Light": {"alias": _LIGHT_ALIAS},
                "NoLight": {"alias": _NOLIGHT_ALIAS},
            },
            "tracking_regions": {},
        }

    def build_config(self, *, rig=None, facet_cutoffs=None, facet_labels=None,
                     factors=None, min_transitions=None, min_movement=None,
                     **_) -> dict:
        """Full Valence config for the create wizard.

        Lays out the rig's DEFAULT plate with the correct X multipliers (Arena
        Max flips the first 18 wells; the Colosseum — all +1, 24 wells; an
        18-well Colosseum is equally valid but has to be chosen in the Config
        Editor — and the 6-well Small Arena have no flips), the Light/NoLight
        counting regions, the chosen (or default) facets with their phase names
        (Acclimation/Experiment/Cooldown by default), and any design factors.
        Region treatments are left blank for the user to assign.
        """
        g: dict = {"experiment_type": self.name}
        if rig:
            g["tracking_rig"] = rig
        cutoffs = facet_cutoffs if facet_cutoffs else self.facet_cutoffs
        g["facet_cutoffs"] = self._clean_cutoffs(cutoffs)
        labels = self._default_facet_labels(cutoffs, facet_labels)
        if labels:
            g["facet_labels"] = labels
        if factors:
            g["experimental_design_factors"] = dict(factors)
        # The Low-Transition Exclusion and Low-Movement Flag are written
        # explicitly so the knobs are visible where users already edit facets
        # (ADR-0003).
        g["min_transitions"] = int(min_transitions) if min_transitions is not None \
            else self.default_min_transitions
        g["min_movement"] = _clean_number(min_movement) if min_movement is not None \
            else _clean_number(self.default_min_movement)

        ## One source for the plate's geometry — the Config Editor lays out
        ## the same wells the same way (``tracking_regions_for_rig``).
        tracking_regions = {
            name: {"experimental_factors": "", **multipliers}
            for name, multipliers in
            (self.tracking_regions_for_rig(rig) or {}).items()
        }
        return {
            "global": g,
            "counting_regions": {
                "Light": {"alias": _LIGHT_ALIAS},
                "NoLight": {"alias": _NOLIGHT_ALIAS},
            },
            "tracking_regions": tracking_regions,
        }
