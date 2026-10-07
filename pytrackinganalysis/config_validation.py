"""Semantic validation for ``tracking_config.yaml``.

Parsing cleanly is not the same as being usable: a config can be perfect YAML
and still name a rig that does not exist, omit the calibration a movie needs, or
declare three counting-region groups for a two-choice experiment. Those all used
to surface much later — as a crash mid-analysis, or worse, as a silent fallback
to a default calibration.

:func:`validate_config` is the shared checker. The Hub's "Validate YAML" button,
the Config Editor and anything else that wants to check a project before running
it should call this rather than re-implementing the rules.
"""

from __future__ import annotations

import os

import yaml

from . import Parameters

# Rig spellings accepted in the config, mapped to their canonical name.
RIG_ALIASES = {
    'small_arena': 'small_arena',
    'smallarena': 'small_arena',
    'arena_max': 'arena_max',
    'arenamax': 'arena_max',
    'colosseum': 'colosseum',
    'colloseum': 'colosseum',
    'obscura': 'obscura',
    'movie': 'movie',
}

_TWO_CHOICE_TYPES = {'TWOCHOICETRACKER', 'TWOCHOICECOUNTER'}
_PAIRWISE_TYPES = {'PAIRWISEINTERACTIONTRACKER', 'PAIRWISEINTERACTIONCOUNTER'}

_NUMERIC_GLOBALS = (
    ('fps', False),
    ('mm_per_pixel', True),
    ('speed_window_seconds', True),
    ('walking_speed_mm_sec', False),
    ('sleep_threshold_min', False),
    ('wall_zone_mm', True),
    ('fly_length_mm', True),
    ('fly_width_mm', True),
)

## Pair-proximity settings where 0 is meaningful (it switches the behaviour
## off — no merges inferred, no hysteresis, no bridging) but a negative value
## is not.
_NONNEGATIVE_GLOBALS = (
    'merge_distance_mm',
    'encounter_hysteresis_mm',
    'encounter_gap_s',
    'encounter_min_s',
)


def normalize_rig(value) -> str:
    """Canonical rig name for *value*, or '' when it is blank."""
    raw = str(value or '').lower().replace(' ', '_').replace('-', '_')
    return RIG_ALIASES.get(raw, raw)


