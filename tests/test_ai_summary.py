"""Tests for the AI Summary feature (CONTEXT.md, ADR-0004): the saved-file
lifecycle, report embedding, the payload builder, the ExperimentType prompt
hook, and the provider layer's availability gating.

Hermetic — no provider SDK call is ever made; providers are exercised through
a fake subclass and monkeypatched environments.
"""

from __future__ import annotations

import os
import pathlib

import numpy as np
import pandas as pd
import pytest

from pytrackinganalysis import Parameters
from pytrackinganalysis.ai import base as ai_base
from pytrackinganalysis.ai.payload import build_payload
from pytrackinganalysis.report import model as m

_PHASES = [(0, 10), (10, 70), (70, float("inf"))]


class _FakeArena:
    def __init__(self, summary):
        self._summary = summary
        self.experiment_name = "E"

    def summarize(self, range_minutes=(0, 0), remove_partners=False, **kw):
        return self._summary.copy()

    def summarize_facet(self, cutoffs=None, remove_partners=False, **kw):
        frames = []
        for win in _PHASES:
            tmp = self._summary.copy()
            tmp["FacetRange"] = [win] * len(tmp)
            frames.append(tmp)
        return pd.concat(frames, ignore_index=True)

    def supports_data_quality(self):
        return False


def _twochoice_summary():
    treat = ["control"] * 4 + ["chr"] * 4
    n = len(treat)
    return pd.DataFrame({
        "Treatment": treat,
        "TotalDistancePerMin": np.linspace(8, 13, n),
        "PercWalking": np.linspace(0.2, 0.4, n),
        "PercResting": np.linspace(0.5, 0.3, n),
        "FinalPI": np.linspace(-0.5, 0.5, n),
        "FinalPercentage": np.linspace(0.3, 0.8, n),
        "Transitions": np.linspace(2, 16, n),
        "TransitionsPerMin": np.linspace(0.2, 1.6, n),
    })


def _model_exp(tmp_path, facet_cutoffs=(10,)):
    """A minimal real ``Experiment`` (bypassing __init__) over a fake arena."""
    from pytrackinganalysis import experiment_types as et
    from pytrackinganalysis.Experiment import Experiment

    analysis = tmp_path / "analysis"
    qc = tmp_path / "qc"
    analysis.mkdir(exist_ok=True)
    qc.mkdir(exist_ok=True)

    class _Exp(Experiment):
        def __init__(self):
            self.arena = _FakeArena(_twochoice_summary())
            self.project_directory = str(tmp_path)
            self.analysis_path = str(analysis) + os.sep
            self.qc_path = str(qc) + os.sep
            self.config = {"global": {"tracking_rig": "colosseum"}}
            self.facet_cutoffs = list(facet_cutoffs) if facet_cutoffs else None
            self.experiment_type = et.get_experiment_type(None)
            self.parameters = Parameters.Parameters(
                tracking_type=Parameters.TrackingType.TWOCHOICETRACKER)

    return _Exp()


# --------------------------------------------------------------------------
# Saved-file lifecycle
# --------------------------------------------------------------------------

def test_ai_summary_roundtrip_with_provenance(tmp_path):
    exp = _model_exp(tmp_path)
    assert exp.read_ai_summary() is None

    path = exp.write_ai_summary("First para.\n\nSecond para.",
                                "Anthropic (Claude)", "claude-opus-5")
    assert os.path.basename(path) == "E_AI_Summary.txt"
    provenance, body = exp.read_ai_summary()
    assert provenance.startswith("Anthropic (Claude) claude-opus-5, ")
    assert body == "First para.\n\nSecond para."

    exp.delete_ai_summary()
    assert exp.read_ai_summary() is None
    exp.delete_ai_summary()  # deleting twice is fine


