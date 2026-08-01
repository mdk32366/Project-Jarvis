"""The scaffold template and renderer — TDD #3 §4.4, §9 "scaffold completeness".

Step 5 produces the file SET; it commits nothing. These tests are entirely
offline, which is the point of splitting rendering from committing: the
structure is proven before a byte reaches a real repository.
"""

from datetime import date
from pathlib import Path

import pytest

from app.scaffold import (ScaffoldError, ScaffoldFile, render_scaffold,
                          template_placeholders)

FIXED = date(2026, 8, 1)

EXPECTED_PATHS = {
    "README.md",
    "ARCHITECTURE.md",
    ".gitignore",
    "docs/README.md",
    "docs/archive/.gitkeep",
    "docs/operational/.gitkeep",
}


def _by_path(files: list[ScaffoldFile]) -> dict[str, str]:
    return {f.path: f.content for f in files}


# ── 1. Completeness (§4.4) ───────────────────────────────────────────────────
def test_all_scaffold_files_are_present():
    files = _by_path(render_scaffold("Foo", "does a thing", now=FIXED))
    assert set(files) == EXPECTED_PATHS


def test_gitignore_is_renamed_out_of_the_template_name():
    """The template stores it as `gitignore.template` so it isn't a live
    gitignore for its own directory; the created repo must get the real name."""
    files = _by_path(render_scaffold("Foo", now=FIXED))
    assert ".gitignore" in files
    assert "gitignore.template" not in files
    assert "__pycache__/" in files[".gitignore"]


def test_gitkeep_files_are_emitted_even_though_empty():
    files = _by_path(render_scaffold("Foo", now=FIXED))
    assert files["docs/archive/.gitkeep"] == ""
    assert files["docs/operational/.gitkeep"] == ""


# ── 2. THE ANTI-DRIFT GUARANTEE (§4.4) ───────────────────────────────────────
def test_docs_readme_carries_the_convention_verbatim():
    """`docs/README.md` is the load-bearing file: it carries the tier convention
    into every new repo. It has NO placeholders, so it must render
    byte-for-byte identical to the tracked template. This is §4.4's "stored, not
    regenerated from memory" expressed as an assertion."""
    template = (Path(__file__).parent.parent / "app" / "scaffold" / "template"
                / "docs" / "README.md").read_text(encoding="utf-8")
    rendered = _by_path(render_scaffold("Foo", "x", now=FIXED))["docs/README.md"]

    assert rendered == template, "docs/README.md must render verbatim"

    # And the convention itself is actually in there — a byte-identical render
    # of the WRONG text would otherwise pass the assertion above.
    for phrase in ("live", "superseded", "spent",
                   "docs/archive/", "docs/operational/",
                   "Commit the design before the work is done."):
        assert phrase in rendered, f"the convention lost: {phrase!r}"


def test_the_template_is_read_from_disk_not_an_inline_string(tmp_path, monkeypatch):
    """Editing the template must change the output. If the structure were carried
    inline, this test could not move it."""
    import app.scaffold as scaffold

    fake = tmp_path / "template"
    (fake / "docs").mkdir(parents=True)
    (fake / "README.md").write_text("# {{PROJECT_NAME}}\nfrom disk\n", encoding="utf-8")
    (fake / "docs" / "README.md").write_text("tiers\n", encoding="utf-8")
    monkeypatch.setattr(scaffold, "_TEMPLATE_DIR", fake)

    # Completeness is enforced, so a stub template must still carry the full set
    # for the render to succeed — that guard is the subject of the next test.
    (fake / "ARCHITECTURE.md").write_text("arch\n", encoding="utf-8")
    (fake / "gitignore.template").write_text("*.pyc\n", encoding="utf-8")
    (fake / "docs" / "archive").mkdir()
    (fake / "docs" / "operational").mkdir()
    (fake / "docs" / "archive" / ".gitkeep").write_text("", encoding="utf-8")
    (fake / "docs" / "operational" / ".gitkeep").write_text("", encoding="utf-8")

    files = _by_path(scaffold.render_scaffold("Foo", now=FIXED))
    assert set(files) == EXPECTED_PATHS
    assert "from disk" in files["README.md"]


