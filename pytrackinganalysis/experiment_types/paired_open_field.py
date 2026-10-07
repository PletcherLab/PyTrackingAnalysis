"""The Paired Open Field Experiment Type — the second concrete type (ADR-0016).

Two flies of the same treatment share each tracking region of an open arena.
The experiment asks whether they engage socially (proximity, Encounters —
ADR-0014) and whether they are healthy and behaving normally (movement,
centrophobism, exploration — ADR-0015), with the Pair as the unit of analysis
(ADR-0013). The measures belong to the tracker and every pairwise project gets
them; this module is the policy on top: rigs, defaults, inclusion rules,
advisory flags, and the report.
"""

from __future__ import annotations

from .. import Parameters, config_validation
from .base import ExperimentType

#: Advisory flag kinds (``compute_advisory_flags``), in report order. Each
#: becomes a ``<kind>Flag`` column in the summary CSVs.
POSSIBLY_DEAD = "PossiblyDead"
ROI_CHECK = "ROICheck"

_FLAG_DESCRIPTIONS = {
    POSSIBLY_DEAD: ("stayed within one body length of one spot over the final "
                    "{minutes:g} min; if confirmed dead, declare the region "
                    "removed"),
    ROI_CHECK: ("the ROI drawn in DTrack does not match where the flies went "
                "(more than {tol:g} mm off); centrophobism and exploration "
                "depend on it"),
}


