"""Registry that maps an ``experiment_type`` name to its ``ExperimentType``.

Adding a type = adding a subclass and one line here (ADR-0002).
"""

from __future__ import annotations

from .base import ExperimentType
from .custom import CustomExperimentType
from .paired_open_field import PairedOpenFieldExperimentType
from .valence import ValenceExperimentType

# name (lowercase) -> class
_REGISTRY: dict[str, type[ExperimentType]] = {
    CustomExperimentType.name.lower(): CustomExperimentType,
    ValenceExperimentType.name.lower(): ValenceExperimentType,
    PairedOpenFieldExperimentType.name.lower(): PairedOpenFieldExperimentType,
}


def get_experiment_type(name) -> ExperimentType:
    """Return an ExperimentType instance for *name*.

    ``None`` or an empty/blank name resolves to the Custom Experiment (today's
    freeform behaviour). An unknown name raises ``ValueError``.
    """
    if name is None or str(name).strip() == "":
        return CustomExperimentType()
    key = str(name).strip().lower()
    try:
        return _REGISTRY[key]()
    except KeyError as err:
        known = ", ".join(sorted(cls.name for cls in _REGISTRY.values()))
        raise ValueError(
            f"Unknown experiment_type '{name}'. Known types: {known}."
        ) from err


def available_experiment_types() -> list[ExperimentType]:
    """One instance of each registered type, Custom first, then in
    registration order — the first concrete type is the default a new
    Project is offered, so adding a type must not silently displace it."""
    return [cls() for cls in _REGISTRY.values()]
