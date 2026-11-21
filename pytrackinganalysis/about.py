# Mirrored from pyflic: pyflic/base/about.py at commit f6fb479
# ("about: keep a catalogue's keys, and start from an earlier recording").
#
# pyflic is the origin of this module, and this file is the verbatim copy its
# docstring mentions — so "here" in "change it here first" means pyflic.
# Change the module there, then copy it over this one whole and update this
# note and tests/test_about_mirror.py, which pins the copy to that commit.
# The copy differs from the origin only in this note and in the word "pyflic"
# before the numbers of pyflic's ADRs.
#
# Read its vocabulary as this package's: flic_config.yaml is
# tracking_config.yaml, a Member is a replicate, and a Project Design is the
# Project's design: section.  What this package does with the section is
# docs/adr/0019.
"""``about:`` — what a recording is, said once in its own ``flic_config.yaml``.

The section describes the recording for a person or a catalogue reading the
config on its own: a title, why it was run, who ran it, the flies, the
protocol, and what each design factor and level means (pyflic ADR-0024).

Every key is optional, and so is the section.  pyflic never reads it for an
analysis: no number, cache key or "re-run needed" verdict depends on it, and a
config without it behaves exactly as it always has.  It belongs to the
recording, like ``data_folder:`` — never part of a Project Design, never
inherited, never compared across Members for errors.  A Project only warns
when two Members describe the same factor level differently, because pooling
treats same-named levels as one group.

The shape::

    about:
      title: Progressive ratio, sucrose, replicate 1
      purpose: Whether silencing MBON10 changes how hard flies work for sucrose.
      experimenter: scott
      series: pr-sucrose-2026
      organism: Drosophila melanogaster
      sex: [female]
      age_at_start: 5-7 days
      protocol: {name: FLIC progressive ratio, version: "2", link: https://...}
      notes: Free text.
      factors:
        Genotype:
          kind: genotype
          levels:
            MBON10: R12C11-GAL4 > UAS-GtACR1
            Ctrl: w1118 > UAS-GtACR1

A level's meaning is a line of text; a mapping with a ``definition:`` key is
read the same way, so a later field beside it does not change the shape.

**Another program may write here** (pyflic ADR-0026).  A catalogue that files the
recording rewrites the section to match its own record: it adds ``run:`` (the
recording's id there) and, inside a level's mapping, ``term:``, ``label:`` and
``role:``.  pyflic shows ``run:``, keeps every key it does not edit, and edits
only a level's ``definition`` (:func:`with_meaning`).  A new recording starts
from an earlier one's shared part (:func:`shared_about`, :func:`start_from`),
which never includes ``run:``, the title, the purpose or the notes.

PyTrackingAnalysis keeps a verbatim copy of this module: change it here first.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

ABOUT_KEY = "about"
#: The recording's id in the catalogue that filed it.  Written by the
#: catalogue, shown here, never typed and never copied to another recording.
RUN_KEY = "run"

#: One-line or free-text fields, in the order the section is written.
TEXT_FIELDS: tuple[str, ...] = (
    "title", "purpose", "experimenter", "series", "organism", "age_at_start", "notes",
)
SEX_KEY = "sex"
#: What the editor offers for ``sex:``.  Any other text a file holds is kept.
SEX_CHOICES: tuple[str, ...] = ("female", "male")
PROTOCOL_KEY = "protocol"
PROTOCOL_FIELDS: tuple[str, ...] = ("name", "version", "link")
FACTORS_KEY = "factors"
#: What kind of variable a design factor is.
FACTOR_KINDS: tuple[str, ...] = ("genotype", "treatment", "diet", "condition", "sex", "other")

#: In a level's mapping: the person's words, and the registry term a catalogue
#: gave the level.  Any other key there is kept as it is.
DEFINITION_KEY = "definition"
TERM_KEY = "term"

#: The order ``about:`` is written in; a key not listed follows, as written.
KEY_ORDER: tuple[str, ...] = (
    RUN_KEY, "title", "purpose", "experimenter", "series", "organism", SEX_KEY,
    "age_at_start", PROTOCOL_KEY, "notes", FACTORS_KEY,
)

#: The part of ``about:`` that replicate recordings share.  The title, the
#: purpose, the notes and ``run:`` are one recording's alone.
SHARED_KEYS: tuple[str, ...] = (
    "experimenter", "series", "organism", SEX_KEY, "age_at_start", PROTOCOL_KEY,
    FACTORS_KEY,
)

#: A title longer than this no longer fits on one line of a list of runs.
TITLE_LIMIT = 60


def read_about(config: Any) -> dict:
    """The config's ``about:`` section, or ``{}`` when it has none or it is
    not a mapping (the linter says so; nothing else depends on it)."""
    if not isinstance(config, Mapping):
        return {}
    section = config.get(ABOUT_KEY)
    return dict(section) if isinstance(section, Mapping) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value).strip()


def sex_values(value: Any) -> list[str]:
    """``sex:`` as a list: one word or a list of them, blanks left out."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    return [text for text in (_text(item) for item in items) if text]


