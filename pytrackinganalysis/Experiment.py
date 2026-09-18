import glob
import io
import logging
import os
import sys
import time
import matplotlib.patches as patches
import matplotlib.pyplot as plt
import pandas as pd
import yaml
from . import Parameters
from . import Arena
from . import config_validation
from . import experiment_types
from . import io_utils
from . import removals

## The rig spellings the config accepts. config_validation owns this table so
## that the checker and the loader can never disagree about which rigs exist —
## Arena used to carry a third copy behind a dead code path.
_RIG_MAP = config_validation.RIG_ALIASES

## global: keys that are forwarded to Parameters.set() as explicit overrides.
_PARAMETER_KEYS = {
    'fps', 'mm_per_pixel', 'speed_window_seconds',
    'micromove_speed_mm_sec', 'walking_speed_mm_sec',
    'sleep_threshold_min', 'interaction_distances',
}

logger = logging.getLogger(__name__)

# Maps TrackingType to the plot methods (and kwargs) that are relevant for it
_TRACKING_TYPE_PLOTS = {
    Parameters.TrackingType.TRACKER: [
        ('plot_totaldistance_facet', {}),
    ],
    ## Both are plain tracking-class types whose trackers produce the standard
    ## Tracker summary, so they get the generic distance policy. Leaving them out
    ## of these tables made run_analysis emit a zero-byte Stats.txt and no plots
    ## while still printing "=== Done. ===" and recording 'ok' in batch mode.
    Parameters.TrackingType.DDROPTRACKER: [
        ('plot_totaldistance_facet', {}),
    ],
    Parameters.TrackingType.CENTROPHOBISMTRACKER: [
        ('plot_totaldistance_facet', {}),
    ],
    Parameters.TrackingType.TWOCHOICETRACKER: [
        ('plot_pi_facet', {}),
        ('plot_percentage_facet', {}),
        ('plot_transitions_facet', {}),
        ('plot_totaldistance_facet', {}),
    ],
    Parameters.TrackingType.TWOCHOICECOUNTER: [
        ('plot_pi_facet', {}),
        ('plot_percentage_facet', {}),
    ],
    Parameters.TrackingType.XCHOICETRACKER: [
        ('plot_adjusted_x_position_facet', {}),
        ('plot_totaldistance_facet', {}),
    ],
    Parameters.TrackingType.PAIRWISEINTERACTIONTRACKER: [
        ('plot_interactions_facet', {}),
        ('plot_totaldistance_facet', {}),
    ],
    Parameters.TrackingType.PAIRWISEINTERACTIONCOUNTER: [
        ('plot_interactions_facet', {}),
    ],
}

# Maps TrackingType to the metrics used in run_pairwise_comparisons_facet
_TRACKING_TYPE_METRICS = {
    Parameters.TrackingType.TRACKER:                   ['TotalDistancePerMin'],
    Parameters.TrackingType.DDROPTRACKER:              ['TotalDistancePerMin'],
    Parameters.TrackingType.CENTROPHOBISMTRACKER:      ['TotalDistancePerMin'],
    Parameters.TrackingType.TWOCHOICETRACKER:          ['FinalPI', 'FinalPercentage', 'TotalDistancePerMin'],
    Parameters.TrackingType.TWOCHOICECOUNTER:          ['FinalPI', 'FinalPercentage'],
    Parameters.TrackingType.XCHOICETRACKER:            ['AvgAdjX_mm', 'TotalDistancePerMin'],
    Parameters.TrackingType.PAIRWISEINTERACTIONTRACKER: None,   # built at runtime from interaction distances
    Parameters.TrackingType.PAIRWISEINTERACTIONCOUNTER: None,
    ## A plain COUNTER summary carries no outcome metric — only Treatment, Name,
    ## TrackingRegion and the observed-minutes bounds — so there is genuinely
    ## nothing to compare. Listed explicitly so it reads as a decision rather
    ## than an omission; stats() says so out loud instead of writing an empty file.
    Parameters.TrackingType.COUNTER:                   [],
}

## One-line reader-facing descriptions for the metrics stats() compares, used
## as section headings in *_Stats.txt so a reader never has to guess what a
## column name measures. PercentInteracting_<d> is described at runtime.
_METRIC_DESCRIPTIONS = {
    'FinalPI': ('final preference index, -1..+1: (frames in counting region 1 '
                '- frames in region 2) / (frames in either). Positive = '
                'preference for region 1 (Light in a Valence Experiment); '
                '0 = indifference.'),
    'FinalPercentage': ('fraction of scored frames spent in counting region 1 '
                        '(Light in a Valence Experiment), 0..1; 0.5 = '
                        'indifference.'),
    'TotalDistancePerMin': ('distance travelled per observed minute (mm/min); '
                            'locomotor activity, typically read as a control.'),
    'AvgAdjX_mm': ('mean polarity-adjusted X position (mm); positive = toward '
                   'the side the design file orients as positive.'),
}