def test_writing_a_summary_also_writes_a_greppable_markdown_copy(tmp_path):
    """A fixed filename, so a later agent can find every narrative in a tree
    with one glob — no project or replicate name required."""
    from pytrackinganalysis.ai.narrative import AI_NARRATIVE_FILENAME

    exp = _model_exp(tmp_path)
    exp.write_ai_summary("Chrimson flies avoided the lit side.",
                         "Anthropic (Claude)", "claude-opus-5")
    md = pathlib.Path(exp.analysis_path) / AI_NARRATIVE_FILENAME
    assert md.is_file()
    body = md.read_text(encoding="utf-8")
    assert "Chrimson flies avoided the lit side." in body
    # Front matter says what the prose is about and where it came from.
    assert body.startswith("# AI narrative — E")
    assert "Anthropic (Claude) claude-opus-5" in body
    assert "Experiment (one replicate)" in body


def test_the_markdown_copy_dies_with_its_summary(tmp_path):
    """ADR-0004: a narrative is a derivative of one run. A second copy that
    outlived the run would be exactly the stale prose that rule forbids."""
    from pytrackinganalysis.ai.narrative import AI_NARRATIVE_FILENAME

    exp = _model_exp(tmp_path)
    exp.write_ai_summary("Old prose.", "Anthropic (Claude)", "claude-opus-5")
    md = pathlib.Path(exp.analysis_path) / AI_NARRATIVE_FILENAME
    assert md.is_file()
    exp.delete_ai_summary()
    assert not md.exists()
    exp.delete_ai_summary()          # deleting twice is fine


def test_project_summary_writes_its_own_markdown_copy(tmp_path):
    from pytrackinganalysis import project as prj
    from pytrackinganalysis.ai.narrative import AI_NARRATIVE_FILENAME
    from test_project import _make_project

    _make_project(tmp_path)
    project = prj.Project(str(tmp_path))
    project.write_ai_summary("Pooled PI favoured Chrimson.",
                             "Anthropic (Claude)", "claude-opus-5")
    md = pathlib.Path(project.analysis_path) / AI_NARRATIVE_FILENAME
    body = md.read_text(encoding="utf-8")
    assert "Pooled PI favoured Chrimson." in body
    # Project level names its replicates, so the file stands on its own.
    assert "Project (2 replicates pooled)" in body
    assert "Rep1, Rep2" in body
    project.delete_ai_summary()
    assert not md.exists()


def test_hand_written_summary_file_has_no_provenance(tmp_path):
    # A file without the marker (hand-edited) still yields its body — the
    # report must never silently drop what the file says.
    exp = _model_exp(tmp_path)
    with open(exp._ai_summary_path(), "w", encoding="utf-8") as handle:
        handle.write("Just some prose.\n")
    assert exp.read_ai_summary() == (None, "Just some prose.")


def test_run_analysis_deletes_the_saved_summary(tmp_path):
    # ADR-0004: the summary is a derivative of a single run; a re-run removes
    # it rather than letting stale prose sit beside fresh figures.
    exp = _model_exp(tmp_path)
    exp.write_ai_summary("Old prose.", "Anthropic (Claude)", "claude-opus-5")
    for name in ("experiment_summary", "run_qc", "save_summary", "stats",
                 "save_plots"):
        setattr(exp, name, lambda *a, **k: None)
    exp.run_analysis()
    assert exp.read_ai_summary() is None


# --------------------------------------------------------------------------
# Report embedding
# --------------------------------------------------------------------------

def test_report_embeds_labeled_ai_summary_when_file_exists(tmp_path):
    exp = _model_exp(tmp_path)
    exp.write_ai_summary("Flies preferred light.\n\nNo animals were excluded.",
                         "OpenAI", "gpt-5.2")

    blocks = exp.build_report_model().blocks
    headings = [b.text for b in blocks if isinstance(b, m.Heading)]
    assert "AI Summary" in headings
    paragraphs = [b.text for b in blocks if isinstance(b, m.Paragraph)]
    label = [p for p in paragraphs if p.startswith("AI-generated summary — ")]
    assert label and "OpenAI gpt-5.2" in label[0]
    assert "Review before relying on it." in label[0]
    assert "Flies preferred light." in paragraphs
    assert "No animals were excluded." in paragraphs
    # The section renders once, as prose — the txt must not also be embedded
    # as a raw Preformatted block by the *.txt sweep.
    assert not any(isinstance(b, m.Preformatted) and b.title == "E_AI_Summary"
                   for b in blocks)