def level_meaning(entry: Any) -> str:
    """A level's meaning: its text, or a mapping's ``definition:``."""
    if isinstance(entry, Mapping):
        return _text(entry.get(DEFINITION_KEY))
    return _text(entry)


def level_term(entry: Any) -> str:
    """The registry term a catalogue gave a level, or ``""``: only a
    mapping holds one."""
    return _text(entry.get(TERM_KEY)) if isinstance(entry, Mapping) else ""


def with_meaning(entry: Any, text: Any) -> Any:
    """*entry* -- a level's text, mapping or nothing -- with its meaning set
    to *text*.

    Text stays text.  A mapping keeps every other key it holds and takes
    *text* as its ``definition:``, first; a blank *text* removes the
    definition and keeps the rest.  ``None`` when nothing is left to write.
    *entry* itself is not changed.
    """
    text = _text(text)
    if isinstance(entry, Mapping):
        rest = {key: copy.deepcopy(value) for key, value in entry.items()
                if key != DEFINITION_KEY and value not in (None, "")}
        if rest:
            return {**({DEFINITION_KEY: text} if text else {}), **rest}
    return text or None


def factor_notes(about: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """``about.factors`` as ``{factor: {"kind": str, "levels": {level: str}}}``,
    with anything malformed left out."""
    raw = about.get(FACTORS_KEY) if isinstance(about, Mapping) else None
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for name, body in raw.items():
        if not isinstance(body, Mapping):
            continue
        levels_raw = body.get("levels")
        levels = ({str(level).strip(): level_meaning(entry)
                   for level, entry in levels_raw.items()}
                  if isinstance(levels_raw, Mapping) else {})
        out[str(name).strip()] = {"kind": _text(body.get("kind")).lower(),
                                  "levels": levels}
    return out


def clean_about(about: Mapping[str, Any]) -> dict:
    """*about* as it is written: known keys in order, text stripped, blanks
    and empty sub-sections left out, and any key this module does not know
    kept as it was.  An all-blank section is ``{}`` — the caller then writes
    no ``about:`` at all."""
    out: dict[str, Any] = {}
    for key in KEY_ORDER:
        if key not in about:
            continue
        value = about[key]
        if key in TEXT_FIELDS or key == RUN_KEY:
            text = _text(value)
            if text:
                out[key] = text
        elif key == SEX_KEY:
            values = sex_values(value)
            if values:
                out[key] = values
        elif key == PROTOCOL_KEY:
            if isinstance(value, Mapping):
                protocol = {f: _text(value.get(f)) for f in PROTOCOL_FIELDS}
                protocol = {f: v for f, v in protocol.items() if v}
                protocol.update({k: v for k, v in value.items()
                                 if k not in PROTOCOL_FIELDS and v not in (None, "")})
                if protocol:
                    out[key] = protocol
            elif _text(value):
                out[key] = {"name": _text(value)}
        elif key == FACTORS_KEY:
            factors = _clean_factors(value)
            if factors:
                out[key] = factors
    for key, value in about.items():
        if key not in KEY_ORDER and value not in (None, "", [], {}):
            out[key] = value
    return out


def _clean_factors(value: Any) -> dict:
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, Any] = {}
    for name, body in value.items():
        if not isinstance(body, Mapping):
            continue
        entry: dict[str, Any] = {}
        kind = _text(body.get("kind")).lower()
        if kind:
            entry["kind"] = kind
        levels = body.get("levels")
        if isinstance(levels, Mapping):
            kept = {}
            for level, meaning in levels.items():
                if isinstance(meaning, Mapping):
                    if any(v not in (None, "") for v in meaning.values()):
                        kept[str(level)] = dict(meaning)
                elif _text(meaning):
                    kept[str(level)] = _text(meaning)
            if kept:
                entry["levels"] = kept
        entry.update({k: v for k, v in body.items()
                      if k not in ("kind", "levels") and v not in (None, "")})
        if entry:
            out[str(name)] = entry
    return out