def test_an_incomplete_template_refuses_to_render(tmp_path, monkeypatch):
    """The realistic failure is not a corrupt template — it is one that is partly
    ABSENT AT RUNTIME. `.dockerignore` excludes `*.md`, and three of the six
    scaffold files are markdown including the convention file, so a build that
    dropped them would seed repos with no README while every offline test passed.

    Refusing beats emitting a partial scaffold: a repo created without
    `docs/README.md` is a repo whose convention was never carried, which is the
    exact drift §4.4 exists to prevent.
    """
    import app.scaffold as scaffold

    fake = tmp_path / "template"
    fake.mkdir()
    (fake / "gitignore.template").write_text("*.pyc\n", encoding="utf-8")   # the .md files are "missing"
    monkeypatch.setattr(scaffold, "_TEMPLATE_DIR", fake)

    with pytest.raises(ScaffoldError) as e:
        scaffold.render_scaffold("Foo", now=FIXED)
    msg = str(e.value)
    assert "incomplete" in msg and "docs/README.md" in msg
    assert "dockerignore" in msg.lower(), "the message must name the likely cause"


# ── 3. Placeholders (§9) ─────────────────────────────────────────────────────
def test_no_placeholder_survives_rendering():
    for content in _by_path(render_scaffold("Foo", "a description", now=FIXED)).values():
        assert "{{" not in content and "}}" not in content


def test_project_name_and_description_actually_land():
    files = _by_path(render_scaffold("Node Narrator", "Reads node status aloud.", now=FIXED))
    assert "# Node Narrator" in files["README.md"]
    assert "Reads node status aloud." in files["README.md"]
    assert "Node Narrator" in files["ARCHITECTURE.md"]
    assert "2026-08-01" in files["README.md"]


def test_missing_description_gets_a_placeholder_not_an_empty_hole():
    files = _by_path(render_scaffold("Foo", now=FIXED))
    assert "_No description yet._" in files["README.md"]


def test_unknown_placeholder_fails_loudly(tmp_path, monkeypatch):
    """A silent unrendered token in a real repo is exactly the drift this step
    exists to prevent, so the renderer raises rather than emitting it."""
    import app.scaffold as scaffold

    fake = tmp_path / "template"
    fake.mkdir()
    (fake / "README.md").write_text("# {{PROJECT_NAME}} by {{OWNER}}\n", encoding="utf-8")
    monkeypatch.setattr(scaffold, "_TEMPLATE_DIR", fake)

    with pytest.raises(ScaffoldError) as e:
        scaffold.render_scaffold("Foo", now=FIXED)
    assert "OWNER" in str(e.value)


def test_placeholder_key_set_is_derived_from_the_template():
    """Derived, not hardcoded — so adding a token to a template file and
    forgetting to supply it is caught by the check above rather than shipped."""
    keys = template_placeholders()
    assert keys <= {"PROJECT_NAME", "DESCRIPTION", "DATE"}
    assert "PROJECT_NAME" in keys


def test_empty_project_name_is_refused():
    with pytest.raises(ScaffoldError):
        render_scaffold("   ", now=FIXED)


# ── 4. Determinism (§9) ──────────────────────────────────────────────────────
def test_render_is_deterministic():
    a = render_scaffold("Foo", "same", now=FIXED)
    b = render_scaffold("Foo", "same", now=FIXED)
    assert a == b
    assert [f.path for f in a] == sorted(f.path for f in a), "output is path-ordered"


def test_the_only_clock_is_the_injected_one():
    """No embedded wall-clock: a different `now` is the only thing that moves the
    date, and nothing else in the output changes with it."""
    a = _by_path(render_scaffold("Foo", "x", now=date(2026, 1, 2)))
    b = _by_path(render_scaffold("Foo", "x", now=date(2027, 3, 4)))
    assert "2026-01-02" in a["README.md"]
    assert "2027-03-04" in b["README.md"]
    assert a["docs/README.md"] == b["docs/README.md"]


# ── 5. Scope: step 5 renders, it does not commit ─────────────────────────────
def test_scaffold_module_has_no_github_client():
    """Step 5 is pure. If this module ever grows an HTTP client, the split that
    makes the structure provable offline has been lost."""
    src = (Path(__file__).parent.parent / "app" / "scaffold" / "__init__.py").read_text(
        encoding="utf-8")
    for forbidden in ("httpx", "requests", "api.github.com", "urllib"):
        assert forbidden not in src, f"{forbidden} appeared in a pure renderer"