class Experiment:
    """
    Primary organizer for a tracking experiment.

    Reads ``tracking_config.yaml`` from the project directory to configure
    Parameters, then loads data from the ``data/`` subdirectory via Arena.
    Analysis outputs go to ``analysis/`` and QC outputs to ``qc/``.

    Directory layout expected::

        project_directory/
            tracking_config.yaml    ← experiment configuration
            data/                   ← input xlsx + csv files  (must already exist)
            analysis/               ← created automatically on load
            qc/                     ← created automatically on load

    Typical usage::

        exp = Experiment('/path/to/project')
        exp.experiment_summary()
        exp.qc()
        exp.run_analysis()          # QC + summaries + plots + stats in one call

    All Arena methods are accessible directly on the Experiment object via
    attribute delegation, so ``exp.summarize()`` is the same as
    ``exp.arena.summarize()``.

    YAML global section keys recognised
    ------------------------------------
    tracking_type   : TrackingType enum name (e.g. ``TWOCHOICETRACKER``)
    tracking_rig    : Rig preset name (small_arena, arena_max, colosseum, obscura, movie)
    facet_cutoffs   : Optional list of minute boundaries for faceted analysis, e.g. [10, 70]
    fps             : Override fps after rig preset is applied
    mm_per_pixel    : Override mm_per_pixel after rig preset is applied
    speed_window_seconds, micromove_speed_mm_sec, walking_speed_mm_sec,
    sleep_threshold_min, interaction_distances : other parameter overrides
    """

    def __init__(self, project_directory: str, force_preprocessing: bool = False,
                 config_path: str | None = None):
        """
        Parameters
        ----------
        project_directory :
            Root directory for the experiment. Must contain ``tracking_config.yaml``
            and a ``data/`` subdirectory with the experiment xlsx and csv files.
        force_preprocessing :
            Passed through to Arena; forces re-computation of nearest-neighbour
            pre-processing when True.
        config_path :
            Alternative config file to load instead of
            ``<project_directory>/tracking_config.yaml``. A relative path is
            resolved against *project_directory*. The Hub offers a config
            selector, and without this parameter the filename was joined on
            unconditionally, so choosing a second config silently loaded the
            *other* file's tracking type, rig calibration and cutoffs.
        """
        self.project_directory = os.path.abspath(project_directory)
        self.data_path     = self._find_subdir('data') + '/'
        self.analysis_path = os.path.join(self.project_directory, 'analysis') + '/'
        self.qc_path       = os.path.join(self.project_directory, 'qc') + '/'
        if config_path is None:
            self.config_path = os.path.join(self.project_directory, 'tracking_config.yaml')
        else:
            self.config_path = config_path if os.path.isabs(config_path) \
                else os.path.join(self.project_directory, config_path)

        # Create output directories (data/ must already exist with files in it)
        os.makedirs(self.analysis_path, exist_ok=True)
        os.makedirs(self.qc_path, exist_ok=True)

        self.config = self._load_config()
        ## The Experiment Type (absent -> Custom) owns the fixed fields and its
        ## own constraints. Resolve it before anything reads tracking_type or
        ## facets, so those come from the type for a typed experiment (ADR-0001).
        self.experiment_type = experiment_types.get_experiment_type(
            (self.config.get('global') or {}).get('experiment_type'))
        self._validate_config()
        self.parameters = self._build_parameters()

        ## Excel writes a '~$Name.xlsx' lock file next to any workbook that is
        ## currently open. Globbing unsorted and unfiltered picked it up, and the
        ## experiment died with "Excel file format cannot be determined" — for
        ## the entirely ordinary reason that the user had the sheet open.
        ## Sorting also makes the choice deterministic when several are present.
        xlsx_files = sorted(
            ## glob.escape: a bracket anywhere in the path (a 'Trial [2]'
            ## folder) makes the pattern a character class and matches nothing.
            f for f in glob.glob(os.path.join(glob.escape(self.data_path), '*.xlsx'))
            if not os.path.basename(f).startswith(('~$', '.'))
        )
        if not xlsx_files:
            raise FileNotFoundError(f"No .xlsx file found in {self.data_path}")
        if len(xlsx_files) > 1:
            logger.warning(
                "Multiple .xlsx files in %s; using '%s'. Others: %s",
                self.data_path, os.path.basename(xlsx_files[0]),
                ", ".join(os.path.basename(f) for f in xlsx_files[1:]),
            )
        exp_name = os.path.splitext(os.path.basename(xlsx_files[0]))[0]

        self.arena = Arena.Arena(
            exp_name, self.data_path, self.parameters,
            config_path=self.config_path,
            force_preprocessing=force_preprocessing,
        )

        # Facet cutoffs: fixed by the Experiment Type for a typed experiment,
        # otherwise read from the global: section (Custom). e.g. [10, 70].
        self.facet_cutoffs: tuple | None = self.experiment_type.resolve_facet_cutoffs(
            self.config.get('global', {}))

        ## Excluded Flies: the Experiment Type's own criterion (ADR-0003, None
        ## when it has none) merged with the experimenter's declared Removed
        ## Regions (ADR-0010). One list, enforced once by Arena, so plots,
        ## stats and CSVs can never disagree about the population. Computed
        ## eagerly at load — interactive arena calls must be filtered too, not
        ## just the pipeline entry points.
        self.refresh_exclusions(announce=True)

        ## Low-Movement Flag: reported, never removed. Computed after the
        ## exclusion so the >50% experiment-level rule describes the analysis
        ## population the results are actually built from.
        self.flagged_flies = self.experiment_type.compute_movement_flags(self)
        if self.flagged_flies is not None and len(self.flagged_flies):
            attrs = self.flagged_flies.attrs
            note = (f"Flagged {len(self.flagged_flies)}/{attrs.get('n_total')} "
                    f"fly(ies) as potentially an issue: movement below "
                    f"{attrs.get('min_movement'):g} mm/min during the "
                    f"{attrs.get('phase_label')} phase (kept in all results).")
            if attrs.get('experiment_flagged'):
                note += (" More than half of the flies are flagged — the "
                         "experiment itself is potentially an issue.")
            print(note)

    # ------------------------------------------------------------------
    # Excluded Flies: type criterion + experimenter-declared removals
    # (ADR-0003, ADR-0010)
    # ------------------------------------------------------------------

    def read_removed_regions(self) -> dict:
        """The experimenter's declared Removed Regions, ``{region: reason}``."""
        return removals.read_removals(self.project_directory)

    def write_removed_regions(self, declared: dict) -> str | None:
        """Persist the declaration and re-apply exclusions immediately.

        Re-applying in memory is what makes the removals window feel real: the
        Arena's excluded set was computed at load, so without this a fly the
        user just removed would keep appearing in plots until the next full
        analysis (ADR-0010).
        """
        path = removals.write_removals(self.project_directory, declared)
        self.refresh_exclusions()
        return path

    def refresh_exclusions(self, announce: bool = False) -> None:
        """Recompute the exclusion list and hand it to Arena."""
        self.excluded_flies = self._build_exclusions()
        excluded = self.excluded_flies
        names = excluded['Name'] if excluded is not None and len(excluded) else []
        self.arena.set_excluded_trackers(names)
        if announce:
            for line in self.exclusion_notes():
                print(line)

    def exclusion_notes(self) -> list:
        """Human-readable lines about the current exclusions: what was removed
        and why, plus a warning per declaration that matched no tracker."""
        excluded = getattr(self, 'excluded_flies', None)
        lines = []
        if excluded is not None and len(excluded):
            parts = []
            attrs = excluded.attrs
            n_removed = attrs.get('n_removed', 0)
            n_low = attrs.get('n_low_transitions', 0)
            if n_removed:
                parts.append(f"{n_removed} removed by the experimenter")
            if n_low:
                parts.append(f"{n_low} with fewer than "
                             f"{attrs.get('min_transitions')} transitions during "
                             f"the {attrs.get('phase_label')} phase")
            detail = f": {', '.join(parts)}" if parts else ""
            lines.append(f"Excluded {len(excluded)} fly(ies){detail}.")
        for region in (excluded.attrs.get('unmatched_regions', [])
                       if excluded is not None else []):
            ## Loud, never fatal (ADR-0010): a stale note must not kill an
            ## unattended batch, but nobody may assume a fly was removed when
            ## nothing matched.
            lines.append(f"Warning: removed region {region} matches no tracker "
                         f"in this experiment (declared in "
                         f"{removals.REMOVALS_FILENAME}).")
        return lines

    def exclusion_summary(self) -> str:
        """One sentence naming the analysis population's losses and their
        causes — the shared wording for Stats.txt and the report cover."""
        excluded = getattr(self, 'excluded_flies', None)
        if excluded is None:
            return "none (no exclusion criterion, no regions removed)"
        attrs = excluded.attrs
        threshold = attrs.get('min_transitions')
        phase = attrs.get('phase_label', 'Primary')
        n_removed = attrs.get('n_removed', 0)
        n_low = attrs.get('n_low_transitions', 0)
        if not len(excluded):
            if threshold:
                return (f"none — every fly made at least {threshold} transitions "
                        f"during the {phase} phase, and no regions were removed")
            if threshold is not None:
                return ("none (min_transitions = 0, low-transition exclusion "
                        "off; no regions removed)")
            return "none (no regions removed)"
        parts = []
        if n_removed:
            parts.append(f"{n_removed} removed by the experimenter")
        if n_low:
            parts.append(f"{n_low} with fewer than {threshold} transitions "
                         f"during the {phase} phase")
        return (f"{len(excluded)} fly(ies) — {'; '.join(parts)} "
                f"(see _Excluded.csv)")

    def _build_exclusions(self):
        """Merge the type's exclusions with the declared Removed Regions.

        One row per fly (ADR-0010): a fly caught by both criteria appears once,
        its ``Reason`` naming both with the experimenter's observation first —
        an observation outranks an inferred rule.
        """
        import pandas as pd

        type_frame = self.experiment_type.compute_exclusions(self)
        declared = self.read_removed_regions()
        if type_frame is None and not declared:
            return None

        if type_frame is not None:
            columns = [c for c in type_frame.columns if c != 'Reason']
        else:
            ## Custom Experiments have no transition criterion, so no column
            ## for one — the file is narrower, never falsely empty.
            columns = ['Name', 'TrackingRegion', 'Treatment']
        rows = []
        by_name = {}
        if type_frame is not None:
            for _, row in type_frame.iterrows():
                record = {c: row.get(c) for c in columns}
                record['Reason'] = removals.LOW_TRANSITION_REASON
                rows.append(record)
                by_name[str(record.get('Name'))] = record

        expanded = removals.expand_regions(declared, self.arena.trackers.keys())
        transitions = self._primary_transitions(columns, type_frame)
        n_removed = 0
        for region, names in expanded.items():
            reason = f"{removals.REMOVAL_PREFIX}: {declared[region]}"
            for name in names:
                n_removed += 1
                record = by_name.get(name)
                if record is not None:
                    ## Both criteria fired: one row, both causes, the
                    ## experimenter's first.
                    record['Reason'] = f"{reason}; {record['Reason']}"
                    continue
                record = {c: None for c in columns}
                record['Name'] = name
                record['TrackingRegion'] = region
                record['Treatment'] = self._tracker_treatment(name)
                if 'Transitions' in columns:
                    record['Transitions'] = transitions.get(name)
                record['Reason'] = reason
                rows.append(record)
                by_name[name] = record

        frame = pd.DataFrame(rows, columns=columns + ['Reason'])
        if type_frame is not None:
            frame.attrs.update(dict(type_frame.attrs))
        frame.attrs['removed_regions'] = dict(declared)
        frame.attrs['unmatched_regions'] = [region for region, names
                                            in expanded.items() if not names]
        frame.attrs['n_removed'] = n_removed
        frame.attrs['n_low_transitions'] = len(frame) - n_removed
        return frame

    def _primary_transitions(self, columns, type_frame) -> dict:
        """``{tracker name: Transitions}`` in the Primary Phase, for the audit.

        A removed fly's transition count is worth recording even though it was
        not what removed it. The summary is already cached from the type's own
        exclusion pass, so this is a lookup rather than a second computation;
        with no criterion (or the threshold off) nothing was computed and the
        column stays NA rather than paying for it here.
        """
        if 'Transitions' not in columns or type_frame is None:
            return {}
        if not type_frame.attrs.get('min_transitions'):
            return {}
        window = type_frame.attrs.get('window')
        if window is None:
            return {}
        try:
            summary = self.arena.summarize(range_minutes=tuple(window),
                                           include_excluded=True)
            return dict(zip(summary['Name'].astype(str),
                            summary['Transitions']))
        except Exception:  # noqa: BLE001 — the audit must not break a load
            return {}

    def _tracker_treatment(self, name: str) -> str:
        """The treatment of a tracker, read from its own region design."""
        tracker = self.arena.trackers.get(name)
        design = getattr(tracker, 'tracking_region_design', None)
        try:
            return str(design['Treatment'].iloc[0])
        except Exception:  # noqa: BLE001
            return ''

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_subdir(self, name: str) -> str:
        """Return the path of a subdirectory matching *name* (case-insensitive).

        Falls back to the lowercase name if no match exists (letting later code
        raise a clear error about missing files).
        """
        try:
            entries = os.listdir(self.project_directory)
        except OSError:
            return os.path.join(self.project_directory, name)
        for entry in entries:
            if entry.lower() == name.lower() and os.path.isdir(
                os.path.join(self.project_directory, entry)
            ):
                return os.path.join(self.project_directory, entry)
        return os.path.join(self.project_directory, name)

    def _load_config(self) -> dict:
        if not os.path.isfile(self.config_path):
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        with open(self.config_path, 'r') as f:
            return yaml.safe_load(f)

    def _validate_config(self) -> None:
        """Validate the config through its Experiment Type.

        ``config_validation`` catches unknown rigs, missing movie calibration,
        bad multipliers, mis-ordered cutoffs, and (for a typed experiment) the
        type's own constraints. For a **typed** experiment we fail hard (ADR-0001)
        — a Valence config that names the wrong rig or omits Light/NoLight should
        stop at load, not crash mid-analysis or silently produce wrong numbers.
        A **Custom** experiment stays lenient (its previous behaviour): a config
        can be imperfect and still analysable, and refusing to load it would be a
        breaking change for existing projects.
        """
        problems = config_validation.validate_config(self.config)
        if not problems:
            return
        if not self.experiment_type.is_custom:
            raise ValueError(
                f"{self.experiment_type.display_name} configuration has "
                f"{len(problems)} problem(s) in {self.config_path}:\n  - "
                + "\n  - ".join(problems)
            )
        logger.warning("Problems in %s:", self.config_path)
        print(f"Warning: {len(problems)} problem(s) in {self.config_path}:")
        for problem in problems:
            logger.warning("  %s", problem)
            print(f"  - {problem}")

    def _build_parameters(self) -> Parameters.Parameters:
        """Create a Parameters object from the global: section of the yaml."""
        global_cfg = self.config.get('global', {})

        ## Tracking type comes from the Experiment Type: fixed for a typed
        ## experiment (the yaml does not carry it), read from the yaml for Custom.
        tracking_type = self.experiment_type.resolve_tracking_type(global_cfg)

        # Resolve rig and apply preset
        rig_raw = global_cfg.get('tracking_rig', '').lower().replace(' ', '_').replace('-', '_')
        rig = _RIG_MAP.get(rig_raw, rig_raw)

        p = Parameters.Parameters()
        if rig == 'small_arena':
            p.set_small_arena_values(tracking_type)
        elif rig == 'arena_max':
            p.set_arena_max_values(tracking_type)
        elif rig in ('colosseum', 'colloseum'):
            p.set_colloseum_values(tracking_type)
        elif rig == 'obscura':
            p.set_obscura_values(tracking_type)
        elif rig == 'movie':
            ## A movie has no rig preset to fall back on. Guessing 30 fps / 0.1 mm
            ## per pixel would silently rescale every time and distance in the
            ## experiment, so require the real calibration instead.
            missing = [k for k in ('fps', 'mm_per_pixel') if global_cfg.get(k) is None]
            if missing:
                raise ValueError(
                    "tracking_rig 'movie' requires explicit calibration in the global: "
                    f"section of tracking_config.yaml. Missing: {', '.join(missing)}."
                )
            p.set_movie_values(tracking_type, global_cfg['fps'], global_cfg['mm_per_pixel'])
        else:
            p.set_tracking_type(tracking_type)

        # Apply any explicit parameter overrides present in global:
        overrides = {k: v for k, v in global_cfg.items() if k in _PARAMETER_KEYS}
        if overrides:
            p.set(**overrides)

        return p

    def _stats_metrics(self) -> list:
        """Return the metrics appropriate for pairwise comparison for this tracking type."""
        tt = self.parameters.get_tracking_type()
        if tt in (Parameters.TrackingType.PAIRWISEINTERACTIONTRACKER,
                  Parameters.TrackingType.PAIRWISEINTERACTIONCOUNTER):
            return [f"PercentInteracting_{d}" for d in self.parameters.interaction_distance_mm]
        return _TRACKING_TYPE_METRICS.get(tt, [])

    def _resolve_cutoffs(self, cutoffs):
        """Return cutoffs to use, preferring the explicit argument over ``self.facet_cutoffs``.

        Returns None when neither is set; callers skip faceted operations gracefully.
        """
        return cutoffs if cutoffs is not None else self.facet_cutoffs

    def _plot_methods(self) -> list:
        """Return (method_name, kwargs) pairs for all relevant plots.

        Injects ``self.facet_cutoffs`` into plot kwargs only when it is set.
        """
        tt = self.parameters.get_tracking_type()
        base = _TRACKING_TYPE_PLOTS.get(tt, [])
        result = []
        for name, kwargs in base:
            merged = dict(kwargs)
            if name.endswith('_facet') and 'cutoffs' not in merged and self.facet_cutoffs is not None:
                merged['cutoffs'] = self.facet_cutoffs
            result.append((name, merged))
        return result

    # ------------------------------------------------------------------
    # Delegation to Arena
    # ------------------------------------------------------------------

    def __getattr__(self, name: str):
        """Delegate attribute lookups to the underlying Arena object."""
        if name == 'arena':
            raise AttributeError(name)
        return getattr(self.arena, name)

    # ------------------------------------------------------------------
    # User-facing methods
    # ------------------------------------------------------------------

    def experiment_summary(self, save: bool = True) -> str:
        """Print a detailed overview of the experiment and optionally save it to analysis_path.

        Parameters
        ----------
        save :
            If True, writes ``{experiment_name}_experiment_summary.txt`` to analysis_path.

        Returns
        -------
        str
            The full info text.
        """
        ## Counter-class experiments have no per-frame DataQuality; the overview
        ## still reports timing and tracker counts for them.
        supports_dq = self.arena.supports_data_quality()
        dq         = self.arena.get_data_quality() if supports_dq else None
        if supports_dq:
            n_total = len(dq)
            n_good  = int((dq['HighQuality'] >= 0.9).sum())
            t_min   = dq['StartMinutes'].min()
            t_max   = dq['EndMinutes'].max()
        else:
            n_total = len(self.arena.trackers)
            n_good  = None
            minutes = [t.rawdata['Minutes'] for t in self.arena.trackers.values()
                       if 'Minutes' in t.rawdata.columns and len(t.rawdata)]
            t_min   = min((m.min() for m in minutes), default=float('nan'))
            t_max   = max((m.max() for m in minutes), default=float('nan'))
        global_cfg = self.config.get('global', {})
        p          = self.parameters
        cutoffs_str = str(self.facet_cutoffs) if self.facet_cutoffs is not None else 'Not set'
        total_frames = sum(len(t.rawdata) for t in self.arena.trackers.values())

        W = 54  # separator width
        sep = '─' * W

        lines = [
            f"=== Experiment: {self.arena.experiment_name} ===",
            "",
            f"── Paths {'─' * (W - 8)}",
            f"  Project   : {self.project_directory}",
            f"  Data      : {self.data_path}",
            f"  Analysis  : {self.analysis_path}",
            f"  QC        : {self.qc_path}",
            "",
            f"── Configuration {'─' * (W - 17)}",
            f"  Tracking type  : {global_cfg.get('tracking_type', 'Unknown')}",
            f"  Tracking rig   : {global_cfg.get('tracking_rig', 'Unknown')}",
            f"  Facet cutoffs  : {cutoffs_str}",
            f"  Design loaded  : {'Yes' if self.experimental_design is not None else 'No'}",
            "",
            f"── Parameters {'─' * (W - 14)}",
            f"  FPS                   : {p.fps}",
            f"  mm / pixel            : {p.mm_per_pixel}",
            f"  Speed window          : {p.speed_window_seconds} s",
            f"  Micro-move speed      : {p.micro_move_speed_mm_sec[0]} – {p.micro_move_speed_mm_sec[1]} mm/s",
            f"  Walking speed         : ≥ {p.walking_speed_mm_sec} mm/s",
            f"  Sleep threshold       : {p.sleep_threshold_min} min",
            f"  Interaction distances : {p.interaction_distance_mm} mm",
            "",
            *self._design_summary_lines(W),
            f"── Data Overview {'─' * (W - 17)}",
            f"  Total trackers        : {n_total}",
            (f"  Passing ≥90% quality  : {n_good} / {n_total}" if supports_dq
             else "  Passing ≥90% quality  : n/a (no per-frame quality for this tracking type)"),
            f"  Recording range       : {t_min:.1f} – {t_max:.1f} min",
            f"  Total data points     : {total_frames:,}",
            "",
            f"── Per-Tracker Summary {'─' * (W - 23)}",
        ]

        def _treatment_of(key):
            tracker = self.arena.trackers.get(key)
            if tracker is not None and tracker.tracking_region_design is not None:
                return str(tracker.tracking_region_design['Treatment'].iat[0])
            return ''

        if supports_dq:
            # Table header
            h = (f"  {'Tracker':<12}  {'Treatment':<22}  {'OK':<2}"
                 f"  {'HighQuality':>11}  {'NotFound':>8}  {'Indiscernible':>13}"
                 f"  {'Start':>6}  {'End':>6}")
            lines.append(h)
            lines.append(f"  {'-'*12}  {'-'*22}  {'-'*2}  {'-'*11}  {'-'*8}  {'-'*13}  {'-'*6}  {'-'*6}")

            for _, row in dq.iterrows():
                key = row['Tracker']
                ok = '✓' if row['HighQuality'] >= 0.9 else '✗'
                lines.append(
                    f"  {key:<12}  {_treatment_of(key):<22}  {ok:<2}"
                    f"  {row['HighQuality']:>10.1%}  {row['NotFound']:>8.1%}"
                    f"  {row['Indiscernible']:>13.1%}"
                    f"  {row['StartMinutes']:>6.1f}  {row['EndMinutes']:>6.1f}"
                )
        else:
            lines.append(f"  {'Region':<12}  {'Treatment':<22}  {'Rows':>10}  {'Start':>6}  {'End':>6}")
            lines.append(f"  {'-'*12}  {'-'*22}  {'-'*10}  {'-'*6}  {'-'*6}")
            for key, tracker in self.arena.trackers.items():
                minutes = tracker.rawdata['Minutes'] if 'Minutes' in tracker.rawdata.columns else None
                start = minutes.min() if minutes is not None and len(minutes) else float('nan')
                end = minutes.max() if minutes is not None and len(minutes) else float('nan')
                lines.append(
                    f"  {key:<12}  {_treatment_of(key):<22}  {len(tracker.rawdata):>10,}"
                    f"  {start:>6.1f}  {end:>6.1f}"
                )

        text = '\n'.join(lines)
        print(text)

        if save:
            path = os.path.join(self.analysis_path,
                                f"{self.arena.experiment_name}_experiment_summary.txt")
            with open(path, 'w', encoding='utf-8') as f:
                f.write(text)
            print(f"\nSaved: {path}")

        return text

    def _design_summary_lines(self, W: int) -> list:
        """Formatted description of the tracking_config.yaml design for the
        experiment summary: factors and their levels, factor assignments to
        tracking regions, non-unit location multipliers, counting regions with
        their aliases, and facet cutoffs."""
        import textwrap

        def _wrap(prefix: str, items: list) -> list:
            """One entry as ``prefix item, item, …`` wrapped to the summary width,
            continuation lines aligned under the first item."""
            return textwrap.wrap(
                prefix + ', '.join(str(i) for i in items),
                width=78,
                subsequent_indent=' ' * len(prefix),
            )

        lines = [f"── Experimental Design {'─' * (W - 23)}"]

        factors = self.config.get('global', {}).get('experimental_design_factors') or {}
        if factors:
            lines.append("  Factors:")
            name_w = max(len(str(n)) for n in factors)
            for name, levels in factors.items():
                if isinstance(levels, (list, tuple)):
                    levels_str = ', '.join(str(v) for v in levels)
                else:
                    levels_str = str(levels)
                lines.append(f"    {str(name):<{name_w}} : {levels_str}")
        else:
            lines.append("  Factors : none defined in config")

        ed = self.experimental_design
        tr = ed.tracking_regions if ed is not None else None
        if tr is not None and not tr.empty:
            lines.append("  Factor assignments (tracking regions):")
            for treatment, grp in tr.groupby('Treatment', sort=False):
                label = str(treatment).strip() or '(unassigned)'
                names = list(grp['RegionName'])
                lines.extend(_wrap(f"    {label} ({len(names)}): ", names))

            flipped = tr[(tr['XLocationMultiplier'] != 1) | (tr['YLocationMultiplier'] != 1)]
            if flipped.empty:
                lines.append("  Location multipliers : all 1 (no flips)")
            else:
                lines.append("  Location multipliers ≠ 1:")
                for (xm, ym), grp in flipped.groupby(
                    ['XLocationMultiplier', 'YLocationMultiplier'], sort=False
                ):
                    names = list(grp['RegionName'])
                    lines.extend(_wrap(f"    x={xm:+d}, y={ym:+d} ({len(names)}): ", names))
        else:
            lines.append("  Tracking regions : none defined in config")

        counting = ed.counting_regions if ed is not None else None
        if counting:
            lines.append("  Counting regions:")
            name_w = max(len(str(n)) for n in counting)
            for name, aliases in counting.items():
                lines.append(f"    {str(name):<{name_w}} : aliases {', '.join(aliases)}")
        else:
            lines.append("  Counting regions : none defined in config")

        if self.facet_cutoffs is not None:
            cutoffs_str = ', '.join(str(c) for c in self.facet_cutoffs) + ' min'
        else:
            cutoffs_str = 'not set'
        lines.append(f"  Facet cutoffs : {cutoffs_str}")
        lines.append("")
        return lines

    def _col_distribution(self, col: str) -> 'pd.DataFrame | None':
        """Return fraction-of-frames per unique value of *col*, one row per tracker.

        Returns None if the column is absent from any tracker's rawdata.
        """
        rows = []
        for key, tracker in self.arena.trackers.items():
            if col not in tracker.rawdata.columns:
                return None
            vc = tracker.rawdata[col].value_counts(normalize=True).sort_index()
            rows.append({'Tracker': key, **{str(k): v for k, v in vc.items()}})
        df = pd.DataFrame(rows).fillna(0).set_index('Tracker')
        return df

    def qc(self, cutoff: float = 0.9, save: bool = True) -> None:
        """
        Print the data quality report and optionally save it to qc_path.

        Counter-class experiments have no per-frame tracking quality to report,
        so this prints a note and returns instead of raising.

        Parameters
        ----------
        cutoff :
            Fraction of high-quality frames below which a tracker triggers a warning.
        save :
            If True, writes ``{experiment_name}_data_quality.csv`` to qc_path.
        """
        if not self.arena.supports_data_quality():
            print("Skipping data-quality table (not supported for this tracking type).")
            return

        dq = self.arena.get_data_quality()
        n_total = len(dq)
        n_good  = int((dq['HighQuality'] >= cutoff).sum())

        print(f"=== Data Quality: {self.arena.experiment_name} ===\n")
        print(f"Trackers passing ≥{cutoff:.0%} high-quality threshold: {n_good}/{n_total}\n")

        # Per-tracker table with all three quality types
        fmt = dq[['Tracker', 'HighQuality', 'NotFound', 'Indiscernible',
                   'StartMinutes', 'EndMinutes']].copy()
        for col in ('HighQuality', 'NotFound', 'Indiscernible'):
            fmt[col] = fmt[col].map('{:.1%}'.format)
        for col in ('StartMinutes', 'EndMinutes'):
            fmt[col] = fmt[col].map('{:.1f}'.format)
        print(fmt.to_string(index=False))

        # Summary stats per quality type
        print("\n--- Summary ---")
        for col in ('HighQuality', 'NotFound', 'Indiscernible'):
            print(f"  {col:15s}  mean={dq[col].mean():.1%}"
                  f"  min={dq[col].min():.1%}  max={dq[col].max():.1%}")

        # NObjects distribution
        nobj_df = self._col_distribution('NObjects')
        if nobj_df is not None:
            print("\n--- NObjects Distribution (% of frames) ---")
            print(nobj_df.apply(lambda c: c.map('{:.1%}'.format)).to_string())

        # BlobType distribution
        blob_df = self._col_distribution('BlobType')
        if blob_df is not None:
            print("\n--- BlobType Distribution (% of frames) ---")
            print(blob_df.apply(lambda c: c.map('{:.1%}'.format)).to_string())

        # Warnings
        below = dq[dq['HighQuality'] < cutoff]
        print()
        if len(below) > 0:
            print("****************************************************")
            print(f"Warning: {len(below)} tracker(s) below {cutoff:.0%} high-quality threshold:")
            for _, row in below.iterrows():
                print(f"  {row['Tracker']}: HighQuality={row['HighQuality']:.1%}"
                      f"  NotFound={row['NotFound']:.1%}"
                      f"  Indiscernible={row['Indiscernible']:.1%}")
            print("****************************************************")
        else:
            print("****************************************************")
            print(f"All trackers meet the {cutoff:.0%} high-quality threshold.")
            print("****************************************************")

        if save:
            path = os.path.join(self.qc_path, f"{self.arena.experiment_name}_data_quality.csv")
            dq.to_csv(path, index=False, na_rep='NA')
            print(f"\nSaved: {path}")

    def run_qc(self, cutoffs=None, qc_cutoff: float = 0.9) -> None:
        """Run the full QC suite. All outputs are written to ``self.qc_path``.

        Always:
          * Per-tracker data-quality table — same as :meth:`qc` (when supported).
          * Per-tracker XY plot grid (``<exp>_qc_trackers_xy.png``).

        Tracker-class types (TRACKER, TWOCHOICETRACKER, XCHOICETRACKER,
        PAIRWISEINTERACTIONTRACKER, CENTROPHOBISMTRACKER, DDROPTRACKER):
          * Faceted dot plot of TotalDistancePerMin
            (``<exp>_qc_TotalDistancePerMin_facet.png``).

        TWOCHOICETRACKER only:
          * Faceted dot plot of TransitionsPerMin
            (``<exp>_qc_TransitionsPerMin_facet.png``).

        Parameters
        ----------
        cutoffs :
            Facet cutoffs in minutes. Defaults to ``self.facet_cutoffs``.
        qc_cutoff :
            Fraction threshold passed through to :meth:`qc`.
        """
        cutoffs = self._resolve_cutoffs(cutoffs)
        name = self.arena.experiment_name
        print(f"=== Running QC for: {name} ===\n")

        # 1. Data-quality table (Tracker subclasses only — Counter has no get_data_quality).
        self.qc(cutoff=qc_cutoff, save=True)

        # 2. Per-tracker XY plot grid.
        self._save_qc_xy_grid()

        # 3. Conditional facet plots.
        tt = self.parameters.get_tracking_type()
        facet_tracker_types = (
            Parameters.TrackingType.TRACKER,
            Parameters.TrackingType.TWOCHOICETRACKER,
            Parameters.TrackingType.XCHOICETRACKER,
            Parameters.TrackingType.PAIRWISEINTERACTIONTRACKER,
            Parameters.TrackingType.CENTROPHOBISMTRACKER,
            Parameters.TrackingType.DDROPTRACKER,
        )
        if tt in facet_tracker_types:
            self._save_qc_facet_plot('TotalDistancePerMin', cutoffs)

        if tt == Parameters.TrackingType.TWOCHOICETRACKER:
            self._save_qc_facet_plot('TransitionsPerMin', cutoffs)

        print(f"\n=== QC outputs in: {self.qc_path} ===")

    def _save_qc_xy_grid(self, range_minutes=(0, 0), ncols: int = 4) -> None:
        """Save a multi-panel XY scatter, one subplot per tracker, to qc_path."""
        tracker_items = list(self.arena.trackers.items())
        n = len(tracker_items)
        if n == 0:
            print("No trackers to plot.")
            return
        ncols = min(ncols, n)
        nrows = -(-n // ncols)
        exp_name = self.arena.experiment_name

        def _treatment(tr):
            d = getattr(tr, 'tracking_region_design', None)
            return str(d['Treatment'].iat[0]) if d is not None and not d.empty else ''

        def _x_mult(tr):
            d = getattr(tr, 'tracking_region_design', None)
            return int(d['XLocationMultiplier'].iloc[0]) if d is not None and not d.empty else 1

        def _y_mult(tr):
            d = getattr(tr, 'tracking_region_design', None)
            return int(d['YLocationMultiplier'].iloc[0]) if d is not None and not d.empty else 1

        fig, axes = plt.subplots(
            nrows, ncols, figsize=(ncols * 3.5, nrows * 3.5), squeeze=False,
        )
        for idx, (key, tracker) in enumerate(tracker_items):
            ax = axes[idx // ncols][idx % ncols]
            try:
                data = tracker.get_data_subset(range_minutes)
                xlims, ylims = tracker.get_plot_limits()
                ax.scatter(
                    data['Xpos_mm'] * _x_mult(tracker),
                    data['Ypos_mm'] * _y_mult(tracker),
                    c=data['Minutes'], cmap='viridis',
                    vmin=data['Minutes'].min(), vmax=data['Minutes'].max(),
                    s=1, alpha=0.5,
                )
                ax.set_xlim(xlims)
                ax.set_ylim(ylims)
                ax.set_aspect('equal', adjustable='box')
                ax.set_title(f"{key}\n{_treatment(tracker)}", fontsize=7)
                ax.tick_params(labelsize=6)
                roi = getattr(tracker, 'tracking_region_roi', None)
                if roi is not None and not roi.empty and roi['Shape'].values[0] == 'Ellipse':
                    w = roi['Width'].values[0] * tracker.parameters.mm_per_pixel
                    h = roi['Height'].values[0] * tracker.parameters.mm_per_pixel
                    ax.add_patch(patches.Ellipse(
                        (0, 0), width=w, height=h,
                        edgecolor='gray', facecolor='none', linewidth=0.8,
                    ))
            except Exception as err:  # noqa: BLE001
                ax.set_title(f"{key}\n(error: {err})", fontsize=7)
        for idx in range(n, nrows * ncols):
            axes[idx // ncols][idx % ncols].set_visible(False)
        fig.supxlabel('X Position (mm)', fontsize=9)
        fig.supylabel('Y Position (mm)', fontsize=9)
        fig.suptitle(f"{exp_name} — QC: XY positions", fontsize=11)
        fig.tight_layout()
        path = os.path.join(self.qc_path, f"{exp_name}_qc_trackers_xy.png")
        fig.savefig(path, dpi=150, bbox_inches='tight')
        plt.close(fig)
        print(f"Saved: {path}")

    def _save_qc_facet_plot(self, metric: str, cutoffs) -> None:
        """Save a facet dot plot of *metric* (mm/min or transitions/min) to qc_path."""
        if cutoffs is None:
            print(f"Skipping QC facet plot for {metric} (facet_cutoffs not set).")
            return

        method_name = {
            'TotalDistancePerMin': 'plot_totaldistance_facet_generaltracker',
            'TransitionsPerMin':   'plot_transitions_facet_twochoicetracker',
        }.get(metric)
        if method_name is None:
            print(f"Unknown QC facet metric: {metric}")
            return
        method = getattr(self.arena, method_name, None)
        if method is None:
            print(f"Arena does not implement {method_name}; skipping.")
            return

        exp_name = self.arena.experiment_name
        filename = os.path.join(self.qc_path, f"{exp_name}_qc_{metric}_facet.png")

        original_show = plt.show

        def _save_and_close():
            fig = plt.gcf()
            fig.savefig(filename, dpi=150, bbox_inches='tight')
            plt.close(fig)
            print(f"Saved: {filename}")

        plt.show = _save_and_close
        try:
            method(cutoffs=cutoffs)
        except Arena.NoFacetData as err:
            ## An empty faceted summary is a finding for QC to report, not a
            ## reason to abort the load that QC is part of.
            print(f"Skipping QC facet plot for {metric}: {err}")
        finally:
            plt.show = original_show

    def save_summary(self, cutoffs=None, copy_to_clipboard: bool = False):
        """
        Compute and save the flat and faceted summaries to analysis_path.

        Parameters
        ----------
        cutoffs :
            Facet cutoffs to use. Defaults to ``self.facet_cutoffs``.
        copy_to_clipboard :
            If True, copies the faceted summary to the clipboard.

        Returns
        -------
        tuple
            ``(summary_df, summary_facet_df)``
        """
        cutoffs = self._resolve_cutoffs(cutoffs)

        summary = self.arena.summarize()
        summary = self._with_flag_column(summary)
        path = os.path.join(self.analysis_path, f"{self.arena.experiment_name}_Summary.csv")
        summary.to_csv(path, index=False, na_rep='NA')
        print(f"Saved: {path}")

        ## Exclusion audit (ADR-0003): written whenever the type has a
        ## criterion — even with zero exclusions — so absence of the file never
        ## needs interpreting.
        excluded = getattr(self, 'excluded_flies', None)
        if excluded is not None:
            path_excl = os.path.join(self.analysis_path,
                                     f"{self.arena.experiment_name}_Excluded.csv")
            excluded.to_csv(path_excl, index=False, na_rep='NA')
            print(f"Saved: {path_excl} ({len(excluded)} excluded)")

        summary_facet = None
        if cutoffs is not None:
            summary_facet = self.arena.summarize_facet(cutoffs=cutoffs)
            summary_facet = self._with_flag_column(summary_facet)
            path_facet = os.path.join(self.analysis_path,
                                      f"{self.arena.experiment_name}_Summary_Facet.csv")
            summary_facet.to_csv(path_facet, index=False, na_rep='NA')
            print(f"Saved: {path_facet}")
            if copy_to_clipboard:
                summary_facet.to_clipboard(index=False, na_rep='NA')
        else:
            print("Skipping faceted summary (facet_cutoffs not set in config or as argument).")

        return summary, summary_facet

    def _with_flag_column(self, summary):
        """Append the ``LowMovementFlag`` column (per-fly, by Name) to a summary
        bound for a CSV. Flagged flies stay in the data — the column is how a
        downstream reader sees the flag. No-op when the type has no flag."""
        flagged = getattr(self, 'flagged_flies', None)
        if flagged is None or summary is None or 'Name' not in summary.columns:
            return summary
        if not flagged.attrs.get('min_movement'):
            return summary  # flagging off: no column rather than all-False
        names = set(flagged['Name'].astype(str))
        out = summary.copy()
        out['LowMovementFlag'] = out['Name'].astype(str).isin(names)
        return out

    def stats(self, cutoffs=None, save: bool = True) -> str:
        """
        Run all relevant pairwise comparisons for this tracking type.

        Results are printed to the console and, when *save* is True, written to
        ``{experiment_name}_Stats.txt`` in analysis_path.

        When facet cutoffs are available the per-facet Tukey HSD comparisons are
        run; otherwise (or in addition) the flat full-recording comparisons are
        run, so :meth:`run_analysis` always produces a Stats file even on
        configs without ``facet_cutoffs``.

        Parameters
        ----------
        cutoffs :
            Facet cutoffs to use. Defaults to ``self.facet_cutoffs``.
        save :
            If True, writes the statistics text file to analysis_path.

        Returns
        -------
        str
            The full statistics output as a string.
        """
        cutoffs = self._resolve_cutoffs(cutoffs)

        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            metrics = self._stats_metrics()
            tracking_type = self.parameters.get_tracking_type()
            if not metrics:
                ## Never write a zero-byte Stats.txt: a reader cannot tell an
                ## empty file from a crashed run, and run_analysis reports
                ## success either way.
                if tracking_type in _TRACKING_TYPE_METRICS:
                    print(f"No statistical comparisons are defined for tracking type "
                          f"{tracking_type.name}: its summary carries no outcome metric.")
                else:
                    print(f"Warning: tracking type {tracking_type.name} has no statistics policy "
                          f"defined in this version. No comparisons were run.")
            else:
                self._print_stats_preamble(cutoffs)
                for metric in metrics:
                    self._print_metric_heading(metric)
                    try:
                        if cutoffs is None:
                            self.arena.run_pairwise_comparisons(metric=metric)
                        else:
                            self.arena.run_pairwise_comparisons_facet(
                                metric=metric,
                                cutoffs=cutoffs,
                                remove_partners='Interacting' in metric,
                            )
                    except Exception as e:  # noqa: BLE001
                        print(f"Warning: could not run comparison for '{metric}': {e}")
        finally:
            sys.stdout = old_stdout

        stats_text = buf.getvalue()
        print(stats_text)

        if save:
            path = os.path.join(self.analysis_path, f"{self.arena.experiment_name}_Stats.txt")
            with open(path, 'w', encoding='utf-8') as f:
                f.write(stats_text)
            print(f"Saved: {path}")

        return stats_text

    def _print_stats_preamble(self, cutoffs) -> None:
        """Orient the Stats.txt reader before any numbers: what is compared, in
        which time windows (with their phase names), and which test applies."""
        bar = '=' * 72
        print(bar)
        print(f"Statistical comparisons - {self.arena.experiment_name}")
        if not self.experiment_type.is_custom:
            print(f"Experiment type : {self.experiment_type.display_name}")
        print(f"Tracking type   : {self.parameters.get_tracking_type().name}")

        ## Exclusion accounting (ADR-0003, ADR-0010): the reader of every
        ## p-value below must know the population it was computed on, and by
        ## whose rule each fly left it. Rendered from the exclusion frame, not
        ## from min_transitions — a Custom Experiment can now have exclusions
        ## with no threshold in sight.
        excluded = getattr(self, 'excluded_flies', None)
        if excluded is not None:
            print(f"Excluded        : {self.exclusion_summary()}")
            for region in excluded.attrs.get('unmatched_regions', []):
                print(f"                  warning: removed region {region} "
                      f"matches no tracker in this experiment.")

        flagged = getattr(self, 'flagged_flies', None)
        if flagged is not None and flagged.attrs.get('min_movement'):
            attrs = flagged.attrs
            line = (f"Flagged         : {len(flagged)}/{attrs.get('n_total')} "
                    f"fly(ies) with movement below {attrs.get('min_movement'):g} "
                    f"mm/min during the {attrs.get('phase_label', 'first')} phase "
                    f"(potential issue; kept in all results).")
            if attrs.get('experiment_flagged'):
                line += " >50% flagged: the experiment itself is potentially an issue."
            print(line)

        if cutoffs is not None:
            from . import windowing
            windows = list(windowing.facet_windows(cutoffs))
            global_cfg = (self.config.get('global') or {}) \
                if isinstance(self.config, dict) else {}
            labels = self.experiment_type.phase_labels_for(windows, global_cfg)

            def fmt(window):
                start, end = window
                if end == float('inf'):
                    return f"{start:g}+ min"
                return f"{start:g}-{end:g} min"

            ## Only prepend names when the phases actually have them — unnamed
            ## phases would otherwise read "0–10 = 0-10 min".
            plain = [self.experiment_type._minute_label(w) for w in windows]
            if labels == plain:
                parts = [fmt(w) for w in windows]
            else:
                parts = [f"{label} = {fmt(w)}" for w, label in zip(windows, labels)]
            print(f"Phases          : {';  '.join(parts)}")
            print()
            print("Every metric below is compared between treatments once per phase.")
            print("The window of each test appears as 'Range Minutes' / 'Facet Range'")
            print("(start, end) in the output. Windows are half-open [start, end): a")
            print("row landing exactly on a boundary counts in the later phase.")
        else:
            print()
            print("No facet phases are configured: every comparison below runs over")
            print("the whole recording.")
        print()
        print("Tests: two treatment levels -> Welch's t-test (no equal-variance")
        print("assumption); three or more -> Tukey HSD at alpha = 0.05. Group N is")
        print("reported per test; animals with no numeric value for a metric in a")
        print("window are excluded and noted. P-values are per test - where one")
        print("metric is tested in several phases, a note gives the Bonferroni-")
        print("adjusted threshold.")
        print(bar)
        print()

    def _print_metric_heading(self, metric: str) -> None:
        """A ruled section heading naming the metric and what it measures."""
        if metric.startswith('PercentInteracting_'):
            distance = metric.rsplit('_', 1)[-1]
            description = (f"fraction of valid frames spent within {distance} mm "
                           f"of the partner, 0..1.")
        else:
            description = _METRIC_DESCRIPTIONS.get(metric, '')
        bar = '-' * 72
        print(bar)
        print(f"Metric: {metric}")
        if description:
            print(f"  {description}")
        print(bar)

    def plot_totaldistance_facet(self, cutoffs=None, save: bool = True) -> None:
        """
        Plot total distance per minute across facet phases, with an option to save.

        Parameters
        ----------
        cutoffs :
            Facet cutoffs in minutes. Defaults to ``self.facet_cutoffs``.
        save :
            If True (default), saves the figure to analysis_path.
        """
        cutoffs = self._resolve_cutoffs(cutoffs)
        if cutoffs is None:
            print("Skipping plot_totaldistance_facet (facet_cutoffs not set in config or as argument).")
            return

        original_show = plt.show

        if save:
            filename = os.path.join(
                self.analysis_path,
                f"{self.arena.experiment_name}_plot_totaldistance_facet.png",
            )

            def _save_and_show():
                fig = plt.gcf()
                fig.savefig(filename, dpi=150, bbox_inches='tight')
                plt.close(fig)
                print(f"Saved: {filename}")

            plt.show = _save_and_show

        try:
            self.arena.plot_totaldistance_facet(cutoffs=cutoffs)
        finally:
            plt.show = original_show

    def save_tracker_grid_plots(self, range_minutes=(0, 0), output_dir=None, ncols=4) -> None:
        """
        Save one multi-panel figure per plot type, with every tracker as its own subplot.

        Produces four files:
            ``{name}_trackers_x.png``
            ``{name}_trackers_y.png``
            ``{name}_trackers_xy.png``
            ``{name}_trackers_totaldistance.png``

        Parameters
        ----------
        range_minutes :
            ``(start, end)`` in minutes. ``(0, 0)`` uses the full recording.
        output_dir :
            Destination directory. Defaults to ``self.analysis_path``.
        ncols :
            Number of columns in the subplot grid. Default 4.
        """
        if output_dir is None:
            output_dir = self.analysis_path
        os.makedirs(output_dir, exist_ok=True)

        tracker_items = list(self.arena.trackers.items())
        n = len(tracker_items)
        if n == 0:
            return

        ncols = min(ncols, n)
        nrows = -(-n // ncols)          # ceiling division
        exp_name = self.arena.experiment_name

        def _treatment(tracker):
            if tracker.tracking_region_design is not None:
                return str(tracker.tracking_region_design['Treatment'].iat[0])
            return ''

        def _x_mult(tracker):
            if tracker.tracking_region_design is not None:
                return int(tracker.tracking_region_design['XLocationMultiplier'].iloc[0])
            return 1

        def _y_mult(tracker):
            if tracker.tracking_region_design is not None:
                return int(tracker.tracking_region_design['YLocationMultiplier'].iloc[0])
            return 1

        # ── X position ──────────────────────────────────────────────────
        fig, axes = plt.subplots(nrows, ncols, figsize=(ncols * 4, nrows * 3), squeeze=False)
        for idx, (key, tracker) in enumerate(tracker_items):
            ax = axes[idx // ncols][idx % ncols]
            try:
                data = tracker.get_data_subset(range_minutes)
                xlims, _ = tracker.get_plot_limits()
                ax.plot(data['Minutes'], data['Xpos_mm'] * _x_mult(tracker), linewidth=0.8)
                ax.set_ylim(xlims)
                ax.set_title(f"{key}\n{_treatment(tracker)}", fontsize=7)
                ax.tick_params(labelsize=6)
            except Exception:
                ax.set_title(f"{key}\n(error)", fontsize=7)
        for idx in range(n, nrows * ncols):
            axes[idx // ncols][idx % ncols].set_visible(False)
        fig.supxlabel('Minutes', fontsize=9)
        fig.supylabel('X Position (mm)', fontsize=9)
        fig.suptitle(f"{exp_name} — X Position", fontsize=11)
        fig.tight_layout()
        path = os.path.join(output_dir, f"{exp_name}_trackers_x.png")
        fig.savefig(path, dpi=150, bbox_inches='tight')
        plt.close(fig)
        print(f"Saved: {path}")

        # ── Y position ──────────────────────────────────────────────────
        fig, axes = plt.subplots(nrows, ncols, figsize=(ncols * 4, nrows * 3), squeeze=False)
        for idx, (key, tracker) in enumerate(tracker_items):
            ax = axes[idx // ncols][idx % ncols]
            try:
                data = tracker.get_data_subset(range_minutes)
                _, ylims = tracker.get_plot_limits()
                ax.plot(data['Minutes'], data['Ypos_mm'] * _y_mult(tracker), linewidth=0.8)
                ax.set_ylim(ylims)
                ax.set_title(f"{key}\n{_treatment(tracker)}", fontsize=7)
                ax.tick_params(labelsize=6)
            except Exception:
                ax.set_title(f"{key}\n(error)", fontsize=7)
        for idx in range(n, nrows * ncols):
            axes[idx // ncols][idx % ncols].set_visible(False)
        fig.supxlabel('Minutes', fontsize=9)
        fig.supylabel('Y Position (mm)', fontsize=9)
        fig.suptitle(f"{exp_name} — Y Position", fontsize=11)
        fig.tight_layout()
        path = os.path.join(output_dir, f"{exp_name}_trackers_y.png")
        fig.savefig(path, dpi=150, bbox_inches='tight')
        plt.close(fig)
        print(f"Saved: {path}")

        # ── XY position ─────────────────────────────────────────────────
        fig, axes = plt.subplots(nrows, ncols, figsize=(ncols * 3.5, nrows * 3.5), squeeze=False)
        for idx, (key, tracker) in enumerate(tracker_items):
            ax = axes[idx // ncols][idx % ncols]
            try:
                data = tracker.get_data_subset(range_minutes)
                xlims, ylims = tracker.get_plot_limits()
                ax.scatter(
                    data['Xpos_mm'] * _x_mult(tracker),
                    data['Ypos_mm'] * _y_mult(tracker),
                    c=data['Minutes'], cmap='viridis',
                    vmin=data['Minutes'].min(), vmax=data['Minutes'].max(),
                    s=1, alpha=0.5,
                )
                ax.set_xlim(xlims)
                ax.set_ylim(ylims)
                ax.set_aspect('equal', adjustable='box')
                ax.set_title(f"{key}\n{_treatment(tracker)}", fontsize=7)
                ax.tick_params(labelsize=6)
                roi = getattr(tracker, 'tracking_region_roi', None)
                if roi is not None and roi['Shape'].values[0] == 'Ellipse':
                    w = roi['Width'].values[0] * tracker.parameters.mm_per_pixel
                    h = roi['Height'].values[0] * tracker.parameters.mm_per_pixel
                    ax.add_patch(patches.Ellipse(
                        (0, 0), width=w, height=h,
                        edgecolor='gray', facecolor='none', linewidth=0.8,
                    ))
            except Exception:
                ax.set_title(f"{key}\n(error)", fontsize=7)
        for idx in range(n, nrows * ncols):
            axes[idx // ncols][idx % ncols].set_visible(False)
        fig.supxlabel('X Position (mm)', fontsize=9)
        fig.supylabel('Y Position (mm)', fontsize=9)
        fig.suptitle(f"{exp_name} — XY Position", fontsize=11)
        fig.tight_layout()
        path = os.path.join(output_dir, f"{exp_name}_trackers_xy.png")
        fig.savefig(path, dpi=150, bbox_inches='tight')
        plt.close(fig)
        print(f"Saved: {path}")

        # ── Total distance ───────────────────────────────────────────────
        # Use Dist_mm.cumsum() rather than the raw DTrack TotalDistance column.
        # The DTrack column is in pixels, starts at a non-zero value at frame 1
        # (pre-accumulated from before the first saved frame), and has occasional
        # non-monotonic drops. Dist_mm is in mm, starts at 0, and is always >= 0.
        fig, axes = plt.subplots(nrows, ncols, figsize=(ncols * 4, nrows * 3), squeeze=False)
        for idx, (key, tracker) in enumerate(tracker_items):
            ax = axes[idx // ncols][idx % ncols]
            try:
                data = tracker.get_data_subset(range_minutes)
                cum_dist = data['Dist_mm'].cumsum()
                ax.plot(data['Minutes'], cum_dist, linewidth=0.8)
                ax.set_title(f"{key}\n{_treatment(tracker)}", fontsize=7)
                ax.tick_params(labelsize=6)
            except Exception:
                ax.set_title(f"{key}\n(error)", fontsize=7)
        for idx in range(n, nrows * ncols):
            axes[idx // ncols][idx % ncols].set_visible(False)
        fig.supxlabel('Minutes', fontsize=9)
        fig.supylabel('Total Distance (mm)', fontsize=9)
        fig.suptitle(f"{exp_name} — Total Distance", fontsize=11)
        fig.tight_layout()
        path = os.path.join(output_dir, f"{exp_name}_trackers_totaldistance.png")
        fig.savefig(path, dpi=150, bbox_inches='tight')
        plt.close(fig)
        print(f"Saved: {path}")

    def save_plots(self, cutoffs=None, output_dir: str = None) -> None:
        """
        Save all relevant plots for this tracking type as PNG files.

        Each Arena plot method is called with the current ``facet_cutoffs`` and
        its ``plt.show()`` call is intercepted so that figures are written to disk
        rather than displayed interactively.

        Parameters
        ----------
        cutoffs :
            Overrides ``self.facet_cutoffs`` for this call only.
        output_dir :
            Destination directory. Defaults to ``self.analysis_path``.
        """
        resolved = self._resolve_cutoffs(cutoffs)
        old_cutoffs = self.facet_cutoffs
        if resolved is not None:
            self.facet_cutoffs = resolved

        if output_dir is None:
            output_dir = self.analysis_path
        os.makedirs(output_dir, exist_ok=True)

        plot_methods = self._plot_methods()
        ## _plot_methods only injects `cutoffs` when facet_cutoffs is set. With
        ## no cutoffs configured the facet methods used to be called bare and
        ## fall through to a hardcoded (10, 70) default, so one PDF could carry
        ## whole-recording p-values beside plots split at 10 and 70 minutes with
        ## nothing marking the discrepancy. Skip them instead, and say so.
        if self.facet_cutoffs is None:
            skipped = [name for name, kwargs in plot_methods if name.endswith('_facet')]
            if skipped:
                print(f"(No facet_cutoffs configured — skipping {len(skipped)} faceted plot(s).)")
            plot_methods = [(name, kwargs) for name, kwargs in plot_methods
                            if not name.endswith('_facet')]

        method_counts: dict = {}
        current_method = ['unknown']
        original_show = plt.show

        def _save_and_close():
            name = current_method[0]
            method_counts[name] = method_counts.get(name, 0) + 1
            suffix = f"_{method_counts[name]}" if method_counts[name] > 1 else ""
            filename = os.path.join(
                output_dir,
                f"{self.arena.experiment_name}_{name}{suffix}.png",
            )
            fig = plt.gcf()
            fig.savefig(filename, dpi=150, bbox_inches='tight')
            plt.close(fig)
            print(f"Saved: {filename}")

        plt.show = _save_and_close
        try:
            for method_name, kwargs in plot_methods:
                current_method[0] = method_name
                try:
                    getattr(self.arena, method_name)(**kwargs)
                except Exception as e:
                    print(f"Warning: could not generate '{method_name}': {e}")
        finally:
            plt.show = original_show
            self.facet_cutoffs = old_cutoffs

        if self.parameters.get_tracking_type() == Parameters.TrackingType.TRACKER:
            self.save_tracker_grid_plots(output_dir=output_dir)

    # ------------------------------------------------------------------
    # PDF report generation
    # ------------------------------------------------------------------

    #: Name of the marker file :meth:`run_analysis` drops in ``analysis/``.
    _RUN_MANIFEST = ".pytracking_run.json"

    def _write_run_manifest(self) -> None:
        """Stamp the start of an analysis run so stale artifacts can be identified."""
        import json
        from datetime import datetime as _dt

        payload = {
            "started": time.time(),
            "started_iso": _dt.now().isoformat(timespec="seconds"),
            "facet_cutoffs": list(self.facet_cutoffs) if self.facet_cutoffs is not None else None,
            "tracking_type": self.parameters.get_tracking_type().name,
        }
        try:
            io_utils.atomic_write_text(
                os.path.join(self.analysis_path, self._RUN_MANIFEST),
                lambda handle: json.dump(payload, handle, indent=2),
            )
        except OSError as err:
            logger.warning("Could not write the run manifest: %s", err)

    def _run_started_at(self):
        """Timestamp of the last :meth:`run_analysis`, or None if never recorded."""
        import json

        path = os.path.join(self.analysis_path, self._RUN_MANIFEST)
        try:
            with open(path, encoding="utf-8") as handle:
                return float(json.load(handle)["started"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def create_report(self, cutoffs=None, qc_cutoff: float = 0.9,
                      backend: str = "reportlab", notes: str | None = None) -> str:
        """Assemble a professional report for this experiment.

        Builds a backend-agnostic document model (see
        :mod:`pytrackinganalysis.report`) from the current analysis outputs and
        renders it with the named *backend* — reportlab today; the same model is
        designed to drive future Markdown/LaTeX backends without any change to
        the analysis core. Run after :meth:`run_analysis` and :meth:`run_qc`.

        The report draws its plots natively as consolidated multi-panel
        figures, renders the pairwise comparisons as a semantic table, and
        builds the experiment summary as structured blocks (the ``*_Stats.txt``
        and ``*_experiment_summary.txt`` files stay on disk but are not
        embedded as raw text). It is written to the **project root** beside
        ``tracking_config.yaml`` as ``<project>_report.pdf`` — the project's
        front page — while the machine-readable artifacts stay in
        ``analysis/``. ``cutoffs`` is accepted for back-compat and unused.
        *notes*, when given, is persisted via :meth:`write_run_notes` (blank
        clears) and rendered near the top of the report; ``None`` keeps
        whatever notes are already saved. Returns the path to the written
        report.
        """
        from .report import render as _render

        del cutoffs  # report sources the current analysis outputs, not a re-run.
        if notes is not None:
            self.write_run_notes(notes)
        report = self.build_report_model(qc_cutoff=qc_cutoff)
        ext = {"reportlab": "pdf"}.get(backend, "pdf")
        ## The report is the project's front page, so it lives in the project
        ## root beside tracking_config.yaml — named for the project, not the
        ## recording — while the machine-readable artifacts stay in analysis/.
        project_name = os.path.basename(os.path.normpath(self.project_directory))
        out_path = os.path.join(self.project_directory,
                                f"{project_name}_report.{ext}")
        _render(report, out_path, backend=backend)
        print(f"Saved: {out_path}")
        return out_path

    # ------------------------------------------------------------------
    # Run notes — user-provided context captured when a run is launched,
    # persisted beside the other analysis artifacts and rendered near the
    # top of the report.
    # ------------------------------------------------------------------

    def _run_notes_path(self) -> str:
        return os.path.join(self.analysis_path,
                            f"{self.arena.experiment_name}_Notes.txt")

    def read_run_notes(self) -> str:
        """The saved run notes, or an empty string when there are none."""
        try:
            with open(self._run_notes_path(), encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            return ""

    def write_run_notes(self, text) -> None:
        """Persist *text* as the run notes; blank (or None) removes them, so
        stale notes never outlive the run they described."""
        path = self._run_notes_path()
        text = (text or "").strip()
        if not text:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")

    # ------------------------------------------------------------------
    # AI Summary — an optional AI-written narrative of this analysis (see
    # CONTEXT.md and ADR-0004). The saved file is the opt-in: the report
    # embeds it iff it exists, and run_analysis deletes it, so stale prose
    # can never sit beside fresh figures.
    # ------------------------------------------------------------------

    #: First-line marker of a saved AI Summary; the rest of that line is the
    #: provenance (provider, model, timestamp) rendered above the summary.
    _AI_SUMMARY_MARKER = "[AI Summary] "

    def _ai_summary_path(self) -> str:
        return os.path.join(self.analysis_path,
                            f"{self.arena.experiment_name}_AI_Summary.txt")

    def read_ai_summary(self):
        """The saved AI Summary as ``(provenance, body)``, or ``None``.

        ``provenance`` is the "provider model, timestamp" string written by
        :meth:`write_ai_summary`, or ``None`` for a file without the marker
        (e.g. hand-edited) — the body is still returned so the report never
        silently drops what the file says.
        """
        try:
            with open(self._ai_summary_path(), encoding="utf-8") as handle:
                text = handle.read().strip()
        except OSError:
            return None
        if not text:
            return None
        first, _, rest = text.partition("\n")
        if first.startswith(self._AI_SUMMARY_MARKER):
            return first[len(self._AI_SUMMARY_MARKER):].strip(), rest.strip()
        return None, text

    def write_ai_summary(self, text: str, provider: str, model: str) -> str:
        """Persist *text* with a provenance first line; returns the path.

        Also writes the fixed-name ``ai_narrative.md`` companion beside it,
        so the prose is greppable across a tree without knowing this
        replicate's name."""
        from datetime import datetime as _dt

        from .ai.narrative import write_narrative_md

        stamp = _dt.now().strftime("%Y-%m-%d %H:%M")
        path = self._ai_summary_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"{self._AI_SUMMARY_MARKER}{provider} {model}, "
                         f"{stamp}\n\n{text.strip()}\n")
        write_narrative_md(
            self.analysis_path, title=self.arena.experiment_name,
            level="Experiment (one replicate)", provider=provider,
            model=model, stamp=stamp, text=text,
            context=[("Experiment type",
                      getattr(self.experiment_type, "display_name", "")),
                     ("Directory", str(self.project_directory))],
        )
        return path

    def delete_ai_summary(self) -> None:
        """Remove the saved AI Summary, if any (ADR-0004: it is a derivative
        of a single analysis run and must not outlive it)."""
        from .ai.narrative import delete_narrative_md

        try:
            os.remove(self._ai_summary_path())
        except FileNotFoundError:
            pass
        delete_narrative_md(self.analysis_path)

    def generate_ai_summary(self, provider: str, model: str | None = None) -> str:
        """Ask *provider* for an AI Summary of the current analysis outputs.

        Builds the payload from the report's own ingredients (never the
        rendered PDF), calls the provider, and persists the result — the next
        :meth:`create_report` embeds it. Raises
        :class:`~pytrackinganalysis.ai.AISummaryError` on any provider
        problem; nothing else is touched in that case. Returns the summary
        text.
        """
        from .ai import build_payload, get_summarizer

        summarizer = get_summarizer(provider, model=model)
        print(f"Requesting AI summary from {summarizer.display_name} "
              f"({summarizer.model})…")
        payload = build_payload(self)
        text = summarizer.summarize(
            payload, self.experiment_type.ai_summary_prompt())
        path = self.write_ai_summary(
            text, summarizer.display_name, summarizer.model)
        print(f"Saved: {path}")
        return text

    # ------------------------------------------------------------------
    # Report model assembly (analysis-side; imports the document model only,
    # never a rendering backend, so the core stays engine-independent).
    # ------------------------------------------------------------------

    def build_report_model(self, qc_cutoff: float = 0.9,
                           include_ai_summary: bool = True):
        """Build the backend-agnostic :class:`~...report.model.Report`.

        The single bridge between analysis data and the report: it emits the
        cover, section dividers, statistics text, summary tables and
        report-native figures as model blocks. *include_ai_summary* exists for
        the AI payload builder, which must serialize the report *without* any
        saved AI Summary so a new summary never quotes its predecessor
        (ADR-0004).
        """
        from datetime import datetime as _dt
        from pathlib import Path as _Path

        from . import report_figures
        from .report import model as _m

        exp_name = self.arena.experiment_name
        ## The report is titled after the project (like the PDF's filename),
        ## not the DTrack recording: the project directory is the thing the
        ## scientist names and recognises. The recording name stays visible
        ## as a cover metadata row.
        project_name = os.path.basename(
            os.path.normpath(self.project_directory)) or exp_name
        report = _m.Report(project_name)

        # ---- Cover ----
        global_cfg = (self.config.get("global", {})
                      if isinstance(self.config, dict) else {})
        metadata = []
        if not self.experiment_type.is_custom:
            metadata.append(("Experiment type", self.experiment_type.display_name))
        metadata += [
            ("Recording", exp_name),
            ("Tracking type", self.parameters.get_tracking_type().name),
            ("Tracking rig", str(global_cfg.get("tracking_rig", "—"))),
        ]
        metadata += self._cover_phase_and_region_metadata(global_cfg)
        metadata += [
            ("Project", os.path.abspath(self.project_directory)),
            ("Generated", _dt.now().strftime("%Y-%m-%d %H:%M")),
        ]
        # Title/intro come from the Experiment Type (ADR-0001): a Valence report
        # says "Valence Experiment Report" and carries the assay's intro.
        status_lines = [line for line in (self._qc_status_line(qc_cutoff),
                                          self._movement_status_line())
                        if line is not None]
        report.add(_m.Cover(project_name, self.experiment_type.report_title(),
                            metadata=metadata,
                            status=status_lines or None))

        # ---- Run notes ----
        # User-provided context typed in when the run was launched (or written
        # to <name>_Notes.txt by hand). Rendered up front, not as an appendix.
        notes = self.read_run_notes()
        if notes:
            report.add(_m.Heading("Notes", level=2))
            for line in notes.splitlines():
                if line.strip():
                    report.add(_m.Paragraph(line.strip()))

        # ---- AI Summary ----
        # Embedded iff the saved file exists — the file IS the opt-in
        # (ADR-0004). Rendered up front, executive-summary style, with an
        # explicit provenance line so nobody mistakes it for human prose.
        saved_summary = self.read_ai_summary() if include_ai_summary else None
        if saved_summary:
            provenance, body = saved_summary
            report.add(_m.Heading("AI Summary", level=2))
            label = (f"AI-generated summary — {provenance}." if provenance
                     else "AI-generated summary.")
            report.add(_m.Paragraph(f"{label} Review before relying on it."))
            for para in body.split("\n\n"):
                para = " ".join(para.split())
                if para:
                    report.add(_m.Paragraph(para))

        # ---- Analysis section ----
        # Whole-recording figures, then the per-phase (faceted) figures when
        # facet_cutoffs is configured, then the statistical-test text. The
        # numeric summary tables are NOT embedded — that data is written to the
        # CSVs in analysis/ by run_analysis; the report is a visual + stats
        # narrative, not a data dump.
        report.add(_m.SectionDivider("Analysis"))
        intro = self.experiment_type.report_intro()
        if intro:
            report.add(_m.Paragraph(intro))
        ## The analysis population comes first: who was excluded and why,
        ## before any result computed from what remained (ADR-0003, ADR-0010).
        ## Type-independent, because an experimenter can remove a region in a
        ## Custom Experiment too.
        for block in report_figures.build_exclusion_blocks(self):
            report.add(block)
        # Type-specific sections lead (ADR-0002) — e.g. Valence's headline
        # Experiment-phase PI, PI-over-time, and persistence blocks. Empty for
        # a Custom Experiment.
        for block in self.experiment_type.report_sections(self):
            report.add(block)
        ## The whole-recording ("summed over all phases") figures are redundant
        ## the moment per-phase figures exist — every metric they show appears
        ## faceted right below — so they are included only when there is no
        ## faceting to replace them (e.g. a config without facet_cutoffs).
        faceted_figs = report_figures.build_faceted_figures(self)
        if not faceted_figs:
            for fig in report_figures.build_analysis_figures(self):
                report.add(fig)
        for fig in faceted_figs:
            report.add(fig)
        ## The pairwise comparisons as a readable table; the raw Stats.txt is
        ## excluded from the text embeds below (it stays on disk).
        for block in report_figures.build_stats_table(self):
            report.add(block)
        self._add_text_blocks(report, _Path(self.analysis_path), _m)

        # ---- Experiment summary section ----
        for block in self._experiment_summary_blocks(_m):
            report.add(block)

        # ---- Quality-control section ----
        report.add(_m.SectionDivider("Quality Control"))
        for fig in report_figures.build_qc_figures(self):
            report.add(fig)
        self._add_text_blocks(report, _Path(self.qc_path), _m)

        return report

    def _cover_phase_and_region_metadata(self, global_cfg: dict) -> list:
        """Cover rows for the phase structure and the plate: what was measured,
        when, and on how many animals — visible before any figure."""
        from collections import Counter as _Counter

        from . import report_figures, windowing as _w

        rows: list = []
        if self.facet_cutoffs:
            windows = list(_w.facet_windows(self.facet_cutoffs))
            labels = self.experiment_type.phase_labels_for(windows, global_cfg)
            parts = []
            for window, label in zip(windows, labels):
                rng = report_figures._phase_label(window)
                parts.append(rng if label == rng else f"{label} {rng}")
            rows.append(("Phases (min)", " · ".join(parts)))

        regions = (self.config.get("tracking_regions") or {}) \
            if isinstance(self.config, dict) else {}
        if regions:
            counts: _Counter = _Counter()
            for region in regions.values():
                factors = (str(region.get("experimental_factors", "")).strip()
                           if isinstance(region, dict) else "")
                if factors:
                    counts[factors] += 1
            value = str(len(regions))
            if counts:
                value += "  (" + ", ".join(
                    f"{name} ×{n}" for name, n in sorted(counts.items())) + ")"
            rows.append(("Tracking regions", value))
        return rows

    def _qc_status_line(self, qc_cutoff: float):
        """Cover-page QC one-liner as a semantic StatusLine, or None."""
        from .report import model as _m

        try:
            if not self.arena.supports_data_quality():
                return None
            dq = self.arena.get_data_quality()
            hq = pd.to_numeric(dq["HighQuality"], errors="coerce")
            n = len(hq)
            n_good = int((hq >= qc_cutoff).sum())
            level = _m.Level.OK if n_good == n else _m.Level.ERROR
            return _m.StatusLine(
                f"Data quality: {n_good}/{n} trackers ≥ {qc_cutoff:.0%} high-quality",
                level)
        except Exception:  # noqa: BLE001
            return None

    def _movement_status_line(self):
        """Cover one-liner for the Low-Movement Flag, or None when the type has
        no such flag (or it is off). Severity: all clear -> OK; some flies
        flagged -> WARN; more than half flagged -> ERROR (the experiment itself
        is potentially an issue)."""
        from .report import model as _m

        flagged = getattr(self, 'flagged_flies', None)
        if flagged is None:
            return None
        attrs = flagged.attrs
        threshold = attrs.get('min_movement')
        if not threshold:
            return None
        n, total = len(flagged), attrs.get('n_total', 0)
        phase = attrs.get('phase_label', 'first')
        where = f"< {threshold:g} mm/min during {phase}"
        if attrs.get('experiment_flagged'):
            return _m.StatusLine(
                f"Potential issue: {n}/{total} flies moved {where} (>50% flagged)",
                _m.Level.ERROR)
        if n:
            return _m.StatusLine(
                f"Low movement: {n}/{total} flies moved {where} (kept in results)",
                _m.Level.WARN)
        return _m.StatusLine(
            f"Movement: all {total} flies ≥ {threshold:g} mm/min during {phase}",
            _m.Level.OK)

    def _experiment_summary_blocks(self, _m) -> list:
        """The experiment-summary section as structured blocks — headings and
        label/value tables — replacing the old embed of the monospaced
        ``*_experiment_summary.txt`` (whose box-drawing rules render as black
        boxes in the PDF's fonts). The text file itself is still written for
        the terminal and disk."""
        blocks: list = [_m.SectionDivider("Experiment Summary")]
        global_cfg = (self.config.get('global') or {}) \
            if isinstance(self.config, dict) else {}
        p = self.parameters

        def _kv(title, pairs):
            rows = [[str(k), str(v)] for k, v in pairs
                    if v is not None and str(v) != ""]
            if rows:
                blocks.append(_m.Table(columns=["Setting", "Value"], rows=rows,
                                       title=title))

        # ---- Configuration -------------------------------------------------
        cutoffs = self.facet_cutoffs
        if cutoffs is not None:
            from . import windowing
            windows = list(windowing.facet_windows(cutoffs))
            labels = self.experiment_type.phase_labels_for(windows, global_cfg)
            phases = ";  ".join(
                f"{label} ({self.experiment_type._minute_label(w)} min)"
                for w, label in zip(windows, labels))
        else:
            phases = "Not set (whole recording)"
        config_pairs = []
        if not self.experiment_type.is_custom:
            config_pairs.append(("Experiment type",
                                 self.experiment_type.display_name))
        config_pairs += [
            ("Tracking type", self.parameters.get_tracking_type().name),
            ("Tracking rig", global_cfg.get('tracking_rig', 'Unknown')),
            ("Phases", phases),
        ]
        min_transitions = self.experiment_type.resolve_min_transitions(global_cfg)
        if min_transitions is not None:
            config_pairs.append(
                ("Exclusion (min_transitions)",
                 f"{min_transitions} transitions in the primary phase"
                 if min_transitions else "off (0)"))
        min_movement = self.experiment_type.resolve_min_movement(global_cfg)
        if min_movement is not None:
            config_pairs.append(
                ("Movement flag (min_movement)",
                 f"{min_movement:g} mm/min in the first phase"
                 if min_movement else "off (0)"))
        _kv("Configuration", config_pairs)

        # ---- Parameters ----------------------------------------------------
        _kv("Parameters", [
            ("FPS", p.fps),
            ("mm per pixel", p.mm_per_pixel),
            ("Speed window", f"{p.speed_window_seconds} s"),
            ("Micro-move speed", f"{p.micro_move_speed_mm_sec[0]} – "
                                 f"{p.micro_move_speed_mm_sec[1]} mm/s"),
            ("Walking speed", f"≥ {p.walking_speed_mm_sec} mm/s"),
            ("Sleep threshold", f"{p.sleep_threshold_min} min"),
            ("Interaction distances",
             ", ".join(str(d) for d in (p.interaction_distance_mm or [])) + " mm"
             if p.interaction_distance_mm else None),
        ])

        # ---- Experimental design -------------------------------------------
        design_pairs = []
        for name, lv in (global_cfg.get("experimental_design_factors") or {}).items():
            design_pairs.append((f"Factor: {name}",
                                 ", ".join(str(l) for l in lv)))
        counting = (self.config.get("counting_regions") or {}) \
            if isinstance(self.config, dict) else {}
        for cname, spec in counting.items():
            alias = spec.get("alias", "") if isinstance(spec, dict) else ""
            design_pairs.append((f"Counting region: {cname}", alias))
        counts: dict[str, int] = {}
        regions = (self.config.get("tracking_regions") or {}) \
            if isinstance(self.config, dict) else {}
        for spec in regions.values():
            treat = str((spec or {}).get("experimental_factors", "")).strip()
            if treat:
                counts[treat] = counts.get(treat, 0) + 1
        if counts:
            design_pairs.append(
                ("Treatment assignment",
                 ",  ".join(f"{k} ×{n}" for k, n in sorted(counts.items()))))
        _kv("Experimental design", design_pairs)

        # ---- Data overview --------------------------------------------------
        try:
            supports_dq = self.arena.supports_data_quality()
            dq = self.arena.get_data_quality() if supports_dq else None
        except Exception:  # noqa: BLE001
            supports_dq, dq = False, None
        trackers = getattr(self.arena, 'trackers', None) or {}
        overview = [("Total trackers", len(trackers))]
        if dq is not None:
            hq = pd.to_numeric(dq['HighQuality'], errors='coerce')
            overview += [
                ("Passing ≥90% quality", f"{int((hq >= 0.9).sum())} / {len(dq)}"),
                ("Recording range",
                 f"{dq['StartMinutes'].min():.1f} – {dq['EndMinutes'].max():.1f} min"),
            ]
        if trackers:
            overview.append(("Total data points",
                             f"{sum(len(t.rawdata) for t in trackers.values()):,}"))
        excluded = getattr(self, 'excluded_flies', None)
        if excluded is not None:
            ## One number, with the breakdown beside it: the analysis
            ## population is flies - excluded, and that arithmetic must have
            ## exactly one input (ADR-0010).
            attrs = excluded.attrs
            parts = []
            if attrs.get('n_removed'):
                parts.append(f"{attrs['n_removed']} removed")
            if attrs.get('n_low_transitions'):
                parts.append(f"{attrs['n_low_transitions']} low transitions")
            detail = f" ({', '.join(parts)})" if parts else ""
            overview.append(("Excluded flies", f"{len(excluded)}{detail}"))
        flagged = getattr(self, 'flagged_flies', None)
        if flagged is not None and flagged.attrs.get('min_movement'):
            overview.append(("Low-movement flagged", len(flagged)))
        _kv("Data overview", overview)

        # ---- Per-tracker table ----------------------------------------------
        def _treatment_of(key):
            tracker = trackers.get(key)
            if tracker is not None and tracker.tracking_region_design is not None:
                return str(tracker.tracking_region_design['Treatment'].iat[0])
            return ''

        if dq is not None:
            rows, levels = [], []
            for _, row in dq.iterrows():
                hq_val = pd.to_numeric(pd.Series([row['HighQuality']]),
                                       errors='coerce').iloc[0]
                ok = hq_val == hq_val and hq_val >= 0.9
                rows.append([
                    str(row['Tracker']), _treatment_of(row['Tracker']),
                    "—" if hq_val != hq_val else f"{hq_val:.1%}",
                    f"{row['NotFound']:.1%}", f"{row['Indiscernible']:.1%}",
                    f"{row['StartMinutes']:.1f}", f"{row['EndMinutes']:.1f}",
                ])
                levels.append(None if ok else _m.Level.ERROR)
            blocks.append(_m.Table(
                columns=["Tracker", "Treatment", "High quality", "Not found",
                         "Indiscernible", "Start (min)", "End (min)"],
                rows=rows, row_levels=levels, title="Per-tracker summary",
                caption="Red rows fall below 90% high-quality frames."))
        else:
            rows = []
            for key, tracker in trackers.items():
                minutes = tracker.rawdata['Minutes'] \
                    if 'Minutes' in tracker.rawdata.columns else None
                start = minutes.min() if minutes is not None and len(minutes) else float('nan')
                end = minutes.max() if minutes is not None and len(minutes) else float('nan')
                rows.append([str(key), _treatment_of(key),
                             f"{len(tracker.rawdata):,}",
                             f"{start:.1f}", f"{end:.1f}"])
            if rows:
                blocks.append(_m.Table(
                    columns=["Region", "Treatment", "Rows", "Start (min)",
                             "End (min)"],
                    rows=rows, title="Per-tracker summary"))
        return blocks

    def _add_text_blocks(self, report, directory, _m) -> None:
        """Add each non-empty ``*.txt`` in *directory* as a Preformatted block.

        Excluded: the run-notes file (rendered as prose after the cover), the
        AI Summary (rendered as its own section up front), the stats text
        (rendered as the Statistical-comparisons table), and the experiment
        summary (rendered as the structured Experiment Summary section) —
        embedding those again would duplicate them in a worse form."""
        if not directory.exists():
            return
        notes_name = os.path.basename(self._run_notes_path())
        for path in sorted(directory.glob("*.txt")):
            if path.name.startswith(".") or path.name == notes_name:
                continue
            if path.name.endswith(("_Stats.txt", "_experiment_summary.txt",
                                   "_AI_Summary.txt")):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as err:
                text = f"(could not read {path.name}: {err})"
            if text.strip():
                report.add(_m.Preformatted(text, title=path.stem))



    def run_analysis(self, cutoffs=None, qc_cutoff: float = 0.9) -> None:
        """
        Run the complete analysis pipeline in one call.

        Order: experiment summary → QC report → summary CSVs → statistics → plots.

        Parameters
        ----------
        cutoffs :
            Facet cutoffs to use throughout. Defaults to ``self.facet_cutoffs``.
        qc_cutoff :
            Fraction threshold passed to :meth:`qc`.
        """
        cutoffs = self._resolve_cutoffs(cutoffs)

        name = self.arena.experiment_name
        print(f"=== Running analysis for: {name} ===\n")

        ## Record when this run began so create_report can tell the artifacts it
        ## produced from leftovers of an earlier configuration sitting in the
        ## same directory. Without it a stale *_Summary_Facet.csv is embedded in
        ## the PDF as a current result with no provenance marker at all.
        self._write_run_manifest()

        ## The AI Summary describes a single analysis run (ADR-0004). Unlike
        ## the plots and CSVs below, this run cannot regenerate it for free —
        ## so it is deleted, not refreshed; the user re-requests it from the
        ## AI card when they want it back.
        self.delete_ai_summary()

        print("--- Experiment Summary ---")
        self.experiment_summary(save=True)

        print("\n--- Quality Control ---")
        ## The full QC suite, not just the table: it is guarded for counter-class
        ## experiments and writes the QC artifacts that create_report picks up.
        self.run_qc(cutoffs=cutoffs, qc_cutoff=qc_cutoff)

        print("\n--- Saving Summaries ---")
        self.save_summary(cutoffs=cutoffs)

        print("\n--- Running Statistics ---")
        self.stats(cutoffs=cutoffs, save=True)

        print("\n--- Saving Plots ---")
        self.save_plots(cutoffs=cutoffs)

        self._verify_output_manifest()

        print(f"\n=== Done. Outputs written to: {self.analysis_path} ===")

    def _verify_output_manifest(self) -> None:
        """Warn if the Experiment Type declared data outputs it did not produce.

        Each Experiment Type declares the files it is expected to write
        (``output_manifest``); this checks they exist after a run. Empty for a
        Custom Experiment, which declares nothing.
        """
        manifest = self.experiment_type.output_manifest()
        if not manifest:
            return
        name = self.arena.experiment_name
        missing = [suffix for suffix in manifest
                   if not os.path.exists(os.path.join(self.analysis_path, f"{name}{suffix}"))]
        if missing:
            print(f"Warning: {self.experiment_type.display_name} expected these "
                  f"outputs which were not produced: {', '.join(missing)}")

    # ------------------------------------------------------------------
    # Representation
    # ------------------------------------------------------------------

    def __str__(self) -> str:
        global_cfg = self.config.get('global', {})
        lines = [
            f"Experiment: {self.arena.experiment_name}",
            f"  Project directory : {self.project_directory}",
            f"  Data path         : {self.data_path}",
            f"  Analysis path     : {self.analysis_path}",
            f"  QC path           : {self.qc_path}",
            f"  Experiment type   : {self.experiment_type.display_name}",
            f"  Tracking type     : {self.parameters.get_tracking_type().name}",
            f"  Tracking rig      : {global_cfg.get('tracking_rig', 'Unknown')}",
            f"  Facet cutoffs     : {self.facet_cutoffs if self.facet_cutoffs is not None else 'Not set'}",
            f"  Parameters        :",
        ]
        for line in str(self.parameters).splitlines():
            lines.append(f"    {line}")
        if self.experimental_design is not None:
            lines.append(f"  Experimental design:")
            for line in str(self.experimental_design).splitlines():
                lines.append(f"    {line}")
        return '\n'.join(lines)

    def __repr__(self) -> str:
        return (
            f"Experiment(project_directory={self.project_directory!r}, "
            f"experiment={self.arena.experiment_name!r})"
        )


def _is_experiment_dir(path: str) -> bool:
    """Return True if *path* looks like a valid experiment directory.

    Requires:
      - tracking_config.yaml present (case-insensitive)
      - a subdirectory named 'data' (case-insensitive) that contains at least one .xlsx file
    """
    entries = {e.lower(): e for e in os.listdir(path)}

    if 'tracking_config.yaml' not in entries:
        return False

    data_name = entries.get('data')
    if data_name is None:
        return False

    data_dir = os.path.join(path, data_name)
    if not os.path.isdir(data_dir):
        return False

    return bool(glob.glob(os.path.join(glob.escape(data_dir), '*.xlsx')))


def batch_analyze(
    parent_directory: str,
    cutoffs=None,
    qc_cutoff: float = 0.9,
    force_preprocessing: bool = False,
) -> dict:
    """Run analysis and create a PDF report for every experiment in *parent_directory*.

    Scans all immediate subdirectories of *parent_directory* for valid experiment
    folders (those containing a ``tracking_config.yaml`` and a ``data/`` subdirectory
    with at least one ``.xlsx`` file). Runs :meth:`Experiment.run_analysis` and
    :meth:`Experiment.create_report` on each one.

    Parameters
    ----------
    parent_directory :
        Root directory to search. Only immediate subdirectories are checked
        (non-recursive).
    cutoffs :
        Facet cutoffs passed through to each experiment. ``None`` uses the
        value in each experiment's own ``tracking_config.yaml``.
    qc_cutoff :
        High-quality frame threshold used in the QC report (default 0.9).
    force_preprocessing :
        If True, forces re-computation of nearest-neighbour pre-processing.

    Returns
    -------
    dict
        ``{experiment_path: 'ok' | error_message}`` for every candidate directory.
    """
    parent_directory = os.path.abspath(parent_directory)
    results = {}

    candidates = sorted(
        entry for entry in (
            os.path.join(parent_directory, name)
            for name in os.listdir(parent_directory)
        )
        if os.path.isdir(entry)
    )

    if not candidates:
        print(f"No subdirectories found in {parent_directory}")
        return results

    valid = [p for p in candidates if _is_experiment_dir(p)]

    if not valid:
        print(f"No valid experiment directories found in {parent_directory}")
        return results

    print(f"Found {len(valid)} experiment(s) in {parent_directory}\n")

    for i, exp_dir in enumerate(valid, 1):
        print(f"[{i}/{len(valid)}] {exp_dir}")
        print("=" * 60)
        try:
            exp = Experiment(exp_dir, force_preprocessing=force_preprocessing)
            exp.run_analysis(cutoffs=cutoffs, qc_cutoff=qc_cutoff)
            exp.create_report(cutoffs=cutoffs, qc_cutoff=qc_cutoff)
            results[exp_dir] = 'ok'
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            print(f"  ERROR: {msg}")
            results[exp_dir] = msg
        print()

    ok = sum(1 for v in results.values() if v == 'ok')
    print(f"Batch complete: {ok}/{len(valid)} succeeded.")
    if ok < len(valid):
        print("Failed directories:")
        for path, msg in results.items():
            if msg != 'ok':
                print(f"  {path}: {msg}")

    return results


if __name__ == "__main__":
    project_dir = sys.argv[1] if len(sys.argv) > 1 else '.'
    exp = Experiment(project_dir)
    print(exp)
