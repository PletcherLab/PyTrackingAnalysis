"""Experiment Types: named bundles that select a Tracking Type and constrain an
experiment end to end (config, analyses, report, outputs).

See ``CONTEXT.md`` for the domain language and ``docs/adr/0001``/``0002`` for the
two architectural decisions (the type owns/derives config; it is a composed
base-class strategy).
"""

from .base import ExperimentType, sole_treatment
from .custom import CustomExperimentType
from .paired_open_field import PairedOpenFieldExperimentType
from .registry import available_experiment_types, get_experiment_type
from .valence import ValenceExperimentType

__all__ = [
    "ExperimentType",
    "sole_treatment",
    "CustomExperimentType",
    "ValenceExperimentType",
    "PairedOpenFieldExperimentType",
    "get_experiment_type",
    "available_experiment_types",
]
