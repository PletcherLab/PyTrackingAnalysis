"""Cached, refreshable model lists for the AI providers.

The dialog's model dropdown is populated from a per-provider cache at
``~/.config/pytrackinganalysis/ai_models.json``, refreshed from the provider's
own model-listing API — automatically when the cache is older than
:data:`TTL_DAYS`, or on demand via the dialog's refresh button. Every failure
path falls back to the provider's curated ``models`` tuple, so the feature
works offline and the dropdown is never empty (ADR-0004: network problems
degrade, they never block).
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from ..io_utils import atomic_write_text
from .base import AISummarizer

#: A cache older than this is refreshed when the AI Summary dialog opens.
TTL_DAYS = 30.0

def cache_file() -> Path:
    """Beside ``ui.json``, worked out on every call for the same reason
    (:func:`~pytrackinganalysis.ui.settings.config_dir`)."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "pytrackinganalysis" / "ai_models.json"


def _load() -> dict:
    try:
        data = json.loads(cache_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _entry(provider: type[AISummarizer]) -> dict | None:
    entry = _load().get(provider.provider_name)
    if (isinstance(entry, dict) and isinstance(entry.get("models"), list)
            and entry["models"]):
        return entry
    return None


def cached_models(provider: type[AISummarizer]) -> list[str]:
    """The cached model ids for *provider*, else its curated fallback."""
    entry = _entry(provider)
    if entry:
        return [str(model) for model in entry["models"]]
    return list(provider.models)


def cache_age_days(provider: type[AISummarizer]) -> float | None:
    """Days since *provider*'s list was fetched, or ``None`` if never."""
    entry = _entry(provider)
    if not entry:
        return None
    try:
        fetched = datetime.fromisoformat(str(entry.get("fetched")))
    except (TypeError, ValueError):
        return None
    return (datetime.now() - fetched).total_seconds() / 86_400


def needs_refresh(provider: type[AISummarizer]) -> bool:
    age = cache_age_days(provider)
    return age is None or age > TTL_DAYS


def refresh(provider: type[AISummarizer]) -> list[str]:
    """Fetch *provider*'s current model list and cache it.

    The curated models lead the list (they are the recommended defaults);
    everything else the API reports follows in the API's order. Raises
    :class:`~pytrackinganalysis.ai.base.AISummaryError` on any fetch problem —
    the cache is left untouched so the previous list keeps working.
    """
    models = _known_first(provider.fetch_models(), provider.models)
    data = _load()
    data[provider.provider_name] = {
        "models": models,
        "fetched": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        path = cache_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            path,
            lambda handle: handle.write(json.dumps(data, indent=2,
                                                   sort_keys=True)),
        )
    except OSError:
        pass  # an unwritable config dir degrades to curated/stale lists
    return models


def _known_first(ids: list[str], preferred: tuple[str, ...]) -> list[str]:
    """*ids* de-duplicated, with the *preferred* (curated) ones leading."""
    unique = list(dict.fromkeys(ids))
    front = [model for model in preferred if model in unique]
    return front + [model for model in unique if model not in front]
