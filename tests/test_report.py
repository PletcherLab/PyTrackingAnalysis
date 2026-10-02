"""Tests for the report subsystem: the backend-agnostic document model, the
reportlab backend, the backend dispatcher, and the report-native figure builder.

Hermetic — nothing is read from disk except the temp PDF these tests write.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from pytrackinganalysis import Parameters, report_figures
from pytrackinganalysis import report
from pytrackinganalysis.report import model as m


def _sample_report() -> report.Report:
    r = report.Report("UnitExperiment")
    r.add(m.Cover("UnitExperiment", "Tracking Analysis Report",
                  metadata=[("Tracking type", "TWOCHOICETRACKER"),
                            ("Project", "C:/" + "long/" * 40 + "proj")],
                  status=m.StatusLine("Data quality: 3/4 ok", m.Level.ERROR)))
    r.add(m.SectionDivider("Analysis"))
    r.add(m.Preformatted("a" * 400, title="Stats"))          # must not overflow
    r.add(m.Table(["Treatment", "HighQuality"],
                  [["control", "0.98"], ["chr", "0.72"]],
                  title="Summary", row_levels=[m.Level.OK, m.Level.ERROR]))
    return r


def test_reportlab_backend_writes_a_pdf(tmp_path):
    out = tmp_path / "r.pdf"
    report.render(_sample_report(), str(out))
    assert out.exists()
    head = out.read_bytes()[:5]
    assert head == b"%PDF-", "output is not a PDF"
    assert out.stat().st_size > 1500


def test_long_monospaced_line_is_wrapped_not_clipped():
    # The backend soft-wraps long monospaced lines so they cannot run off the
    # page; the wrap width must be a sane positive number of characters.
    from pytrackinganalysis.report.backends import reportlab_backend as rb

    assert rb._MONO_WRAP > 40
    wrapped = rb._wrap_mono("x" * 400)
    assert all(len(line) <= rb._MONO_WRAP for line in wrapped.splitlines())


def test_dispatcher_lists_reportlab_and_rejects_unknown(tmp_path):
    assert "reportlab" in report.available_backends()
    with pytest.raises(ValueError, match="Unknown report backend"):
        report.render(_sample_report(), str(tmp_path / "x.pdf"), backend="nope")


def test_model_stays_free_of_rendering_imports():
    # The whole point of the split: the document model must not drag in a
    # rendering engine. Importing it must not import reportlab.
    import importlib
    import sys

    sys.modules.pop("reportlab", None)
    importlib.reload(importlib.import_module("pytrackinganalysis.report.model"))
    assert "reportlab" not in sys.modules


_PHASES = [(0, 10), (10, 70), (70, float("inf"))]  # the Valence default structure


class _FakeArena:
    def __init__(self, summary, dq=None):
        self._summary = summary
        self._dq = dq

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
        return self._dq is not None

    def get_data_quality(self, range_minutes=(0, 0)):
        if self._dq is None:
            raise ValueError("no dq")
        return self._dq.copy()


class _FakeExp:
    def __init__(self, tracking_type, summary, dq=None, facet_cutoffs=None):
        self.arena = _FakeArena(summary, dq)
        self.facet_cutoffs = facet_cutoffs
        self.parameters = Parameters.Parameters(tracking_type=tracking_type)


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


def test_analysis_figures_built_for_two_choice():
    exp = _FakeExp(Parameters.TrackingType.TWOCHOICETRACKER, _twochoice_summary())
    figs = report_figures.build_analysis_figures(exp)
    # At least the Choice and Locomotion figures, each carrying PNG bytes.
    assert len(figs) >= 2
    for fig in figs:
        assert isinstance(fig, m.Figure)
        assert fig.data[:8] == b"\x89PNG\r\n\x1a\n"
        assert fig.width_in > 0 and fig.height_in > 0


def test_analysis_figures_empty_when_no_treatments():
    summary = _twochoice_summary()
    summary["Treatment"] = ""  # every row unassigned
    exp = _FakeExp(Parameters.TrackingType.TWOCHOICETRACKER, summary)
    assert report_figures.build_analysis_figures(exp) == []


def test_qc_figure_built_when_supported():
    dq = pd.DataFrame({"Tracker": [f"T_{i}" for i in range(5)],
                       "HighQuality": [0.6, 0.85, 0.95, 0.99, 0.7]})
    exp = _FakeExp(Parameters.TrackingType.TWOCHOICETRACKER,
                   _twochoice_summary(), dq=dq)
    figs = report_figures.build_qc_figures(exp)
    assert len(figs) == 1 and isinstance(figs[0], m.Figure)


def test_qc_figure_absent_when_unsupported():
    exp = _FakeExp(Parameters.TrackingType.TWOCHOICECOUNTER, _twochoice_summary())
    assert report_figures.build_qc_figures(exp) == []


def test_faceted_figures_empty_without_cutoffs():
    exp = _FakeExp(Parameters.TrackingType.TWOCHOICETRACKER, _twochoice_summary(),
                   facet_cutoffs=None)
    assert report_figures.build_faceted_figures(exp) == []


def test_faceted_figures_built_with_cutoffs():
    exp = _FakeExp(Parameters.TrackingType.TWOCHOICETRACKER, _twochoice_summary(),
                   facet_cutoffs=[10])
    figs = report_figures.build_faceted_figures(exp)
    # Choice-by-phase and Locomotion-by-phase at least; each carries PNG bytes.
    assert len(figs) >= 2
    assert all(isinstance(f, m.Figure) and f.data[:4] == b"\x89PNG" for f in figs)


def test_faceted_figures_use_experiment_type_phase_labels(monkeypatch):
    # A Valence experiment_type should label phases Acclimation/Experiment/…
    # rather than raw minute ranges. Phases are the per-panel titles in the
    # faceted layout; capture what each panel's set_title receives.
    from pytrackinganalysis import experiment_types as et
    from pytrackinganalysis import report_figures as rf

    captured = []
    real = rf.plt.Axes.set_title

    def _spy(self, label, *a, **k):
        captured.append(str(label))
        return real(self, label, *a, **k)

    monkeypatch.setattr(rf.plt.Axes, "set_title", _spy)

    exp = _FakeExp(Parameters.TrackingType.TWOCHOICETRACKER, _twochoice_summary(),
                   facet_cutoffs=[10, 70])
    exp.experiment_type = et.get_experiment_type("Valence")
    rf.build_faceted_figures(exp)
    assert captured[:3] == ["Acclimation", "Experiment", "Cooldown"]


def test_faceted_figures_prefer_configured_facet_labels(monkeypatch):
    # A facet_labels key in the yaml renames the phases in the report figures.
    from pytrackinganalysis import experiment_types as et
    from pytrackinganalysis import report_figures as rf

    captured = []
    real = rf.plt.Axes.set_title

    def _spy(self, label, *a, **k):
        captured.append(str(label))
        return real(self, label, *a, **k)

    monkeypatch.setattr(rf.plt.Axes, "set_title", _spy)

    exp = _FakeExp(Parameters.TrackingType.TWOCHOICETRACKER, _twochoice_summary(),
                   facet_cutoffs=[10, 70])
    exp.experiment_type = et.get_experiment_type("Valence")
    exp.config = {"global": {"facet_labels": ["Baseline", "Stimulus", "Recovery"]}}
    rf.build_faceted_figures(exp)
    assert captured[:3] == ["Baseline", "Stimulus", "Recovery"]


def _model_exp(tmp_path, exp_type_name=None, facet_cutoffs=(10, 70)):
    """A minimal real ``Experiment`` (bypassing __init__) over a fake arena,
    for exercising build_report_model / notes without a real project."""
    from pytrackinganalysis import experiment_types as et
    from pytrackinganalysis.Experiment import Experiment

    analysis = tmp_path / "analysis"
    qc = tmp_path / "qc"
    analysis.mkdir(exist_ok=True)
    qc.mkdir(exist_ok=True)

    class _Exp(Experiment):
        def __init__(self):
            self.arena = _FakeArena(_twochoice_summary())
            self.arena.experiment_name = "E"
            self.project_directory = str(tmp_path)
            self.analysis_path = str(analysis) + os.sep
            self.qc_path = str(qc) + os.sep
            self.config = {"global": {"tracking_rig": "colosseum"}}
            self.facet_cutoffs = list(facet_cutoffs) if facet_cutoffs else None
            self.experiment_type = et.get_experiment_type(exp_type_name)
            self.parameters = Parameters.Parameters(
                tracking_type=Parameters.TrackingType.TWOCHOICETRACKER)

    return _Exp()


def test_report_model_renders_stats_as_table_not_text(tmp_path):
    # The raw Stats.txt and experiment_summary.txt are NOT embedded as
    # monospaced text: the comparisons appear as the semantic
    # "Statistical comparisons" table and the summary as its own structured
    # section. Raw CSV data is still never dumped into the PDF.
    analysis = tmp_path / "analysis"
    analysis.mkdir()
    (tmp_path / "qc").mkdir()
    _twochoice_summary().to_csv(analysis / "E_Summary.csv", index=False)
    (analysis / "E_Stats.txt").write_text("control vs chr: T=3.1, p=0.005\n",
                                           encoding="utf-8")
    (analysis / "E_experiment_summary.txt").write_text("── boxy ──\n",
                                                       encoding="utf-8")

    model = _model_exp(tmp_path, facet_cutoffs=[10]).build_report_model(qc_cutoff=0.9)
    kinds = [type(b).__name__ for b in model.blocks]
    assert "Preformatted" not in kinds      # neither txt is embedded raw
    assert kinds.count("Figure") >= 2

    tables = [b for b in model.blocks if isinstance(b, m.Table)]
    stats = [t for t in tables if t.title == "Statistical comparisons"]
    assert stats, "expected the pairwise-comparison table"
    assert stats[0].columns[:2] == ["Metric", "Phase"]
    assert "p-value" in stats[0].columns
    # One row per metric x phase x pair: 3 two-choice metrics x 2 phases.
    assert len(stats[0].rows) == 6

    dividers = [b.title for b in model.blocks
                if type(b).__name__ == "SectionDivider"]
    assert "Experiment Summary" in dividers
    assert any(t.title == "Configuration" for t in tables)
    assert any(t.title == "Parameters" for t in tables)
    # Two treatments: compared with each other, not against indifference.
    assert not any(t.title == "Tests against indifference" for t in tables)


def _single_treatment_summary():
    s = _twochoice_summary()
    s["Treatment"] = "chr"
    s["FinalPI"] = np.linspace(0.3, 0.7, len(s))       # a clear preference
    return s


def test_single_treatment_report_tests_against_indifference(tmp_path):
    """One treatment level leaves no pair to compare; PI is tested against 0
    and Percentage against 0.5 instead, in a table of its own."""
    exp = _model_exp(tmp_path, exp_type_name="Valence", facet_cutoffs=[10])
    exp.arena = _FakeArena(_single_treatment_summary())
    exp.arena.experiment_name = "E"
    tables = report_figures.build_stats_table(exp)
    assert [t.title for t in tables] == ["Tests against indifference"]
    rows = tables[0].rows
    # PI and Percentage once per phase (2 phases); movement has no null.
    assert [r[0] for r in rows] == ["FinalPI"] * 2 + ["FinalPercentage"] * 2
    pi = rows[0]
    assert pi[2] == "chr (n=8)" and pi[4] == "0" and pi[-1] == "yes"
    assert rows[2][4] == "0.5 (50%)"


def test_single_treatment_stats_file_tests_against_indifference(capsys):
    from pytrackinganalysis.Arena import Arena

    class _StatsArena(_FakeArena):
        _treatment_arms = Arena.__dict__["_treatment_arms"]
        _print_group_comparison = Arena.__dict__["_print_group_comparison"]
        _print_indifference_test = Arena.__dict__["_print_indifference_test"]
        run_pairwise_comparisons_facet = Arena.run_pairwise_comparisons_facet

    arena = _StatsArena(_single_treatment_summary())
    assert arena.run_pairwise_comparisons_facet(metric="FinalPI") is None
    out = capsys.readouterr().out
    assert out.count("Test against indifference") == 3       # one per phase
    assert "chr (n=8" in out and "indifference = 0:" in out
    assert "one-sample t-test" in out
    assert "3 independent t-tests" in out          # the Bonferroni note
    # A metric with no null stays "not applicable" with one treatment.
    msg = arena.run_pairwise_comparisons_facet(metric="TotalDistancePerMin")
    assert msg.startswith("Not applicable")


# --------------------------------------------------------------------------
# Valence-specific report sections
# --------------------------------------------------------------------------

def _valence_exp(summary=None, cutoffs=(10, 70)):
    from pytrackinganalysis import experiment_types as et

    exp = _FakeExp(Parameters.TrackingType.TWOCHOICETRACKER,
                   summary if summary is not None else _twochoice_summary(),
                   facet_cutoffs=list(cutoffs))
    exp.experiment_type = et.get_experiment_type("Valence")
    exp.config = {"global": {}}
    return exp


def test_valence_sections_have_headline_stats_and_persistence():
    blocks = report_figures.build_valence_sections(_valence_exp())
    titles = [getattr(b, "title", None) for b in blocks]
    assert "Headline result" in titles
    assert "Emergence & persistence" in titles
    # Two treatments -> a Welch pairwise table for the primary phase.
    tables = [b for b in blocks if isinstance(b, m.Table)]
    assert len(tables) == 1 and "Welch" in tables[0].caption
    paragraphs = [b.text for b in blocks if isinstance(b, m.Paragraph)]
    assert any("Experiment phase" in p for p in paragraphs)


def test_valence_pi_over_time_uses_trackers():
    class _FakeTwoChoice:
        def __init__(self, treat, offset):
            self._treat, self._off = treat, offset

        def get_treatment(self):
            return self._treat

        def get_time_dependent_pi(self, window_size_min=10, step_size_min=5,
                                  range_minutes=(0, 0)):
            ends = [10, 15, 20, 25]
            return pd.DataFrame({
                "StartMin": [e - window_size_min for e in ends],
                "EndMin": ends,
                "PI": [0.1 + self._off, 0.3 + self._off,
                       0.2 + self._off, 0.4 + self._off],
            })

    exp = _valence_exp()
    exp.arena.trackers = {"T_0": _FakeTwoChoice("control", 0.0),
                          "T_1": _FakeTwoChoice("control", 0.1),
                          "T_2": _FakeTwoChoice("chr", -0.2)}
    titles = [getattr(b, "title", None)
              for b in report_figures.build_valence_sections(exp)]
    assert "Preference over time" in titles

    # The trace reads trackers directly, so it must honour the Low-Transition
    # Exclusion itself: with every 'chr' fly excluded (by name), the trace only
    # carries 'control'.
    for name, tracker in exp.arena.trackers.items():
        tracker.name = name
    exp.arena.excluded_names = {"T_2"}
    captured = {}
    real_plot = report_figures.plt.Axes.plot

    def _spy(self, *a, **k):
        if "label" in k:
            captured.setdefault("labels", []).append(k["label"])
        return real_plot(self, *a, **k)

    report_figures.plt.Axes.plot = _spy
    try:
        report_figures._valence_pi_over_time(exp)
    finally:
        report_figures.plt.Axes.plot = real_plot
    assert captured["labels"] and all("control" in l for l in captured["labels"])


def test_valence_sections_survive_thin_data():
    # No assigned treatments -> every section skips; no exception, no blocks.
    empty = _twochoice_summary().assign(Treatment="")
    assert report_figures.build_valence_sections(_valence_exp(summary=empty)) == []


def test_report_model_leads_with_valence_sections(tmp_path):
    model = _model_exp(tmp_path, exp_type_name="Valence").build_report_model()
    kinds = [type(b).__name__ for b in model.blocks]
    assert "Table" in kinds                      # the pairwise-stats table
    first_figure = next(b for b in model.blocks if isinstance(b, m.Figure))
    assert first_figure.title == "Headline result"


# --------------------------------------------------------------------------
# Run notes and cover metadata
# --------------------------------------------------------------------------

def test_run_notes_round_trip_and_render(tmp_path):
    exp = _model_exp(tmp_path)
    assert exp.read_run_notes() == ""
    exp.write_run_notes("Genotype: w1118\n\nLights at 50%")
    assert "w1118" in exp.read_run_notes()

    model = exp.build_report_model()
    paragraphs = [b.text for b in model.blocks if isinstance(b, m.Paragraph)]
    assert any("w1118" in p for p in paragraphs)
    assert any("Lights at 50%" in p for p in paragraphs)
    # The notes render once, after the cover — never again as a text appendix.
    pre_titles = [b.title for b in model.blocks if isinstance(b, m.Preformatted)]
    assert "E_Notes" not in pre_titles

    exp.write_run_notes("   ")                   # blank clears
    assert exp.read_run_notes() == ""
    assert not any(isinstance(b, m.Heading) and b.text == "Notes"
                   for b in exp.build_report_model().blocks)


def test_stats_file_explains_metrics_and_phases(tmp_path):
    # *_Stats.txt must orient the reader: which phases (by name), which test
    # applies when, and what each metric measures — before the numbers.
    exp = _model_exp(tmp_path, exp_type_name="Valence")

    class _StatsArena(_FakeArena):
        def run_pairwise_comparisons_facet(self, metric="FinalPI", cutoffs=None,
                                           remove_partners=False,
                                           equal_var=False):
            print(f"RESULT for {metric}")

    exp.arena = _StatsArena(_twochoice_summary())
    exp.arena.experiment_name = "E"

    text = exp.stats(save=True)
    assert "Statistical comparisons - E" in text
    assert "Valence Experiment" in text
    assert "Acclimation = 0-10 min" in text and "Cooldown = 70+ min" in text
    assert "Welch's t-test" in text and "Tukey HSD" in text
    # Each metric gets a described heading, placed before its results.
    assert "Metric: FinalPI" in text and "preference for region 1" in text
    assert text.index("Metric: FinalPI") < text.index("RESULT for FinalPI")
    assert "Metric: TotalDistancePerMin" in text and "mm/min" in text
    saved = (tmp_path / "analysis" / "E_Stats.txt").read_text(encoding="utf-8")
    assert saved == text


def test_report_is_titled_after_the_project_not_the_recording(tmp_path):
    # The title matches the PDF's filename (the project directory the
    # scientist named), not the DTrack recording name — which stays visible
    # as a cover metadata row.
    model = _model_exp(tmp_path).build_report_model()
    assert model.title == tmp_path.name
    cover = model.blocks[0]
    assert cover.title == tmp_path.name
    assert dict(cover.metadata)["Recording"] == "E"


def test_cover_lists_phases_and_regions(tmp_path):
    exp = _model_exp(tmp_path, exp_type_name="Valence")
    exp.config["tracking_regions"] = {
        "T_0": {"experimental_factors": "control"},
        "T_1": {"experimental_factors": "chr"},
        "T_2": {},
    }
    metadata = dict(exp.build_report_model().blocks[0].metadata)
    assert metadata["Phases (min)"] == \
        "Acclimation 0–10 · Experiment 10–70 · Cooldown 70+"
    assert metadata["Tracking regions"].startswith("3")
    assert "control ×1" in metadata["Tracking regions"]


# ---- Low-Transition Exclusion accounting (ADR-0003) ------------------------

def _excluded_df(rows, reasons=None):
    """An exclusion frame as Experiment builds it: one row per fly, with the
    Reason column ADR-0010 added."""
    df = pd.DataFrame(rows, columns=["Name", "TrackingRegion", "Treatment",
                                     "Transitions"])
    df["Reason"] = reasons if reasons is not None else ["Low transitions"] * len(df)
    n_removed = sum(1 for r in df["Reason"] if str(r).startswith("Removed"))
    df.attrs.update({"min_transitions": 5, "window": (10, 70),
                     "phase_label": "Experiment", "removed_regions": {},
                     "unmatched_regions": [], "n_removed": n_removed,
                     "n_low_transitions": len(df) - n_removed})
    return df


def test_exclusion_section_lists_removed_flies():
    exp = _FakeExp(Parameters.TrackingType.TWOCHOICETRACKER, _twochoice_summary())
    exp.excluded_flies = _excluded_df([
        ["T_1_0", "T_1", "control", 2.0],
        ["T_2_0", "T_2", "chr", float("nan")],
    ])
    blocks = report_figures.build_exclusion_blocks(exp)
    assert len(blocks) == 2
    assert isinstance(blocks[0], m.Paragraph) and "2 fly(ies)" in blocks[0].text
    table = blocks[1]
    assert isinstance(table, m.Table)
    assert [r[0] for r in table.rows] == ["T_1_0", "T_2_0"]
    # NA transitions (no data in the Primary Phase) is shown honestly.
    assert table.rows[1][3] == "no data"
    assert table.rows[0][3] == "2"


def test_exclusion_section_zero_excluded_and_off():
    exp = _FakeExp(Parameters.TrackingType.TWOCHOICETRACKER, _twochoice_summary())
    exp.excluded_flies = _excluded_df([])
    blocks = report_figures.build_exclusion_blocks(exp)
    assert len(blocks) == 1 and "No flies were excluded" in blocks[0].text

    off = _excluded_df([])
    off.attrs["min_transitions"] = 0
    exp.excluded_flies = off
    blocks = report_figures.build_exclusion_blocks(exp)
    assert len(blocks) == 1 and "Low-transition exclusion is off" in blocks[0].text

    # No criterion at all (Custom / attr absent): no block.
    exp2 = _FakeExp(Parameters.TrackingType.TWOCHOICETRACKER, _twochoice_summary())
    assert report_figures.build_exclusion_blocks(exp2) == []


def test_faceted_figures_include_transitions_and_movement():
    from pytrackinganalysis import experiment_types as et

    exp = _FakeExp(Parameters.TrackingType.TWOCHOICETRACKER, _twochoice_summary(),
                   facet_cutoffs=[10, 70])
    exp.experiment_type = et.get_experiment_type("Valence")
    figs = report_figures.build_faceted_figures(exp)
    titles = [f.title for f in figs]
    assert "Preference index by phase" in titles
    assert "Transitions by phase" in titles
    assert "Movement by phase" in titles
    # Percentage figure is labelled by the actual region-1 name when known.
    exp.config = {"global": {},
                  "counting_regions": {"Light": {"alias": "L"},
                                       "NoLight": {"alias": "N"}}}
    figs = report_figures.build_faceted_figures(exp)
    assert "Time in Light by phase" in [f.title for f in figs]


# ---- Low-Movement Flag accounting -------------------------------------------

def _flagged_df(rows, threshold=140.0, n_total=4, experiment_flagged=False):
    df = pd.DataFrame(rows, columns=["Name", "TrackingRegion", "Treatment",
                                     "TotalDistancePerMin"])
    df.attrs.update({"min_movement": threshold, "window": (0, 10),
                     "phase_label": "Acclimation", "n_total": n_total,
                     "fraction": (len(df) / n_total) if n_total else 0.0,
                     "experiment_flagged": experiment_flagged})
    return df


def test_movement_flag_section_lists_flagged_flies():
    exp = _FakeExp(Parameters.TrackingType.TWOCHOICETRACKER, _twochoice_summary())
    exp.flagged_flies = _flagged_df([
        ["T_1_0", "T_1", "control", 96.5],
        ["T_2_0", "T_2", "chr", float("nan")],
    ])
    blocks = report_figures._valence_movement_flags(exp)
    assert len(blocks) == 2
    assert isinstance(blocks[0], m.Paragraph)
    assert "flagged" in blocks[0].text and "remain" in blocks[0].text
    table = blocks[1]
    assert isinstance(table, m.Table)
    assert [r[0] for r in table.rows] == ["T_1_0", "T_2_0"]
    assert table.rows[0][3] == "96.5" and table.rows[1][3] == "no data"
    assert table.row_levels == [m.Level.WARN, m.Level.WARN]


def test_movement_flag_section_experiment_level_warning():
    exp = _FakeExp(Parameters.TrackingType.TWOCHOICETRACKER, _twochoice_summary())
    exp.flagged_flies = _flagged_df(
        [["a", "T_0", "x", 100.0], ["b", "T_1", "x", 90.0],
         ["c", "T_2", "x", 80.0]],
        experiment_flagged=True)
    blocks = report_figures._valence_movement_flags(exp)
    assert "POTENTIAL ISSUE" in blocks[0].text and "75%" in blocks[0].text


def test_movement_flag_section_zero_off_and_absent():
    exp = _FakeExp(Parameters.TrackingType.TWOCHOICETRACKER, _twochoice_summary())
    exp.flagged_flies = _flagged_df([])
    blocks = report_figures._valence_movement_flags(exp)
    assert len(blocks) == 1 and "No flies were flagged" in blocks[0].text

    off = _flagged_df([], threshold=0.0)
    exp.flagged_flies = off
    blocks = report_figures._valence_movement_flags(exp)
    assert len(blocks) == 1 and "flagging is off" in blocks[0].text

    exp2 = _FakeExp(Parameters.TrackingType.TWOCHOICETRACKER, _twochoice_summary())
    assert report_figures._valence_movement_flags(exp2) == []


def test_cover_carries_movement_status_line(tmp_path):
    exp = _model_exp(tmp_path, exp_type_name="Valence")
    exp.flagged_flies = _flagged_df(
        [["a", "T_0", "x", 100.0], ["b", "T_1", "x", 90.0],
         ["c", "T_2", "x", 80.0]],
        experiment_flagged=True)
    model = exp.build_report_model()
    cover = model.blocks[0]
    lines = cover.status_lines()
    movement = [l for l in lines if "Potential issue" in l.text]
    assert movement and movement[0].level == m.Level.ERROR

    # All clear -> an OK line, so the reader knows the check ran.
    exp.flagged_flies = _flagged_df([])
    cover = exp.build_report_model().blocks[0]
    ok = [l for l in cover.status_lines() if "Movement" in l.text]
    assert ok and ok[0].level == m.Level.OK


def test_save_summary_adds_low_movement_flag_column(tmp_path):
    exp = _model_exp(tmp_path, exp_type_name="Valence")
    exp.flagged_flies = _flagged_df([["1", "T_1", "control", 100.0]])
    # _twochoice_summary has no Name column; give the fake arena one.
    summary = _twochoice_summary()
    summary["Name"] = [str(i) for i in range(len(summary))]
    exp.arena._summary = summary
    flat, facet = exp.save_summary()
    assert list(flat["LowMovementFlag"]) == [False, True] + [False] * 6
    # The per-fly flag repeats on each of the fly's facet rows.
    assert facet.groupby("Name")["LowMovementFlag"].nunique().max() == 1
    assert facet[facet["Name"] == "1"]["LowMovementFlag"].all()

    saved = pd.read_csv(tmp_path / "analysis" / "E_Summary.csv")
    assert "LowMovementFlag" in saved.columns

    # Flagging off -> no column (rather than a misleading all-False).
    exp.flagged_flies = _flagged_df([], threshold=0.0)
    flat, _ = exp.save_summary()
    assert "LowMovementFlag" not in flat.columns


# ---- whole-recording page vs faceted figures --------------------------------

def test_flat_figures_dropped_when_faceted_figures_exist(tmp_path):
    # With facet_cutoffs configured the per-phase figures replace the
    # whole-recording ("summed over all phases") Choice / Locomotion page.
    exp = _model_exp(tmp_path, facet_cutoffs=(10, 70))
    titles = [b.title for b in exp.build_report_model().blocks
              if isinstance(b, m.Figure)]
    assert "Choice" not in titles and "Locomotion & activity" not in titles
    assert any("by phase" in (t or "") for t in titles)

    # Without faceting the flat figures are the only content — keep them.
    exp = _model_exp(tmp_path, facet_cutoffs=None)
    titles = [b.title for b in exp.build_report_model().blocks
              if isinstance(b, m.Figure)]
    assert "Choice" in titles
    assert not any("by phase" in (t or "") for t in titles)


def test_transitions_by_phase_plots_rate_with_rate_threshold(monkeypatch):
    # The transitions facet figure plots TransitionsPerMin, and the exclusion
    # criterion line is converted to a rate (min_transitions / phase minutes).
    from pytrackinganalysis import experiment_types as et
    from pytrackinganalysis import report_figures as rf

    lines = []
    real = rf.plt.Axes.axhline

    def _spy(self, y=0, *a, **k):
        if k.get("linestyle") == ":":
            lines.append(y)
        return real(self, y, *a, **k)

    monkeypatch.setattr(rf.plt.Axes, "axhline", _spy)

    exp = _FakeExp(Parameters.TrackingType.TWOCHOICETRACKER, _twochoice_summary(),
                   facet_cutoffs=[10, 70])
    exp.experiment_type = et.get_experiment_type("Valence")
    figs = rf.build_faceted_figures(exp)
    trans = [f for f in figs if f.title == "Transitions by phase"]
    assert trans and "per minute" in trans[0].caption
    # min_transitions=5 over the 60-minute Experiment phase -> 5/60 per min.
    assert lines and abs(lines[0] - 5 / 60) < 1e-9


# ---- per-tracker QC bars & report location ----------------------------------

def test_qc_metric_bars_built_per_tracker():
    from pytrackinganalysis import experiment_types as et

    summary = _twochoice_summary()
    summary["Name"] = [f"T_{i}_0" for i in range(len(summary))]
    dq = pd.DataFrame({"Tracker": [f"T_{i}" for i in range(5)],
                       "HighQuality": [0.95] * 5})
    exp = _FakeExp(Parameters.TrackingType.TWOCHOICETRACKER, summary, dq=dq,
                   facet_cutoffs=[10, 70])
    exp.experiment_type = et.get_experiment_type("Valence")
    exp.config = {"global": {}}
    figs = report_figures.build_qc_figures(exp)
    titles = [f.title for f in figs]
    assert "Data quality" in titles
    assert "Per-tracker transitions — Experiment phase" in titles
    assert "Per-tracker movement — Acclimation phase" in titles
    trans = [f for f in figs if "transitions" in f.title][0]
    assert "min_transitions" in trans.caption  # criterion line explained


def test_qc_metric_bars_skip_without_name_column():
    dq = pd.DataFrame({"Tracker": ["T_0"], "HighQuality": [0.95]})
    exp = _FakeExp(Parameters.TrackingType.TWOCHOICETRACKER,
                   _twochoice_summary(), dq=dq, facet_cutoffs=[10, 70])
    figs = report_figures.build_qc_figures(exp)
    assert [f.title for f in figs] == ["Data quality"]


def test_report_pdf_written_to_project_root(tmp_path, monkeypatch):
    exp = _model_exp(tmp_path, facet_cutoffs=[10, 70])
    out = exp.create_report()
    assert os.path.dirname(out) == str(tmp_path)
    assert os.path.basename(out) == f"{os.path.basename(tmp_path)}_report.pdf"
    assert os.path.exists(out)


def test_qc_bars_use_pi_gradient():
    # Endpoint colors of the PI gradient: −1 = palette red, +1 = palette
    # green, NaN = grey; and the figures explain the coloring.
    from pytrackinganalysis import experiment_types as et

    assert report_figures._pi_color(-1.0) == "#dc2626"
    assert report_figures._pi_color(1.0) == "#16a34a"
    assert report_figures._pi_color(float("nan")) == "#94a3b8"
    # Clamped outside the PI range.
    assert report_figures._pi_color(2.0) == "#16a34a"

    summary = _twochoice_summary()
    summary["Name"] = [f"T_{i}_0" for i in range(len(summary))]
    dq = pd.DataFrame({"Tracker": [f"T_{i}_0" for i in range(len(summary))],
                       "HighQuality": [0.95] * len(summary)})
    exp = _FakeExp(Parameters.TrackingType.TWOCHOICETRACKER, summary, dq=dq,
                   facet_cutoffs=[10, 70])
    exp.experiment_type = et.get_experiment_type("Valence")
    exp.config = {"global": {}}
    figs = report_figures.build_qc_figures(exp)
    assert len(figs) == 3
    for fig in figs:
        assert "preference index" in fig.caption
        assert "red −1 … green +1" in fig.caption


def test_report_transitions_and_movement_use_free_y(monkeypatch):
    # The by-phase transitions and movement figures give each phase its own
    # y axis; PI and percentage keep a shared, fixed scale.
    from pytrackinganalysis import report_figures as rf

    calls = []
    real = rf.plt.subplots

    def _spy(*args, **kwargs):
        if "sharey" in kwargs:
            calls.append(kwargs["sharey"])
        return real(*args, **kwargs)

    monkeypatch.setattr(rf.plt, "subplots", _spy)
    exp = _FakeExp(Parameters.TrackingType.TWOCHOICETRACKER, _twochoice_summary(),
                   facet_cutoffs=[10, 70])
    figs = rf.build_faceted_figures(exp)
    titles = [f.title for f in figs]
    by_title = dict(zip(titles, calls))
    assert by_title["Preference index by phase"] is True
    assert by_title["Time in region 1 by phase"] is True
    assert by_title["Transitions by phase"] is False
    assert by_title["Movement by phase"] is False
