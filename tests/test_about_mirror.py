"""``pytrackinganalysis/about.py`` is pyflic's module, copied whole (ADR-0019).

pyflic is the origin. The copy may differ from it in two ways only: the note
above its docstring, which names the commit it was copied at, and the word
"pyflic" in front of the numbers of pyflic's ADRs. These tests hold the copy to
that without pyflic being installed: the origin's names and the constants the
shared contract fixes are spelled out here, and the text is pinned by the
digest of the origin file at that commit. Its behaviour is tested by
``test_about.py``, copied the same way.

When the origin changes, copy the file again, then update the commit in its
note and ``ORIGIN_COMMIT``, ``ORIGIN_SHA256`` and the names below.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from pytrackinganalysis import about

#: The pyflic commit ``pyflic/base/about.py`` was copied at, and the SHA-256
#: of that file there.
ORIGIN_COMMIT = "f6fb479"
ORIGIN_SHA256 = "a7a5484e1b5f0ff1b48b1e9e4a6c61af7c399c3e600c905af0b330f9f300ba5c"

ORIGIN_FUNCTIONS = [
    "read_about", "_text", "sex_values", "level_meaning", "level_term",
    "with_meaning", "factor_notes", "clean_about", "_clean_factors",
    "about_problems", "shared_about", "start_from", "meaning_spread",
]
ORIGIN_CONSTANTS = [
    "ABOUT_KEY", "RUN_KEY", "TEXT_FIELDS", "SEX_KEY", "SEX_CHOICES",
    "PROTOCOL_KEY", "PROTOCOL_FIELDS", "FACTORS_KEY", "FACTOR_KINDS",
    "DEFINITION_KEY", "TERM_KEY", "KEY_ORDER", "SHARED_KEYS", "TITLE_LIMIT",
]


def _source() -> str:
    return Path(about.__file__).read_text(encoding="utf-8")


def _note_and_body() -> tuple[str, str]:
    """The note above the module docstring, and everything from the docstring on."""
    note, quotes, rest = _source().partition('"""')
    return note, quotes + rest


def test_the_note_names_the_origin_and_the_commit():
    note, _body = _note_and_body()
    assert note.strip(), "the copy must say where it came from, above its docstring"
    assert all(line.startswith("#") for line in note.splitlines())
    assert "pyflic/base/about.py" in note
    assert ORIGIN_COMMIT in note


def test_the_copy_is_the_origin_apart_from_its_note_and_adr_names():
    """Edit pyflic first, then copy: a change made only here fails this."""
    _note, body = _note_and_body()
    as_in_origin = body.replace("pyflic ADR-", "ADR-")
    assert hashlib.sha256(as_in_origin.encode("utf-8")).hexdigest() == ORIGIN_SHA256


def test_the_functions_and_constants_are_the_origins():
    tree = ast.parse(_source())
    functions = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
    constants = [target.id for node in tree.body if isinstance(node, ast.Assign)
                 for target in node.targets if isinstance(target, ast.Name)]
    constants += [node.target.id for node in tree.body
                  if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)]
    assert functions == ORIGIN_FUNCTIONS
    assert sorted(constants) == sorted(ORIGIN_CONSTANTS)
    assert not [node for node in tree.body if isinstance(node, ast.ClassDef)]


def test_the_constants_are_the_shared_contracts():
    assert about.ABOUT_KEY == "about"
    assert about.RUN_KEY == "run"
    assert about.KEY_ORDER == (
        "run", "title", "purpose", "experimenter", "series", "organism", "sex",
        "age_at_start", "protocol", "notes", "factors")
    assert about.SHARED_KEYS == (
        "experimenter", "series", "organism", "sex", "age_at_start", "protocol",
        "factors")
    assert about.FACTOR_KINDS == (
        "genotype", "treatment", "diet", "condition", "sex", "other")
    assert about.PROTOCOL_FIELDS == ("name", "version", "link")
    assert about.TITLE_LIMIT == 60
    ## What one recording keeps to itself is never part of what replicates share.
    assert not {"run", "title", "purpose", "notes"} & set(about.SHARED_KEYS)


def test_it_imports_only_the_standard_library():
    """The section is never read for an analysis, and the module that knows
    it reads nothing of this package's: no Qt, no loader, no catalogue."""
    imported = set()
    for node in ast.walk(ast.parse(_source())):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative imports: the module stands alone"
            imported.add(node.module)
    assert imported == {"__future__", "copy", "collections.abc", "typing"}