def about_problems(about: Any, factors: Mapping[str, Any] | None = None
                   ) -> list[tuple[str, str]]:
    """What is odd about an ``about:`` section, as ``(path, message)`` pairs.

    Warnings only: nothing here stops a load, since pyflic does not read the
    section.  *factors* is the config's ``experimental_design_factors`` (the
    Design's, for a Member), against which ``about.factors`` is checked.
    """
    problems: list[tuple[str, str]] = []
    if about is None:
        return problems
    if not isinstance(about, Mapping):
        return [(ABOUT_KEY, "about: must be a mapping of fields, such as "
                            "title: and purpose:")]
    for key in (RUN_KEY, *TEXT_FIELDS):
        value = about.get(key)
        if isinstance(value, (Mapping, list, tuple)):
            problems.append((f"{ABOUT_KEY}.{key}", f"about.{key} should be text"))
    title = about.get("title")
    if isinstance(title, str):
        if "\n" in title.strip():
            problems.append((f"{ABOUT_KEY}.title", "about.title should be one line"))
        if len(title.strip()) > TITLE_LIMIT:
            problems.append((f"{ABOUT_KEY}.title",
                             f"about.title is {len(title.strip())} characters; keep it "
                             f"to {TITLE_LIMIT} so it fits on one line in a list"))
    sex = about.get(SEX_KEY)
    if sex is not None and not isinstance(sex, (str, list, tuple)):
        problems.append((f"{ABOUT_KEY}.{SEX_KEY}",
                         "about.sex should be a list such as [female, male]"))
    protocol = about.get(PROTOCOL_KEY)
    if protocol is not None and not isinstance(protocol, (Mapping, str)):
        problems.append((f"{ABOUT_KEY}.{PROTOCOL_KEY}",
                         "about.protocol should be a mapping of name, version and link"))
    raw = about.get(FACTORS_KEY)
    if raw is None:
        return problems
    if not isinstance(raw, Mapping):
        problems.append((f"{ABOUT_KEY}.{FACTORS_KEY}",
                         "about.factors should map each factor to its kind and levels"))
        return problems
    declared = {str(k).strip(): [str(v).strip() for v in (vals or [])]
                for k, vals in (factors or {}).items()} if isinstance(factors, Mapping) else {}
    for name, body in raw.items():
        where = f"{ABOUT_KEY}.{FACTORS_KEY}.{name}"
        if not isinstance(body, Mapping):
            problems.append((where, f"about.factors.{name} should be a mapping "
                                    f"with kind: and levels:"))
            continue
        kind = _text(body.get("kind")).lower()
        if kind and kind not in FACTOR_KINDS:
            problems.append((f"{where}.kind",
                             f"about.factors.{name}.kind is {kind!r}; expected one of "
                             f"{', '.join(FACTOR_KINDS)}"))
        if str(name).strip() not in declared:
            problems.append((where, f"about.factors names {name!r}, which "
                                    f"experimental_design_factors does not declare"))
            continue
        levels = body.get("levels")
        if levels is None:
            continue
        if not isinstance(levels, Mapping):
            problems.append((f"{where}.levels",
                             f"about.factors.{name}.levels should map each level to "
                             f"what it means"))
            continue
        for level in levels:
            if str(level).strip() not in declared[str(name).strip()]:
                problems.append((f"{where}.levels.{level}",
                                 f"about.factors.{name} describes level {level!r}, "
                                 f"which {name} does not declare"))
    return problems


