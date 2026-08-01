"""The new-project scaffold — a stored, versioned template and a pure renderer.

TDD #3 §4.4. The template is **files in this repository**, not a string in this
module, and that is the entire point:

> A structure regenerated from memory each time will drift, and drift in the
> thing whose entire job is preventing drift is a special kind of failure.

Because the template is tracked, a change to the scaffold is a diff in a pull
request — reviewable, attributable, revertible — rather than a silent change in
a code literal that nobody diffs.

WHY RENDERING IS SEPARATE FROM COMMITTING. Step 6 (`create_project_repo`) is
gated, outward-facing, and already carries the irreversible risk of creating a
named repository. Keeping *rendering* pure — read template files, substitute,
return text — means the structure is fully proven offline before a byte reaches
a real repo. It is the same split as `secretscan` (detect) versus
`commit_document` (enforce): the risky half calls a proven pure half.

WHY `gitignore.template` AND NOT `.gitignore`. A literal `.gitignore` inside the
template directory would be a *live* gitignore for that subtree — git would
apply its rules to the template itself, which is both surprising and a way to
silently mask a template file from being tracked. The renderer maps the name on
the way out, so the created repo gets a real `.gitignore` and this repo does not
get a phantom one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

_TEMPLATE_DIR = Path(__file__).parent / "template"

# Files whose name in the template differs from their name in a created repo.
_RENAMES = {"gitignore.template": ".gitignore"}

_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z_]+)\}\}")

# The scaffold is only a scaffold if it is COMPLETE. Rendering is checked against
# this set rather than trusting whatever files happen to be on disk, because the
# realistic failure is not a corrupt template — it is a template that is *partly
# absent at runtime*.
#
# That is not hypothetical. `.dockerignore` excludes `*.md`, and three of these
# six files are markdown, including the one carrying the tier convention. A build
# that dropped them would produce new repos with no README and no `docs/README.md`
# while every offline test still passed, because tests read the source tree and
# production reads the image. The `.dockerignore` negation is the fix; this is the
# guard that makes a regression of it loud instead of silent.
_REQUIRED_PATHS = frozenset({
    "README.md",
    "ARCHITECTURE.md",
    ".gitignore",
    "docs/README.md",
    "docs/archive/.gitkeep",
    "docs/operational/.gitkeep",
})


class ScaffoldError(Exception):
    """The template asked for something the renderer cannot supply.

    Raised rather than emitted: an unrendered `{{TOKEN}}` sitting in a real
    repository is precisely the drift this step exists to prevent, and a silent
    passthrough would put it there.
    """


@dataclass(frozen=True)
class ScaffoldFile:
    """One file in a new repo: repo-relative path and its full text."""

    path: str
    content: str


def _template_files() -> list[Path]:
    return sorted(p for p in _TEMPLATE_DIR.rglob("*") if p.is_file())


def _out_path(p: Path) -> str:
    rel = p.relative_to(_TEMPLATE_DIR).as_posix()
    parts = rel.rsplit("/", 1)
    name = _RENAMES.get(parts[-1], parts[-1])
    return f"{parts[0]}/{name}" if len(parts) == 2 else name


def template_placeholders() -> set[str]:
    """Every placeholder key the template actually uses.

    Derived from the template rather than hardcoded, so adding `{{OWNER}}` to a
    template file and forgetting to supply it fails loudly instead of shipping
    the literal token.
    """
    found: set[str] = set()
    for p in _template_files():
        found |= set(_PLACEHOLDER_RE.findall(p.read_text(encoding="utf-8")))
    return found


def render_scaffold(project_name: str, description: str = "",
                    now: date | None = None) -> list[ScaffoldFile]:
    """Render the versioned template into the file set a new repo is seeded with.

    Pure: reads the template files and nothing else. No network, no repo, no DB.
    Deterministic for fixed inputs — the only clock is the injectable `now`.
    """
    name = (project_name or "").strip()
    if not name:
        raise ScaffoldError("a scaffold needs a project name")

    values = {
        "PROJECT_NAME": name,
        "DESCRIPTION": (description or "").strip() or "_No description yet._",
        "DATE": (now or date.today()).isoformat(),
    }

    unknown = template_placeholders() - set(values)
    if unknown:
        raise ScaffoldError(
            f"template uses placeholders the renderer cannot supply: "
            f"{', '.join(sorted(unknown))}"
        )

    out: list[ScaffoldFile] = []
    for p in _template_files():
        text = p.read_text(encoding="utf-8")
        rendered = _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], text)
        # Belt and braces: the substitution above cannot leave a known token
        # behind, and `unknown` above catches the rest — so anything surviving
        # here is a bug in this function, not in the template.
        if _PLACEHOLDER_RE.search(rendered):
            raise ScaffoldError(f"unrendered placeholder survived in {_out_path(p)}")
        out.append(ScaffoldFile(path=_out_path(p), content=rendered))

    missing = _REQUIRED_PATHS - {f.path for f in out}
    if missing:
        raise ScaffoldError(
            f"the scaffold template is incomplete at runtime — missing "
            f"{', '.join(sorted(missing))}. The template files are not reaching "
            f"this process (check .dockerignore); refusing to seed a repo with a "
            f"partial scaffold."
        )

    return sorted(out, key=lambda f: f.path)
