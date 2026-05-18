import re
from pathlib import Path

import frontmatter

from .models import Plan, Subgoal

# Matches "### Subgoal A: Title" or "### Subgoal A — Title"
_SUBGOAL_RE = re.compile(r"^###\s+Subgoal\s+([A-Z])[:\s—\-]+(.+)$", re.MULTILINE)

# Matches a child plan reference line: "This subgoal is now tracked in [name](./name.md)."
_CHILD_REF_RE = re.compile(r"tracked in \[.*?\]\(\./([^)]+)\.md\)")


def _split_sections(body: str) -> dict[str, str]:
    """Split markdown body on ## headings into {section_name: content}, preserving original heading case."""
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []

    for line in body.splitlines():
        heading = re.match(r"^##\s+(.+)$", line)
        if heading:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = heading.group(1).strip()
            buf = []
        elif current is not None:
            buf.append(line)

    if current is not None:
        sections[current] = "\n".join(buf).strip()

    return sections


def _parse_subgoals(sections: dict[str, str]) -> list[Subgoal]:
    """Extract subgoals from the top-level subgoals section if present."""
    raw = next((v for k, v in sections.items() if k.lower() == "top-level subgoals"), "")
    if not raw:
        return []

    subgoals: list[Subgoal] = []
    chunks = re.split(r"(?=^###\s+Subgoal\s+[A-Z])", raw, flags=re.MULTILINE)

    for chunk in chunks:
        stripped = chunk.strip()
        m = _SUBGOAL_RE.match(stripped)
        if not m:
            continue
        sg_id, title = m.group(1), m.group(2).strip()
        child_m = _CHILD_REF_RE.search(stripped)
        if child_m:
            subgoals.append(Subgoal(id=sg_id, title=title, status="split", child_plan=child_m.group(1)))
        else:
            content = stripped[m.end():].strip()
            subgoals.append(Subgoal(id=sg_id, title=title, status="inline", content=content))

    return subgoals


def load(path: Path) -> Plan:
    post = frontmatter.load(str(path))
    meta = post.metadata
    body: str = post.content

    title_m = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    title = title_m.group(1).strip() if title_m else path.stem

    sections = _split_sections(body)
    subgoals = _parse_subgoals(sections)

    return Plan(
        name=meta.get("name", path.stem),
        title=title,
        parent=meta.get("parent", ""),
        status=meta.get("status", "active"),
        sections=sections,
        subgoals=subgoals,
    )


def dump(plan: Plan, path: Path) -> None:
    """Serialise a Plan back to disk, preserving frontmatter + section order."""
    meta = {"name": plan.name, "status": plan.status}
    if plan.parent:
        meta["parent"] = plan.parent

    lines: list[str] = [f"# {plan.title}", ""]

    for section_name, content in plan.sections.items():
        lines.append(f"## {section_name}")
        lines.append("")
        if content:
            lines.append(content)
            lines.append("")

    body = "\n".join(lines).rstrip() + "\n"
    post = frontmatter.Post(body, **meta)
    path.write_text(frontmatter.dumps(post))