def shared_about(about: Any, factors: Mapping[str, Any] | None = None) -> dict:
    """The part of *about* a replicate recording shares (:data:`SHARED_KEYS`),
    as a copy: who ran it, the series, the flies, the protocol, and each
    factor's kind and each level's whole entry -- a mapping's ``term:`` and
    the rest included, so a name a catalogue standardised once travels to the
    next recording.

    With *factors* (an ``experimental_design_factors`` mapping), only the
    factors and levels it declares are kept: a description of a level this
    recording does not have would only draw a warning.
    """
    about = about if isinstance(about, Mapping) else {}
    shared = clean_about({key: copy.deepcopy(about[key]) for key in SHARED_KEYS
                          if key in about})
    if factors is None or FACTORS_KEY not in shared:
        return shared
    declared = {str(name).strip(): {str(level).strip() for level in (levels or [])}
                for name, levels in factors.items()} if isinstance(factors, Mapping) else {}
    kept: dict[str, Any] = {}
    for name, body in shared[FACTORS_KEY].items():
        if str(name).strip() not in declared:
            continue
        body = dict(body)
        levels = body.get("levels")
        if isinstance(levels, Mapping):
            levels = {level: entry for level, entry in levels.items()
                      if str(level).strip() in declared[str(name).strip()]}
            if levels:
                body["levels"] = levels
            else:
                body.pop("levels")
        if body:
            kept[name] = body
    if kept:
        shared[FACTORS_KEY] = kept
    else:
        shared.pop(FACTORS_KEY)
    return shared


def start_from(about: Any, earlier: Any, factors: Mapping[str, Any] | None = None) -> dict:
    """*about* started from an *earlier* recording's: every shared field
    *earlier* states (:func:`shared_about`) replaces this one's, and nothing
    else changes -- the title, the purpose, the notes, ``run:`` and any key
    this module does not know stay this recording's own.  A shared field
    *earlier* does not state is left as it is.  Neither argument is changed.
    """
    about = about if isinstance(about, Mapping) else {}
    return clean_about({**copy.deepcopy(dict(about)), **shared_about(earlier, factors)})


def meaning_spread(abouts: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Where Members describe one factor or level differently, one line each.

    *abouts* is each Member's ``about:`` by Member name.  A Member that says
    nothing about a factor or level is not a difference; two that say
    different things are, because the Combined Analysis pools levels by name.

    Where every Member that describes a level cites a registry ``term:``, the
    terms are compared and the wording is free: one term is one level however
    it is put.  Otherwise the words are compared, among the Members that give
    any.
    """
    kinds: dict[str, dict[str, list[str]]] = {}
    said: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for member, about in abouts.items():
        raw = about.get(FACTORS_KEY) if isinstance(about, Mapping) else None
        if not isinstance(raw, Mapping):
            continue
        for name, body in raw.items():
            if not isinstance(body, Mapping):
                continue
            factor = str(name).strip()
            kind = _text(body.get("kind")).lower()
            if kind:
                kinds.setdefault(factor, {}).setdefault(kind, []).append(member)
            levels = body.get("levels")
            if not isinstance(levels, Mapping):
                continue
            for level, entry in levels.items():
                meaning, term = level_meaning(entry), level_term(entry)
                if meaning or term:
                    said.setdefault((factor, str(level).strip()), []).append(
                        (member, meaning, term))
    lines: list[str] = []
    for factor, seen in kinds.items():
        if len(seen) > 1:
            detail = "; ".join(f"{kind}: {', '.join(names)}" for kind, names in seen.items())
            lines.append(f"about: members give factor {factor} different kinds ({detail})")
    for (factor, level), entries in said.items():
        by_term = all(term for _member, _meaning, term in entries)
        seen = {}
        for member, meaning, term in entries:
            key = term if by_term else meaning
            if key:
                seen.setdefault(key, []).append(member)
        if len(seen) > 1:
            detail = "; ".join(f"{text!r}: {', '.join(names)}" for text, names in seen.items())
            lines.append(f"about: members describe {factor} {level!r} differently "
                         f"({detail}); the pooled results treat them as one level")
    return lines