class PairedOpenFieldExperimentType(ExperimentType):
    name = "PairedOpenField"
    display_name = "Paired Open Field Experiment"

    tracking_type = Parameters.TrackingType.PAIRWISEINTERACTIONTRACKER
    allowed_rigs = ("small_arena", "arena_max", "colosseum", "movie")
    facet_cutoffs = (10, 70)   # a default; the user may change it
    facets_fixed = False
    phase_labels = ("Acclimation", "Experiment", "Cooldown")
    required_counting_regions = None
    # Preset rigs take their preset calibration — every threshold here is in
    # mm, and a mistyped override would rescale them all. A Movie has no
    # preset, so it must supply fps and mm_per_pixel.
    allow_calibration_override = False
    calibration_override_rigs = ("movie",)
    # Plates come from the data: open-field pairs may run in inserts other
    # than the Valence plates, and the loader already requires exactly two
    # flies per region.
    region_counts = {}
    # The lab's established thresholds (ADR-0016).
    parameter_defaults = {"interaction_distances": [4, 8, 10]}
    # Low-Tracking Exclusion: a Pair tracked for under this fraction of the
    # Primary Phase is excluded. yaml `min_valid_fraction`; 0 turns it off.
    default_min_valid_fraction = 0.8
    #: Possibly-Dead Flag: the final stretch of the recording inspected (min).
    possibly_dead_window_min = 20.0
    #: ROI Check: how far (mm) the flies' reach may miss the ROI edge.
    roi_tolerance_mm = 1.0

    # ---- phases -------------------------------------------------------

    def _primary_window(self, experiment, global_cfg):
        """The Primary Phase window and its label, else the whole recording."""
        from .. import windowing

        cutoffs = getattr(experiment, "facet_cutoffs", None)
        if cutoffs:
            windows = list(windowing.facet_windows(cutoffs))
            primary = windows[1] if len(windows) >= 2 else windows[0]
            return primary, self.phase_labels_for(windows, global_cfg)[windows.index(primary)]
        return (0, 0), "whole recording"

    # ---- report text --------------------------------------------------

    def report_intro(self) -> str:
        return ("Pairs of flies in an open arena, two of the same treatment per "
                "region; every result counts Pairs, with each fly's own "
                "measures averaged across its Pair. Social behaviour is read "
                "from how much of the time the two flies spend near each other "
                "and how they meet (Encounters); general health and behaviour "
                "from movement, walking, centrophobism (avoiding the open centre "
                "of the arena) and exploration. The recording is split into "
                "Acclimation (0–10 min), Experiment (10–70 min, the phase the "
                "primary results are read from) and Cooldown (70+ min). "
                "Proximity is reported raw: two flies that both follow the wall "
                "meet more often than two that use the whole arena, so read "
                "interaction beside the Centrophobism Index.")

    def output_manifest(self) -> list[str]:
        return ["_Summary.csv", "_Summary_Facet.csv", "_Summary_PerFly.csv",
                "_Summary_Facet_PerFly.csv", "_Stats.txt", "_Excluded.csv"]

    def ai_summary_prompt(self) -> str:
        return super().ai_summary_prompt() + (
            "\n"
            "\nAssay context (Paired Open Field Experiment): two flies of the "
            "same treatment share each arena, and every number is per Pair "
            "(each fly's own measures are averaged across its Pair). Social "
            "behaviour: PercentInteracting_<d> is the fraction of tracked time "
            "the two flies spent within d mm, including frames where they "
            "touched and merged into one blob (MergedFraction says how much); "
            "Encounters are bouts of proximity, summarised as a rate, a mean "
            "duration and a latency. Health and behaviour: movement "
            "(TotalDistancePerMin), walking speed and bouts, the Centrophobism "
            "Index (-1..+1 over walking frames; positive = avoids the open "
            "centre; 0 = uses the arena uniformly) and exploration "
            "(ExplorationAUC, 0..1: how quickly the body footprint covered "
            "the arena). The primary results are read from the Experiment "
            "phase. Primary metrics are the headline; secondary metrics are "
            "exploratory. Proximity has no chance correction: flies that both "
            "hug the wall meet more often by chance, so interpret interaction "
            "together with centrophobism. Pairs excluded for low tracking are "
            "absent from all results; possibly-dead and ROI-check flags are "
            "advisory only (flagged flies stay in the results)."
        )

    def report_sections(self, experiment) -> list:
        from .. import report_figures
        return report_figures.build_paired_open_field_sections(experiment)

    # ---- inclusion: the Low-Tracking Exclusion -------------------------

    def compute_exclusions(self, experiment):
        """Pairs tracked for under ``min_valid_fraction`` of the Primary Phase
        (merged frames count as tracked — touching is not a tracking failure),
        including Pairs with no frames there. Both flies of a Pair go: the
        Pair is the unit (ADR-0013). Read from the trackers' pair measures
        directly, so excluding at load never pays for the open-field ones.
        """
        import pandas as pd

        from .. import removals

        global_cfg = (getattr(experiment, "config", None) or {}).get("global") or {}
        threshold = self.resolve_min_valid_fraction(global_cfg)
        primary, label = self._primary_window(experiment, global_cfg)

        columns = ["Name", "TrackingRegion", "Treatment", "ValidFraction", "Reason"]
        rows = []
        if threshold and threshold > 0:
            for name, tracker in experiment.arena.trackers.items():
                try:
                    valid = pd.to_numeric(
                        pd.Series([tracker.get_pair_measures(primary)["ValidFraction"]]),
                        errors="coerce").iloc[0]
                except Exception:  # noqa: BLE001 — no pair data is no tracking
                    valid = float("nan")
                if pd.isna(valid) or valid < threshold:
                    rows.append([name, tracker.get_tracking_region_id(),
                                 experiment._tracker_treatment(name),
                                 None if pd.isna(valid) else float(valid),
                                 removals.LOW_TRACKING_REASON])
        excluded = pd.DataFrame(rows, columns=columns)

        excluded.attrs["min_valid_fraction"] = threshold
        excluded.attrs["window"] = tuple(primary)
        excluded.attrs["phase_label"] = label
        excluded.attrs["policy_label"] = "low tracking"
        share = f"{threshold:.0%}" if threshold else "0%"
        excluded.attrs["policy_cause"] = (
            f"in Pairs tracked for under {share} of the {label} phase")
        excluded.attrs["policy_none"] = (
            f"every Pair was tracked for at least {share} of the {label} phase, "
            "and no regions were removed" if threshold and threshold > 0 else
            "the low-tracking exclusion is off (min_valid_fraction = 0) and no "
            "regions were removed")
        return excluded

    # ---- advisory flags: Possibly-Dead and the ROI Check ---------------

    def roi_check(self, experiment):
        """Per region: how close both flies came to the ROI edge (ReachGap_mm)
        and what that says about the ROI. Covers every region — like data
        quality, it describes the recording, not the analysis population."""
        import numpy as np
        import pandas as pd

        from .. import openfield

        by_region: dict = {}
        for tracker in experiment.arena.trackers.values():
            by_region.setdefault(tracker.get_tracking_region_id(), []).append(tracker)
        rows = []
        for region, trackers in by_region.items():
            geometry = trackers[0].arena_geometry() \
                if hasattr(trackers[0], "arena_geometry") else None
            treatment = experiment._tracker_treatment(trackers[0].name)
            if geometry is None:
                rows.append([region, treatment, np.nan, "no arena geometry in the ROI sheet"])
                continue
            x = np.concatenate([t.rawdata["Xpos_mm"].to_numpy() for t in trackers])
            y = np.concatenate([t.rawdata["Ypos_mm"].to_numpy() for t in trackers])
            valid = np.concatenate([(t.rawdata["DataQuality"] == "High").to_numpy()
                                    for t in trackers])
            gap = openfield.reach_gap(x, y, valid, geometry)
            if np.isnan(gap):
                status = "no tracked positions"
            elif gap > self.roi_tolerance_mm:
                status = "flies never reach the ROI edge: ROI too large, or the Pair barely moved"
            elif gap < -self.roi_tolerance_mm:
                status = "positions outside the ROI: ROI too small or off-centre"
            else:
                status = "ok"
            rows.append([region, treatment, gap, status])
        return pd.DataFrame(rows, columns=["TrackingRegion", "Treatment",
                                           "ReachGap_mm", "Status"])

    def compute_advisory_flags(self, experiment):
        """The Possibly-Dead Flag (per fly, over the analysis population) and
        the ROI Check (per region). Never removes anything (ADR-0010: death is
        the experimenter's to declare)."""
        import numpy as np
        import pandas as pd

        from .. import openfield

        excluded = getattr(experiment.arena, "excluded_names", set()) or set()
        length = experiment.parameters.fly_length_mm
        minutes = self.possibly_dead_window_min
        rows = []
        for name, tracker in experiment.arena.trackers.items():
            if name in excluded:
                continue
            data = tracker.rawdata
            spread = openfield.terminal_spread(
                data["Xpos_mm"].to_numpy(), data["Ypos_mm"].to_numpy(),
                (data["DataQuality"] == "High").to_numpy(),
                data["Minutes"].to_numpy(), final_min=minutes)
            if np.isnan(spread):
                detail = f"no tracked position in the final {minutes:g} min"
            elif spread < length:
                detail = (f"stayed within {spread:.1f} mm of one spot over the "
                          f"final {minutes:g} min")
            else:
                continue
            rows.append([name, tracker.get_tracking_region_id(),
                         experiment._tracker_treatment(name), POSSIBLY_DEAD,
                         "fly", detail])

        roi = self.roi_check(experiment)
        for _, row in roi[roi["Status"] != "ok"].iterrows():
            gap = row["ReachGap_mm"]
            detail = row["Status"] + ("" if pd.isna(gap) else f" (reach gap {gap:+.1f} mm)")
            rows.append([row["TrackingRegion"], row["TrackingRegion"], row["Treatment"],
                         ROI_CHECK, "region", detail])

        flags = pd.DataFrame(rows, columns=["Name", "TrackingRegion", "Treatment",
                                            "Flag", "Level", "Detail"])
        flags.attrs["kinds"] = [POSSIBLY_DEAD, ROI_CHECK]
        flags.attrs["descriptions"] = {
            POSSIBLY_DEAD: _FLAG_DESCRIPTIONS[POSSIBLY_DEAD].format(minutes=minutes),
            ROI_CHECK: _FLAG_DESCRIPTIONS[ROI_CHECK].format(tol=self.roi_tolerance_mm),
        }
        flags.attrs["roi_check"] = roi
        return flags

    # ---- validation ---------------------------------------------------

    def validate(self, config: dict) -> list[str]:
        global_cfg = config.get("global") or {}
        problems: list[str] = []
        problems += self._validate_owned_fields(global_cfg)
        problems += self._validate_rig(global_cfg)
        if config_validation.normalize_rig(global_cfg.get("tracking_rig")) == "movie":
            for key in ("fps", "mm_per_pixel"):
                if global_cfg.get(key) is None:
                    problems.append(
                        f"{self.display_name}: a Movie rig has no preset, so "
                        f"'{key}' is required.")
        raw = global_cfg.get("min_valid_fraction")
        if raw is not None:
            ok = isinstance(raw, (int, float)) and not isinstance(raw, bool) \
                and 0 <= raw <= 1
            if not ok:
                problems.append(f"{self.display_name}: min_valid_fraction must be "
                                f"a number from 0 to 1 (0 turns the exclusion "
                                f"off); got {raw!r}.")
        if not config.get("tracking_regions"):
            problems.append(
                f"{self.display_name}: no tracking_regions defined — assign each "
                f"region's treatment before running.")
        return problems

    # ---- new-project scaffold -----------------------------------------

    def _knobs(self, min_valid_fraction=None) -> dict:
        """The yaml-overridable defaults, written into a new config so they
        are visible where users already edit settings (ADR-0016)."""
        p = Parameters.Parameters()
        knobs = {"interaction_distances": list(self.parameter_defaults["interaction_distances"])}
        for key in ("merge_distance_mm", "wall_zone_mm", "fly_length_mm",
                    "fly_width_mm", "encounter_hysteresis_mm", "encounter_gap_s",
                    "encounter_min_s"):
            value = getattr(p, key)
            knobs[key] = int(value) if float(value) == int(value) else value
        knobs["min_valid_fraction"] = (float(min_valid_fraction)
                                       if min_valid_fraction is not None
                                       else self.default_min_valid_fraction)
        return knobs

    def scaffold_config(self) -> dict:
        # Rig left blank: the user must choose one, so a fresh project fails
        # validation until they do.
        return {
            "global": {"experiment_type": self.name, "tracking_rig": "",
                       **self._knobs()},
            "tracking_regions": {},
        }

    def build_config(self, *, rig=None, facet_cutoffs=None, facet_labels=None,
                     factors=None, min_valid_fraction=None, **_) -> dict:
        """Full Paired Open Field config for the create wizard. The plate is
        not fixed, so tracking regions start empty — they are filled from the
        data's regions in the Config Editor."""
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
        g.update(self._knobs(min_valid_fraction))
        return {"global": g, "tracking_regions": {}}