def validate_config(config) -> list[str]:
    """Return a list of human-readable problems with *config* (empty when valid)."""
    problems: list[str] = []

    if config is None:
        return ["The file is empty."]
    if not isinstance(config, dict):
        return [f"Top level must be a mapping of sections, not {type(config).__name__}."]

    global_cfg = config.get('global')
    if global_cfg is None:
        problems.append("Missing the 'global:' section.")
        global_cfg = {}
    elif not isinstance(global_cfg, dict):
        problems.append("'global:' must be a mapping of settings.")
        global_cfg = {}

    # Resolve the Experiment Type (absent -> Custom). A typed experiment may
    # fix the tracking type and constrain the rig / counting regions, in which
    # case the type owns those checks (see experiment_types); we defer to it and
    # skip the generic versions to avoid contradictory messages.
    from . import experiment_types
    try:
        exp_type = experiment_types.get_experiment_type(global_cfg.get('experiment_type'))
    except ValueError as err:
        problems.append(str(err))
        exp_type = experiment_types.get_experiment_type(None)

    valid_types = [t.name for t in Parameters.TrackingType]
    if exp_type.tracking_type is not None:
        # Owned by the type; the yaml should not carry it. A conflicting value
        # is reported by exp_type.validate(). Use the type's for the checks below.
        tracking_type = exp_type.tracking_type.name
    else:
        tracking_type = str(global_cfg.get('tracking_type', 'TRACKER')).upper()
        if tracking_type not in valid_types:
            problems.append(
                f"Unknown tracking_type '{tracking_type}'. Valid values: {', '.join(valid_types)}."
            )

    rig = normalize_rig(global_cfg.get('tracking_rig'))
    if exp_type.allowed_rigs is not None:
        pass  # the Experiment Type validates the rig against its allowed set
    elif not rig:
        problems.append("Missing tracking_rig — no calibration would be applied.")
    elif rig not in RIG_ALIASES.values():
        problems.append(
            f"Unknown tracking_rig '{global_cfg.get('tracking_rig')}'. "
            f"Valid values: {', '.join(sorted(set(RIG_ALIASES.values())))}."
        )
    elif rig == 'movie':
        for key in ('fps', 'mm_per_pixel'):
            if global_cfg.get(key) is None:
                problems.append(
                    f"tracking_rig 'movie' requires '{key}' — there is no rig preset to fall back on."
                )

    for key, must_be_positive in _NUMERIC_GLOBALS:
        if key not in global_cfg or global_cfg[key] is None:
            continue
        value = global_cfg[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            problems.append(f"global.{key} must be a number, not {value!r}.")
        elif must_be_positive and value <= 0:
            problems.append(f"global.{key} must be greater than zero.")

    for key in _NONNEGATIVE_GLOBALS:
        value = global_cfg.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            problems.append(f"global.{key} must be a number, not {value!r}.")
        elif value < 0:
            problems.append(f"global.{key} must be zero or more.")

    length, width = global_cfg.get('fly_length_mm'), global_cfg.get('fly_width_mm')
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
           for v in (length, width)) and width > length:
        problems.append("global.fly_width_mm must not exceed fly_length_mm.")

    micromove = global_cfg.get('micromove_speed_mm_sec')
    if micromove is not None:
        if not isinstance(micromove, (list, tuple)) or len(micromove) != 2:
            problems.append("global.micromove_speed_mm_sec must be a two-value [min, max] list.")
        elif not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in micromove):
            problems.append("global.micromove_speed_mm_sec values must be numbers.")
        elif micromove[0] >= micromove[1]:
            problems.append("global.micromove_speed_mm_sec: the minimum must be below the maximum.")

    distances = global_cfg.get('interaction_distances')
    if distances is not None:
        if not isinstance(distances, (list, tuple)) or not distances:
            problems.append("global.interaction_distances must be a non-empty list of numbers.")
        elif not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in distances):
            problems.append("global.interaction_distances values must be numbers.")

    cutoffs = global_cfg.get('facet_cutoffs')
    if cutoffs is not None:
        if not isinstance(cutoffs, (list, tuple)) or not cutoffs:
            problems.append("global.facet_cutoffs must be a non-empty list of minute boundaries.")
        elif not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in cutoffs):
            problems.append("global.facet_cutoffs values must be numbers.")
        else:
            values = list(cutoffs)
            if sorted(values) != values:
                problems.append("global.facet_cutoffs must increase from left to right.")
            if len(set(values)) != len(values):
                problems.append("global.facet_cutoffs values must be distinct.")
            if any(v <= 0 for v in values):
                problems.append("global.facet_cutoffs values must be greater than zero.")

    labels = global_cfg.get('facet_labels')
    if labels is not None:
        if not isinstance(labels, (list, tuple)) or not labels:
            problems.append("global.facet_labels must be a non-empty list of phase names.")
        elif not all(isinstance(v, str) and v.strip() for v in labels):
            problems.append("global.facet_labels values must be non-empty strings.")
        else:
            try:
                resolved = exp_type.resolve_facet_cutoffs(global_cfg)
            except Exception:  # noqa: BLE001 — malformed cutoffs already reported
                resolved = None
            if resolved is None:
                problems.append(
                    "global.facet_labels requires facet_cutoffs — without phases "
                    "there is nothing to name."
                )
            elif len(labels) != len(resolved) + 1:
                problems.append(
                    f"global.facet_labels must name every phase: facet_cutoffs "
                    f"{list(resolved)} creates {len(resolved) + 1} phases, but "
                    f"{len(labels)} names were given."
                )

    tracking_regions = config.get('tracking_regions')
    if not tracking_regions:
        problems.append("No tracking_regions defined — the analysis would have nothing to load.")
    elif not isinstance(tracking_regions, dict):
        problems.append("'tracking_regions:' must be a mapping of region name to settings.")
    else:
        for name, region in tracking_regions.items():
            if not isinstance(region, dict):
                problems.append(f"tracking_regions.{name} must be a mapping.")
                continue
            for axis in ('x_location_multiplier', 'y_location_multiplier'):
                if axis in region and region[axis] not in (-1, 1):
                    problems.append(
                        f"tracking_regions.{name}.{axis} must be -1 or 1, not {region[axis]!r}."
                    )

    counting_regions = config.get('counting_regions')
    if counting_regions is not None and not isinstance(counting_regions, dict):
        problems.append("'counting_regions:' must be a mapping of group name to settings.")
        counting_regions = None
    elif counting_regions:
        for name, group in counting_regions.items():
            if not isinstance(group, dict) or 'alias' not in group:
                problems.append(f"counting_regions.{name} is missing its 'alias' key.")
                continue
            aliases = [a.strip() for a in str(group['alias']).split(',') if a.strip()]
            if not aliases:
                problems.append(f"counting_regions.{name}.alias is empty.")

    if tracking_type in _TWO_CHOICE_TYPES and exp_type.required_counting_regions is None:
        n_groups = len(counting_regions) if counting_regions else 0
        if n_groups != 2:
            problems.append(
                f"{tracking_type} requires exactly two counting_regions groups; found {n_groups}."
            )

    if tracking_type in _PAIRWISE_TYPES and not global_cfg.get('interaction_distances') \
            and 'interaction_distances' not in exp_type.parameter_defaults:
        problems.append(
            f"{tracking_type} has no global.interaction_distances; "
            "the default of [8] mm will be used."
        )

    # Type-specific constraints (rig subset, required Light/NoLight, owned-field
    # conflicts). Empty for a Custom Experiment.
    problems += exp_type.validate(config)

    return problems


def validate_config_file(path) -> list[str]:
    """Validate the YAML document at *path*, including parse and read errors."""
    if not os.path.isfile(path):
        return [f"File not found: {path}"]
    try:
        with open(path, encoding='utf-8') as handle:
            config = yaml.safe_load(handle)
    except yaml.YAMLError as err:
        return [f"YAML syntax error: {err}"]
    except OSError as err:
        return [f"Could not read the file: {err}"]
    return validate_config(config)