def test_report_omits_ai_summary_without_the_file(tmp_path):
    blocks = _model_exp(tmp_path).build_report_model().blocks
    assert "AI Summary" not in [b.text for b in blocks
                                if isinstance(b, m.Heading)]


# --------------------------------------------------------------------------
# Payload builder
# --------------------------------------------------------------------------

def test_payload_serializes_report_and_csvs_and_collects_figures(tmp_path):
    exp = _model_exp(tmp_path)
    _twochoice_summary().to_csv(
        os.path.join(exp.analysis_path, "E_Summary.csv"), index=False)

    payload = build_payload(exp)
    assert "=== Analysis ===" in payload.text
    assert "--- E_Summary.csv ---" in payload.text
    assert "FinalPI" in payload.text                    # CSV content included
    assert payload.images, "report figures should be attached as images"
    for _title, png in payload.images:
        assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_payload_never_contains_the_previous_ai_summary(tmp_path):
    # ADR-0004: a new summary must not quote its predecessor.
    exp = _model_exp(tmp_path)
    exp.write_ai_summary("UNIQUE-OLD-SUMMARY-SENTINEL",
                         "Anthropic (Claude)", "claude-opus-5")
    payload = build_payload(exp)
    assert "UNIQUE-OLD-SUMMARY-SENTINEL" not in payload.text
    # ...even though it IS a *.txt in analysis/ (excluded by name, not luck).
    assert "E_AI_Summary" not in payload.text


# --------------------------------------------------------------------------
# ExperimentType prompt hook
# --------------------------------------------------------------------------

def test_ai_summary_prompt_base_and_valence():
    from pytrackinganalysis import experiment_types as et

    base_prompt = et.get_experiment_type(None).ai_summary_prompt()
    assert "do not perform your own" in base_prompt
    # The mandated structure: quality -> design -> results, in that order.
    quality = base_prompt.index("'Data quality:'")
    design = base_prompt.index("'Experimental design:'")
    results = base_prompt.index("'Results:'")
    assert quality < design < results
    valence_prompt = et.get_experiment_type("Valence").ai_summary_prompt()
    assert valence_prompt.startswith(base_prompt)
    assert "Preference Index" in valence_prompt


# --------------------------------------------------------------------------
# Provider layer (no network)
# --------------------------------------------------------------------------

@pytest.fixture
def clean_ai_env(monkeypatch):
    """No provider keys, and .env loading disabled (the repo has a real one)."""
    monkeypatch.setattr(ai_base, "_env_loaded", True)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def test_availability_is_presence_gated(clean_ai_env, monkeypatch):
    from pytrackinganalysis import ai

    assert ai.available_providers() == []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert ai.available_providers() == [ai.AnthropicSummarizer]
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert ai.available_providers() == [ai.AnthropicSummarizer,
                                        ai.OpenAISummarizer]


def test_get_summarizer_without_key_raises(clean_ai_env):
    from pytrackinganalysis import ai

    with pytest.raises(ai.AISummaryError, match="ANTHROPIC_API_KEY"):
        ai.get_summarizer("anthropic")
    with pytest.raises(ai.AISummaryError, match="Unknown AI provider"):
        ai.get_summarizer("skynet")


def test_get_summarizer_uses_default_and_explicit_model(clean_ai_env, monkeypatch):
    from pytrackinganalysis import ai

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert ai.get_summarizer("openai").model == ai.OpenAISummarizer.models[0]
    assert ai.get_summarizer("openai", model="gpt-4o").model == "gpt-4o"


def test_openai_model_filter_keeps_chat_families_only():
    from pytrackinganalysis.ai.openai_provider import _filter_model_ids

    ids = [
        "gpt-5.2", "gpt-5.2-mini", "gpt-4o", "o3", "o4-mini",
        "gpt-4o-2024-08-06",            # dated duplicate of an alias
        "gpt-4o-audio-preview", "gpt-4o-realtime-preview",
        "gpt-3.5-turbo", "text-embedding-3-large", "whisper-1",
        "dall-e-3", "davinci-002", "chatgpt-4o-latest", "tts-1",
        "omni-moderation-latest", "codex-mini-latest",
    ]
    assert _filter_model_ids(ids) == ["gpt-5.2", "gpt-5.2-mini", "gpt-4o",
                                      "o3", "o4-mini"]


def test_anthropic_model_filter_keeps_claude_ids():
    from pytrackinganalysis.ai.anthropic_provider import _filter_model_ids

    assert _filter_model_ids(["claude-opus-5", "not-a-model"]) == \
        ["claude-opus-5"]


def test_model_catalog_cache_roundtrip_and_fallback(tmp_path, monkeypatch):
    from pytrackinganalysis.ai import AnthropicSummarizer, model_catalog

    monkeypatch.setattr(model_catalog, "cache_file",
                        lambda: tmp_path / "ai_models.json")
    # No cache yet: curated fallback, and a refresh is wanted.
    assert model_catalog.cached_models(AnthropicSummarizer) == \
        list(AnthropicSummarizer.models)
    assert model_catalog.needs_refresh(AnthropicSummarizer)

    monkeypatch.setattr(
        AnthropicSummarizer, "fetch_models",
        classmethod(lambda cls: ["claude-sonnet-5", "claude-new-9",
                                 "claude-opus-5"]))
    refreshed = model_catalog.refresh(AnthropicSummarizer)
    # Curated models lead (recommended defaults), new ones follow.
    assert refreshed == ["claude-opus-5", "claude-sonnet-5", "claude-new-9"]
    assert model_catalog.cached_models(AnthropicSummarizer) == refreshed
    assert not model_catalog.needs_refresh(AnthropicSummarizer)
    assert model_catalog.cache_age_days(AnthropicSummarizer) < 1


def test_model_catalog_failed_refresh_keeps_old_cache(tmp_path, monkeypatch):
    from pytrackinganalysis.ai import AISummaryError, OpenAISummarizer, model_catalog

    monkeypatch.setattr(model_catalog, "cache_file",
                        lambda: tmp_path / "ai_models.json")
    monkeypatch.setattr(OpenAISummarizer, "fetch_models",
                        classmethod(lambda cls: ["gpt-5.2", "gpt-6"]))
    model_catalog.refresh(OpenAISummarizer)

    def _boom(cls):
        raise AISummaryError("offline")

    monkeypatch.setattr(OpenAISummarizer, "fetch_models", classmethod(_boom))
    with pytest.raises(AISummaryError, match="offline"):
        model_catalog.refresh(OpenAISummarizer)
    assert model_catalog.cached_models(OpenAISummarizer) == ["gpt-5.2", "gpt-6"]


def test_summarize_strips_and_rejects_empty(clean_ai_env, monkeypatch):
    from pytrackinganalysis.ai import SummaryPayload

    monkeypatch.setenv("FAKE_API_KEY", "k")

    class _Fake(ai_base.AISummarizer):
        provider_name = "fake"
        display_name = "Fake"
        env_var = "FAKE_API_KEY"
        models = ("fake-1",)
        reply = "  prose \n"

        def _request(self, instructions, payload):
            return self.reply

    fake = _Fake()
    assert fake.summarize(SummaryPayload(text="t"), "do it") == "prose"
    fake.reply = "   "
    with pytest.raises(ai_base.AISummaryError, match="empty summary"):
        fake.summarize(SummaryPayload(text="t"), "do it")
