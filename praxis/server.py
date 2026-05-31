import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from mcp.types import ToolListChangedNotification
from fastmcp import Context, FastMCP

from . import parser
from .models import Plan, Subgoal

mcp = FastMCP("praxis")

_plans_dir: Path = Path("plans")
_capabilities_dir: Path = Path("capabilities")
_patch_undo: dict[str, str] = {}  # name → file content before last patch

CHILD_PLAN_SECTIONS = [
    "Objective",
    "Current Assumptions",
    "Success Criteria",
    "Open Questions",
    "Workstreams",
    "Current Decisions",
    "Next Actions",
]

_SUBGOAL_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Matches "- [2026-05-14] Decision text" with optional rationale on the next line
_DECISION_RE = re.compile(
    r"^-\s+\[(\d{4}-\d{2}-\d{2})\]\s+(.+?)(?:\n\s+Rationale:\s*(.+))?$",
    re.MULTILINE,
)


def _plan_path(name: str) -> Path:
    return _plans_dir / f"{name}.md"


def _available_plan_names() -> list[str]:
    return sorted(p.stem for p in _plans_dir.glob("*.md"))


def _load(name: str) -> Plan:
    p = _plan_path(name)
    if not p.exists():
        available = _available_plan_names()
        raise FileNotFoundError(
            f"Plan '{name}' not found. Available: {available}"
        )
    return parser.load(p)


def _save(plan: Plan) -> None:
    parser.dump(plan, _plan_path(plan.name))


def _all_plans() -> list[Plan]:
    return [parser.load(p) for p in sorted(_plans_dir.glob("*.md"))]


def _load_capabilities() -> None:
    """Load project-specific capability tools from the capabilities directory."""
    if not _capabilities_dir.exists():
        return
    for cap_file in sorted(_capabilities_dir.glob("*.py")):
        try:
            code = cap_file.read_text()
            exec(compile(code, str(cap_file), "exec"), {"mcp": mcp})  # noqa: S102
        except Exception as e:
            print(f"Warning: failed to load capability '{cap_file.stem}': {e}", file=sys.stderr)


def _find_section_key(sections: dict[str, str], name: str) -> str | None:
    """Return the existing key matching name case-insensitively, or None."""
    name_lower = name.lower()
    return next((k for k in sections if k.lower() == name_lower), None)


def _section_items(content: str) -> list[str]:
    """Extract non-empty bullet items from a section, stripping leading '- '."""
    return [l.lstrip("- ").strip() for l in content.splitlines() if l.strip()]


def _plan_to_dict(plan: Plan, include_sections: list[str] | None = None) -> dict:
    if include_sections is not None:
        needles = {s.lower() for s in include_sections}
        sections = {k: v for k, v in plan.sections.items() if k.lower() in needles}
    else:
        sections = plan.sections
    return {
        "name": plan.name,
        "title": plan.title,
        "parent": plan.parent or None,
        "status": plan.status,
        "sections": sections,
        "subgoals": [
            {
                "id": sg.id,
                "title": sg.title,
                "status": sg.status,
                "content": sg.content or None,
                "child_plan": sg.child_plan or None,
            }
            for sg in plan.subgoals
        ],
    }


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_briefing(status: str | None = "active") -> dict:
    """
    Compact session-start digest: plans with status, plus next actions and
    open questions aggregated across the tree.
    Pass status='active' (default) to skip archived/complete plans, or
    status=None to include all plans.
    Use this instead of separate list_plans + query_plans calls at session start.
    """
    plans = _all_plans()
    if status is not None:
        plans = [p for p in plans if p.status == status]

    result: dict = {"plans": [], "next_actions": {}, "open_questions": {}}

    for p in plans:
        non_empty_sections = [k for k, v in p.sections.items() if v.strip()]
        result["plans"].append({
            "name": p.name,
            "title": p.title,
            "status": p.status,
            "parent": p.parent or None,
            "subgoals_total": len(p.subgoals),
            "subgoals_split": sum(1 for sg in p.subgoals if sg.status == "split"),
            "sections": non_empty_sections,
        })
        na_key = _find_section_key(p.sections, "next actions")
        if na_key:
            items = _section_items(p.sections[na_key])
            if items:
                result["next_actions"][p.name] = items
        oq_key = _find_section_key(p.sections, "open questions")
        if oq_key:
            items = _section_items(p.sections[oq_key])
            if items:
                result["open_questions"][p.name] = items

    return result


@mcp.tool()
def list_plans() -> list[dict]:
    """List all plans with their hierarchy, status, and subgoal summary."""
    plans = _all_plans()
    return [
        {
            "name": p.name,
            "title": p.title,
            "parent": p.parent or None,
            "status": p.status,
            "subgoals": [
                {"id": sg.id, "title": sg.title, "status": sg.status, "child_plan": sg.child_plan or None}
                for sg in p.subgoals
            ],
        }
        for p in plans
    ]


@mcp.tool()
def get_plan(name: str, include_sections: list[str] | None = None) -> dict:
    """
    Return a plan parsed into its sections and subgoals.
    Pass include_sections to return only specific sections (case-insensitive),
    reducing response size when only part of a plan is needed.
    """
    return _plan_to_dict(_load(name), include_sections)


@mcp.tool()
def get_plans(names: list[str], include_sections: list[str] | None = None) -> list[dict]:
    """
    Return multiple plans in a single call. Accepts a list of plan name slugs.
    Pass include_sections to filter sections on all returned plans.
    More efficient than sequential get_plan calls when multiple plans are needed.
    """
    return [_plan_to_dict(_load(name), include_sections) for name in names]


@mcp.tool()
def get_section(name: str, section: str) -> str:
    """
    Return a single named section from a plan. Section name is case-insensitive.
    More token-efficient than get_plan when only one section is needed.
    Raises ValueError if the section does not exist.
    """
    plan = _load(name)
    key = _find_section_key(plan.sections, section)
    if key is None:
        available = list(plan.sections.keys())
        raise ValueError(
            f"Section '{section}' not found in '{name}'. Available: {available}"
        )
    return plan.sections[key]


@mcp.tool()
def get_decisions(name: str) -> list[dict]:
    """
    Return the Current Decisions section as a structured list of
    [{date, decision, rationale}] records, parsed from log_decision entries.
    More useful than get_section for reading decisions programmatically.
    """
    plan = _load(name)
    key = _find_section_key(plan.sections, "current decisions")
    if key is None:
        return []
    content = plan.sections[key]
    results = []
    for m in _DECISION_RE.finditer(content):
        results.append({
            "date": m.group(1),
            "decision": m.group(2).strip(),
            "rationale": m.group(3).strip() if m.group(3) else "",
        })
    return results


@mcp.tool()
def search_plans(query: str, context_chars: int = 80) -> list[dict]:
    """
    Case-insensitive literal substring search across all plan content.
    Returns [{plan, section, excerpt}] for each matching section.
    context_chars controls how many characters of surrounding context to include
    on each side of the match (default 80, increase for more context).

    IMPORTANT: This tool uses exact substring matching, not semantic search.
    Single keywords work well ("positioning", "ecommerce"). Multi-word phrases
    ("CSVAPI positioning strategy") will only match if those exact words appear
    adjacent in the text — prefer short, specific keywords over phrases.

    Use this tool only for discovery — when you do not know which plan or section
    contains the relevant context. If you already know the plan, use get_plans
    with include_sections instead; it is more targeted and token-efficient.
    """
    results: list[dict] = []
    needle = query.lower()

    for plan in _all_plans():
        for section_name, content in plan.sections.items():
            if needle in content.lower():
                lower = content.lower()
                idx = lower.index(needle)
                start = max(0, idx - context_chars)
                end = min(len(content), idx + len(query) + context_chars)
                prefix = "..." if start > 0 else ""
                suffix = "..." if end < len(content) else ""
                excerpt = prefix + content[start:end].strip() + suffix
                results.append({"plan": plan.name, "section": section_name, "excerpt": excerpt})

    return results


@mcp.tool()
def create_plan(
    name: str,
    title: str,
    objective: str = "",
    parent: str | None = None,
) -> str:
    """
    Create a new active plan with the standard sections pre-populated.
    Use this when starting work on a new goal that has no existing plan.

    - name: slug used as the filename (e.g. "csvapi-outreach")
    - title: human-readable title
    - objective: optional initial content for the Objective section
    - parent: optional parent plan name if this is a child plan
    """
    path = _plan_path(name)
    if path.exists():
        raise FileExistsError(f"Plan '{name}' already exists.")

    plan = Plan(
        name=name,
        title=title,
        parent=parent,
        status="active",
        sections={s: "" for s in CHILD_PLAN_SECTIONS},
    )
    if objective:
        plan.sections["Objective"] = objective
    _save(plan)
    return f"Plan '{name}' created."


@mcp.tool()
def set_status(name: str, status: str) -> str:
    """
    Set a plan's status. Valid values: active, complete, archived.
    """
    valid = {"active", "complete", "archived"}
    if status not in valid:
        raise ValueError(f"Invalid status '{status}'. Must be one of: {sorted(valid)}")
    plan = _load(name)
    plan.status = status
    _save(plan)
    return f"Plan '{name}' status set to '{status}'."


@mcp.tool()
def log_decision(name: str, decision: str, rationale: str = "") -> str:
    """
    Append a timestamped decision record to a plan's Current Decisions section.
    Decisions are durable context — prefer this over update_section for decisions.
    """
    plan = _load(name)
    key = _find_section_key(plan.sections, "current decisions") or "Current Decisions"
    existing = plan.sections.get(key, "")
    lines = existing.splitlines() if existing.strip() else []
    entry = f"- [{date.today().isoformat()}] {decision}"
    if rationale:
        entry += f"\n  Rationale: {rationale}"
    lines.append(entry)
    plan.sections[key] = "\n".join(lines)
    _save(plan)
    return f"Decision logged in '{name}'."


@mcp.tool()
def add_subgoal(name: str, title: str, content: str = "") -> str:
    """
    Append a new inline subgoal to a plan's Top-Level Subgoals section.
    The subgoal ID (A, B, C…) is assigned automatically as the next available letter.
    """
    plan = _load(name)
    key = _find_section_key(plan.sections, "top-level subgoals")
    if key is None:
        raise ValueError(f"Plan '{name}' has no 'Top-Level Subgoals' section.")

    existing_ids = {sg.id.upper() for sg in plan.subgoals}
    next_id = next(c for c in _SUBGOAL_LETTERS if c not in existing_ids)

    block = f"\n\n### Subgoal {next_id}: {title}"
    if content:
        block += f"\n\n{content}"

    plan.sections[key] = plan.sections[key].rstrip() + block
    _save(plan)
    return f"Added Subgoal {next_id}: '{title}' to plan '{name}'."


@mcp.tool()
def update_section(name: str, section: str, content: str) -> str:
    """Overwrite a named section in a plan. Section name is case-insensitive."""
    plan = _load(name)
    key = _find_section_key(plan.sections, section) or section
    plan.sections[key] = content
    _save(plan)
    return f"Section '{key}' updated in plan '{name}'."


@mcp.tool()
def add_next_action(name: str, action: str) -> str:
    """Append a next action item to a plan without overwriting existing items."""
    plan = _load(name)
    key = _find_section_key(plan.sections, "next actions") or "Next Actions"
    existing = plan.sections.get(key, "")
    lines = [l for l in existing.splitlines() if l.strip()]
    lines.append(f"- {action.lstrip('- ')}")
    plan.sections[key] = "\n".join(lines)
    _save(plan)
    return f"Added next action to '{name}'."


@mcp.tool()
def complete_next_action(name: str, action: str) -> str:
    """
    Remove a completed next action. Tries exact match first, then falls back to
    case-insensitive substring. Raises ValueError if no match is found, or if a
    substring match is ambiguous (matches more than one action).
    """
    plan = _load(name)
    key = _find_section_key(plan.sections, "next actions")
    if key is None:
        raise ValueError(f"Plan '{name}' has no 'Next Actions' section.")

    lines = [l for l in plan.sections[key].splitlines() if l.strip()]
    needle = action.strip().lstrip("- ")

    # Exact match first (case-insensitive)
    exact = [l for l in lines if l.lstrip("- ").strip().lower() == needle.lower()]
    if exact:
        remaining = [l for l in lines if l not in exact]
        plan.sections[key] = "\n".join(remaining)
        _save(plan)
        return f"Removed {len(exact)} action(s) matching '{action}' from '{name}'."

    # Substring fallback
    matches = [l for l in lines if needle.lower() in l.lower()]
    if len(matches) == 0:
        raise ValueError(
            f"No action matching '{action}' found in '{name}'. "
            f"Current actions: {[l.lstrip('- ').strip() for l in lines]}"
        )
    if len(matches) > 1:
        raise ValueError(
            f"Ambiguous match for '{action}' in '{name}' — {len(matches)} actions matched: "
            f"{[l.lstrip('- ').strip() for l in matches]}. Use a more specific string."
        )

    remaining = [l for l in lines if l not in matches]
    plan.sections[key] = "\n".join(remaining)
    _save(plan)
    return f"Removed action matching '{action}' from '{name}'."


@mcp.tool()
def split_subgoal(source_plan: str, subgoal_id: str, child_plan_name: str) -> str:
    """
    Promote an inline subgoal to a child plan file.

    Creates the child plan with required sections pre-populated from the subgoal
    content, then updates the parent plan to replace the inline section with a
    reference to the new child plan.
    """
    parent = _load(source_plan)

    sg = next((s for s in parent.subgoals if s.id.upper() == subgoal_id.upper()), None)
    if sg is None:
        raise ValueError(f"Subgoal '{subgoal_id}' not found in plan '{source_plan}'.")
    if sg.status == "split":
        raise ValueError(f"Subgoal '{subgoal_id}' is already split into '{sg.child_plan}'.")

    child_path = _plan_path(child_plan_name)
    if child_path.exists():
        raise FileExistsError(f"Plan file '{child_plan_name}.md' already exists.")

    child = Plan(
        name=child_plan_name,
        title=sg.title,
        parent=source_plan,
        status="active",
        sections={s: "" for s in CHILD_PLAN_SECTIONS},
    )
    if sg.content:
        child.sections["Objective"] = sg.content

    _save(child)

    sg.status = "split"
    sg.child_plan = child_plan_name
    sg.content = ""

    subgoal_key = _find_section_key(parent.sections, "top-level subgoals")
    if subgoal_key:
        subgoal_section = parent.sections[subgoal_key]
        ref_line = f"This subgoal is now tracked in [{child_plan_name}](./{child_plan_name}.md)."
        block_re = rf"(###\s+Subgoal\s+{subgoal_id}[^\n]*\n)(.*?)(?=###\s+Subgoal\s+[A-Z]|\Z)"

        def replace_block(m: re.Match) -> str:
            return f"{m.group(1)}\n{ref_line}\n\n"

        new_section = re.sub(block_re, replace_block, subgoal_section, flags=re.DOTALL)
        parent.sections[subgoal_key] = new_section.strip()

    _save(parent)
    return f"Subgoal '{subgoal_id}' split into '{child_plan_name}.md'. Parent plan updated."


@mcp.tool()
def query_plans(field: str) -> dict[str, str]:
    """
    Aggregate a section across all plans. Field is a section name, e.g.
    'next actions', 'open questions', 'current decisions'.
    Returns {plan_name: section_content} for plans where the section is non-empty.
    """
    results: dict[str, str] = {}
    for plan in _all_plans():
        key = _find_section_key(plan.sections, field)
        if key:
            content = plan.sections[key].strip()
            if content:
                results[plan.name] = content
    return results


# ── Capability management (Mode 3) ────────────────────────────────────────────

_CAPABILITY_SECTIONS = ["Description", "Inputs", "Outputs", "Implementation Notes", "Code"]


def _extract_function_name(code: str) -> str | None:
    """Return the first def name found in the code block."""
    m = re.search(r"^def\s+(\w+)", code, re.MULTILINE)
    return m.group(1) if m else None


@mcp.tool()
def list_capabilities() -> list[dict]:
    """
    List all capability specs — proposed or installed tools that extend Praxis.
    Returns [{name, title, status}] where status is 'capability' (pending) or 'installed'.
    """
    return [
        {"name": p.name, "title": p.title, "status": p.status}
        for p in _all_plans()
        if p.status in {"capability", "installed"}
    ]


@mcp.tool()
def create_capability_spec(
    name: str,
    title: str,
    description: str,
    inputs_description: str,
    outputs_description: str,
    code: str,
    implementation_notes: str = "",
) -> str:
    """
    Document a proposed new Praxis tool as a capability spec for user review.

    Call this when an action requires a capability that doesn't exist. Write a
    complete, working @mcp.tool() function in the code parameter — it will be
    written to capabilities/<name>.py on install_capability().

    After creating the spec, present it to the user and ask for approval.
    Only call install_capability() after the user explicitly approves.

    - code: complete @mcp.tool() decorated Python function, ready to insert
    - inputs_description: human-readable description of parameters
    - outputs_description: what the tool returns
    """
    path = _plan_path(name)
    if path.exists():
        raise FileExistsError(f"Plan or capability '{name}' already exists.")

    try:
        compile(code, "<capability>", "exec")
    except SyntaxError as e:
        raise ValueError(f"Code has a syntax error: {e}")

    sections: dict[str, str] = {
        "Description": description,
        "Inputs": inputs_description,
        "Outputs": outputs_description,
        "Code": code,
    }
    if implementation_notes:
        sections["Implementation Notes"] = implementation_notes

    spec = Plan(
        name=name,
        title=title,
        parent=None,
        status="capability",
        sections=sections,
    )
    _save(spec)
    return f"Capability spec '{name}' created. Present to user for review before installing."


@mcp.tool()
async def install_capability(name: str, ctx: Context) -> str:
    """
    Install an approved capability spec into the project's capabilities directory.

    Writes the tool code to capabilities/<name>.py, registers it immediately in the
    running server, and notifies the client to refresh its tool list — no session
    restart required.

    Only call this after the user has explicitly approved the capability spec.
    """
    plan = _load(name)
    if plan.status not in {"capability", "installed"}:
        raise ValueError(f"'{name}' is not a capability spec (status: '{plan.status}').")

    code = plan.sections.get("Code", "").strip()
    if not code:
        raise ValueError(f"Capability '{name}' has no Code section.")

    try:
        compiled = compile(code, "<capability>", "exec")
    except SyntaxError as e:
        raise ValueError(f"Code has a syntax error: {e}")

    func_name = _extract_function_name(code)
    if func_name:
        core_src = Path(__file__).read_text()
        if f"def {func_name}" in core_src:
            raise ValueError(f"Function '{func_name}' already exists in the core server.")
        for existing in _capabilities_dir.glob("*.py"):
            if f"def {func_name}" in existing.read_text():
                raise ValueError(
                    f"Function '{func_name}' already exists in capability '{existing.stem}'."
                )

    _capabilities_dir.mkdir(parents=True, exist_ok=True)
    cap_file = _capabilities_dir / f"{name}.py"
    if cap_file.exists():
        raise FileExistsError(f"Capability file '{cap_file.name}' already exists.")

    cap_file.write_text(code + "\n")

    # Hot-register: run the decorator now so the tool is live without a restart.
    exec(compiled, {"mcp": mcp})  # noqa: S102

    plan.status = "installed"
    _save(plan)

    # Tell the client the tool list has changed.
    await ctx.send_notification(ToolListChangedNotification())

    return (
        f"Capability '{name}' installed and live.\n\n"
        f"```python\n{code}\n```"
    )


@mcp.tool()
async def patch_capability(name: str, old_str: str, new_str: str, ctx: Context) -> str:
    """
    Apply a targeted find-and-replace patch to an installed capability file.

    Saves the pre-patch content to an undo buffer, writes the change, hot-reloads
    the tool, and returns a diff for review. After reviewing, call accept_patch(name)
    to finalise or revert_capability(name) to undo.

    Raises ValueError if old_str is not found or matches more than once.
    """
    import difflib

    cap_file = _capabilities_dir / f"{name}.py"
    if not cap_file.exists():
        raise FileNotFoundError(f"Capability file '{name}.py' not found.")

    current = cap_file.read_text()
    count = current.count(old_str)
    if count == 0:
        raise ValueError(f"old_str not found in '{name}.py'.")
    if count > 1:
        raise ValueError(
            f"old_str matches {count} locations in '{name}.py' — make it more specific."
        )

    new_content = current.replace(old_str, new_str, 1)

    try:
        compile(new_content, "<capability>", "exec")
    except SyntaxError as e:
        raise ValueError(f"Patched code has a syntax error: {e}")

    _patch_undo[name] = current
    cap_file.write_text(new_content)

    exec(compile(new_content, str(cap_file), "exec"), {"mcp": mcp})  # noqa: S102
    await ctx.send_notification(ToolListChangedNotification())

    lines = list(difflib.unified_diff(
        current.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"{name}.py (before)",
        tofile=f"{name}.py (after)",
    ))
    return (
        f"Patch applied and live. Review the diff, then call accept_patch('{name}') "
        f"to keep or revert_capability('{name}') to undo.\n\n"
        "```diff\n" + "".join(lines) + "\n```"
    )


@mcp.tool()
def accept_patch(name: str) -> str:
    """
    Finalise the last patch to a capability. Clears the undo buffer.

    Call this after reviewing the diff from patch_capability and deciding to keep
    the change. If you want to undo instead, call revert_capability(name).
    """
    if name not in _patch_undo:
        raise ValueError(f"No pending patch for '{name}'.")
    del _patch_undo[name]
    return f"Patch for '{name}' accepted."


@mcp.tool()
async def revert_capability(name: str, ctx: Context) -> str:
    """
    Revert the last patch to a capability, restoring the pre-patch file content.

    Reads the undo buffer saved by patch_capability, writes it back, and hot-reloads
    the tool. Raises ValueError if there is no pending patch to revert.
    """
    if name not in _patch_undo:
        raise ValueError(f"No pending patch for '{name}' — nothing to revert.")

    original = _patch_undo.pop(name)
    cap_file = _capabilities_dir / f"{name}.py"
    cap_file.write_text(original)

    exec(compile(original, str(cap_file), "exec"), {"mcp": mcp})  # noqa: S102
    await ctx.send_notification(ToolListChangedNotification())

    return f"Capability '{name}' reverted to pre-patch state."


# ── Protocol library ──────────────────────────────────────────────────────────

_PROTOCOL_SECTIONS = ["Trigger", "Required Inputs", "Steps", "Output", "Notes"]


@mcp.tool()
def list_protocols() -> list[dict]:
    """
    List all available protocols (reusable playbooks for recurring action types).
    Returns [{name, title, trigger}] for each protocol.
    Protocols are plans with status='protocol' and do not appear in get_briefing.
    """
    return [
        {
            "name": p.name,
            "title": p.title,
            "trigger": p.sections.get("Trigger", "").strip(),
        }
        for p in _all_plans()
        if p.status == "protocol"
    ]


@mcp.tool()
def find_protocol(action: str) -> list[dict]:
    """
    Search protocol trigger fields for a match to the given action description.
    Returns matching protocols as [{name, title, trigger}].

    Call this before executing any recurring action to check if a playbook exists.
    Trigger matching uses literal substring: each comma-separated keyword in the
    trigger field is checked against the action description.

    Example: find_protocol("draft outreach email to SaaS prospects")
    """
    action_lower = action.lower()
    results = []
    for p in _all_plans():
        if p.status != "protocol":
            continue
        trigger = p.sections.get("Trigger", "")
        keywords = [kw.strip().lower() for kw in trigger.replace("\n", ",").split(",") if kw.strip()]
        if any(kw in action_lower for kw in keywords):
            results.append({"name": p.name, "title": p.title, "trigger": trigger})
    return results


@mcp.tool()
def create_protocol(
    name: str,
    title: str,
    trigger: str,
    inputs_description: str,
    steps: str,
    output_description: str,
    notes: str = "",
) -> str:
    """
    Create a new reusable protocol (playbook) for a recurring action type.
    Protocols are stored with status='protocol' and discovered via find_protocol.

    - trigger: comma-separated keywords that identify this action type
      e.g. "outreach email, cold email, prospect email"
    - inputs_description: context the agent needs before starting
    - steps: numbered steps to execute
    - output_description: what 'done' looks like
    - notes: optional caveats, examples, or edge cases
    """
    path = _plan_path(name)
    if path.exists():
        raise FileExistsError(f"Plan or protocol '{name}' already exists.")

    sections: dict[str, str] = {
        "Trigger": trigger,
        "Required Inputs": inputs_description,
        "Steps": steps,
        "Output": output_description,
    }
    if notes:
        sections["Notes"] = notes

    protocol = Plan(
        name=name,
        title=title,
        parent=None,
        status="protocol",
        sections=sections,
    )
    _save(protocol)
    return f"Protocol '{name}' created."


# ── Session protocol definition ────────────────────────────────────────────────

_PROTOCOL = """\
# Praxis Session Protocol

## On activation
get_briefing has already been called. Report: active plans and hierarchy, which plans
have next actions, which have open questions. If the user specified a focus area, call
get_plans on the relevant child plans before responding. Ask what to work on, or proceed
if the user already stated a goal.

## Reading plans
After get_briefing you already know which plans exist and which sections each contains.
Use that knowledge — do not default to search_plans as a first step.

Decision tree:
1. You know which plan(s) and section(s) are relevant → get_plans with include_sections.
   This is the default path for most questions after a briefing.
2. You do not know which plan contains the relevant context → search_plans with a single
   short keyword (e.g. "ecommerce", "validation"). Multi-word phrases rarely match because
   search_plans uses literal substring matching, not semantic search. Avoid generic terms
   like "competitor", "pricing", or "market" — these hit large sections across many plans
   and produce oversized, expensive results. Use specific names ("csvapi", "hypothesis",
   "signal") to scope results tightly.
3. You need structured decision history → get_decisions (not get_section).
4. You need one specific section from one plan → get_section.

Never fetch a full plan when targeted sections suffice. Never call get_plan sequentially
for more than one plan — use get_plans (batch). Do not generate advice without first
grounding it in plan content.

## Writing to plans

1. Decision made → call get_decisions first (guard against cross-session duplicates),
   then call log_decision immediately. Do not batch writes at the end of the session.
2. Action completed → call get_section(name, "Next Actions") to fetch the current list,
   present it to the user, and ask them to confirm which item to close. Then call
   complete_next_action with their confirmed wording. Always show the list — do not
   attempt to match from memory or guess.
3. New action emerged → call add_next_action immediately. Check the existing Next Actions
   list first to avoid duplicates.
4. Any write → confirm the target plan name with the user before calling any write tool.

## Executing actions
For any next action, follow this order:
1. Call find_protocol(action_text) — if a match exists, load it with get_plan and
   follow its Steps section exactly.
2. If no protocol and the action is directly executable: do it. For any external
   operation (email, API call, post), state the exact action and params and wait
   for the user to confirm before proceeding.
3. If a required capability doesn't exist: call create_capability_spec with a
   complete @mcp.tool() implementation, present it to the user for review, and
   only call install_capability() after explicit approval.
   If updating an existing capability: call patch_capability(name, old_str, new_str),
   then output the diff from the tool result as text in your response, then call
   accept_patch(name) in the same turn. The tool approval UI is the yes/no decision
   — the diff is already visible in the chat above it. If accept_patch is rejected
   by the user, immediately call revert_capability(name).
4. If the action is too vague or large: decompose with add_next_action, then
   loop back to step 1 for each sub-action.
5. If the action will recur and no protocol exists: call create_protocol after
   completing to codify the steps for next time.

## Session close
Before closing, check: any unlogged decisions? any completed actions not yet ticked off?
Prompt "Shall I sync the plans?" and sweep if confirmed.

## What this system is for
These plans are durable commercial memory. Treat them as the authoritative record of
strategy, decisions, and next actions — not notes to summarise and discard.
"""

_SLASH_COMMAND_CONTENT = """\
You are entering Praxis mode. Do the following now, in order:

1. Call `get_briefing` to load the current state of all active plans.
2. Call `get_agent_protocol` to load the session protocol.
3. Load all tools needed this session via ToolSearch:
   select:mcp__praxis__create_plan,mcp__praxis__add_next_action,mcp__praxis__complete_next_action,mcp__praxis__log_decision,mcp__praxis__update_section,mcp__praxis__find_protocol,mcp__praxis__create_protocol,mcp__praxis__list_protocols,mcp__praxis__create_capability_spec,mcp__praxis__install_capability,mcp__praxis__patch_capability,mcp__praxis__accept_patch,mcp__praxis__revert_capability,mcp__praxis__list_capabilities
4. Call list_protocols() to see available playbooks.
5. Follow the protocol for the remainder of this session.

Core rules that apply for the entire session regardless of context:
- Never answer a question about plan content without first reading the relevant plan.
- Never write to a plan without confirming the target plan name with the user first.
- Never attempt to complete an action from memory — always fetch the Next Actions list
  and ask the user to confirm which item to close.

$ARGUMENTS
"""

_CODEX_PLUGIN_MANIFEST = {
    "name": "praxis",
    "version": "0.1.0",
    "description": "Praxis planning MCP server for durable plans, protocols, and capabilities.",
    "author": {
        "name": "Praxis",
    },
    "mcpServers": "./.mcp.json",
    "interface": {
        "displayName": "Praxis",
        "shortDescription": "Planning MCP with durable project memory",
        "longDescription": (
            "Bring Praxis into Codex as a repo-local MCP server for durable plans, "
            "protocols, decisions, and capability extension."
        ),
        "developerName": "Praxis",
        "category": "Productivity",
        "capabilities": ["Interactive", "Write"],
        "defaultPrompt": [
            "Open Praxis and brief me on the active plans in this repo."
        ],
        "brandColor": "#1F6FEB",
    },
}

_CODEX_SLASH_COMMAND_CONTENT = """\
---
description: Start a Praxis planning session by loading active plans, protocols, and session rules.
argument-hint: [focus]
allowed-tools: [mcp__praxis__get_briefing, mcp__praxis__get_agent_protocol, mcp__praxis__list_protocols, tool_search_tool]
---

You are entering Praxis mode. Do the following now, in order:

1. Call `get_briefing` to load the current state of all active plans.
2. Call `get_agent_protocol` to load the session protocol.
3. Load all tools needed this session via ToolSearch:
   select:mcp__praxis__create_plan,mcp__praxis__add_next_action,mcp__praxis__complete_next_action,mcp__praxis__log_decision,mcp__praxis__update_section,mcp__praxis__find_protocol,mcp__praxis__create_protocol,mcp__praxis__list_protocols,mcp__praxis__create_capability_spec,mcp__praxis__install_capability,mcp__praxis__patch_capability,mcp__praxis__accept_patch,mcp__praxis__revert_capability,mcp__praxis__list_capabilities
4. Call list_protocols() to see available playbooks.
5. Follow the protocol for the remainder of this session.

Core rules that apply for the entire session regardless of context:
- Never answer a question about plan content without first reading the relevant plan.
- Never write to a plan without confirming the target plan name with the user first.
- Never attempt to complete an action from memory — always fetch the Next Actions list
  and ask the user to confirm which item to close.

$ARGUMENTS
"""

_CODEX_MARKETPLACE = {
    "name": "praxis-local",
    "interface": {
        "displayName": "Praxis Local Plugins",
    },
    "plugins": [
        {
            "name": "praxis",
            "source": {
                "source": "local",
                "path": "./plugins/praxis",
            },
            "policy": {
                "installation": "INSTALLED_BY_DEFAULT",
                "authentication": "ON_INSTALL",
            },
            "category": "Productivity",
        }
    ],
}

_CODEX_RUNNER = """#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../../.." && pwd)"

for VENV_PYTHON in "${PROJECT_ROOT}/.venv/bin/python" "${PROJECT_ROOT}/venv/bin/python"; do
  if [[ -x "${VENV_PYTHON}" ]]; then
    exec "${VENV_PYTHON}" -m praxis.server \
      --plans-dir "${PROJECT_ROOT}/plans" \
      --capabilities-dir "${PROJECT_ROOT}/capabilities"
  fi
done

export PYTHONPATH="${PROJECT_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
exec python3 -m praxis.server \
  --plans-dir "${PROJECT_ROOT}/plans" \
  --capabilities-dir "${PROJECT_ROOT}/capabilities"
"""

_CLAUDE_RUNNER = """#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

for VENV_PYTHON in "${PROJECT_ROOT}/.venv/bin/python" "${PROJECT_ROOT}/venv/bin/python"; do
  if [[ -x "${VENV_PYTHON}" ]]; then
    exec "${VENV_PYTHON}" -m praxis.server \
      --plans-dir "${PROJECT_ROOT}/plans" \
      --capabilities-dir "${PROJECT_ROOT}/capabilities"
  fi
done

export PYTHONPATH="${PROJECT_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
exec python3 -m praxis.server \
  --plans-dir "${PROJECT_ROOT}/plans" \
  --capabilities-dir "${PROJECT_ROOT}/capabilities"
"""


@mcp.tool()
def get_agent_protocol() -> str:
    """
    Return the Praxis session protocol — the rules the agent should follow
    during a planning session. Called automatically by the /praxis slash command.
    """
    return _PROTOCOL


# ── Install ────────────────────────────────────────────────────────────────────

def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _upsert_marketplace_plugin(project: Path) -> None:
    marketplace_path = project / ".agents" / "plugins" / "marketplace.json"
    plugin_entry = _CODEX_MARKETPLACE["plugins"][0]

    if marketplace_path.exists():
        data = json.loads(marketplace_path.read_text(encoding="utf-8"))
    else:
        data = {
            "name": _CODEX_MARKETPLACE["name"],
            "interface": dict(_CODEX_MARKETPLACE["interface"]),
            "plugins": [],
        }

    data.setdefault("name", _CODEX_MARKETPLACE["name"])
    interface = data.setdefault("interface", {})
    interface.setdefault("displayName", _CODEX_MARKETPLACE["interface"]["displayName"])

    plugins = data.setdefault("plugins", [])
    plugins = [p for p in plugins if p.get("name") != plugin_entry["name"]]
    plugins.append(plugin_entry)
    data["plugins"] = plugins

    _write_text(marketplace_path, json.dumps(data, indent=2) + "\n")


def _upsert_mcp_json(path: Path, new_servers: dict) -> None:
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = {}
    data.setdefault("mcpServers", {}).update(new_servers)
    _write_text(path, json.dumps(data, indent=2) + "\n")


def _create_praxis_dirs(project: Path) -> None:
    for d in ("plans", "capabilities"):
        (project / d).mkdir(exist_ok=True)
        print(f"Ensured directory exists: {project / d}")


def _install_claude_command(project: Path) -> None:
    _create_praxis_dirs(project)

    commands_dir = project / ".claude" / "commands"
    commands_dir.mkdir(parents=True, exist_ok=True)
    command_path = commands_dir / "praxis.md"
    command_path.write_text(_SLASH_COMMAND_CONTENT, encoding="utf-8")

    runner_path = project / ".claude" / "run-praxis-mcp"
    _write_text(runner_path, _CLAUDE_RUNNER)
    runner_path.chmod(0o755)

    mcp_path = project / ".mcp.json"
    _upsert_mcp_json(mcp_path, {"praxis": {"command": str(runner_path)}})

    print(f"Installed /praxis command at {command_path}")
    print(f"Installed MCP runner at {runner_path}")
    print(f"Updated MCP config at {mcp_path}")
    print("Restart your Claude session to pick up the new command and MCP server.")


def _codex_mcp_config(project: Path) -> dict:
    return {
        "mcpServers": {
            "praxis": {
                "command": str(project / "plugins" / "praxis" / "scripts" / "run-praxis-mcp"),
            }
        }
    }


def _install_codex_plugin(project: Path) -> None:
    _create_praxis_dirs(project)

    plugin_root = project / "plugins" / "praxis"
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    mcp_path = plugin_root / ".mcp.json"
    command_path = plugin_root / "commands" / "praxis.md"
    runner_path = plugin_root / "scripts" / "run-praxis-mcp"
    marketplace_path = project / ".agents" / "plugins" / "marketplace.json"

    _write_text(manifest_path, json.dumps(_CODEX_PLUGIN_MANIFEST, indent=2) + "\n")
    _write_text(mcp_path, json.dumps(_codex_mcp_config(project), indent=2) + "\n")
    _write_text(command_path, _CODEX_SLASH_COMMAND_CONTENT)
    _write_text(runner_path, _CODEX_RUNNER)
    runner_path.chmod(0o755)
    _upsert_marketplace_plugin(project)

    print(f"Installed Codex plugin manifest at {manifest_path}")
    print(f"Installed Codex MCP config at {mcp_path}")
    print(f"Installed Codex /praxis command at {command_path}")
    print(f"Installed Codex marketplace entry at {marketplace_path}")
    print("Restart Codex to pick up the default Praxis plugin.")


def _install_command(project: Path, target: str) -> None:
    if target == "claude":
        _install_claude_command(project)
        return
    if target == "codex":
        _install_codex_plugin(project)
        return
    raise ValueError(f"Unsupported install target '{target}'.")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="praxis: self-extending agent infrastructure")
    ap.add_argument("--plans-dir", default="plans", help="Path to plans directory")
    ap.add_argument(
        "--capabilities-dir",
        default=None,
        help="Path to capabilities directory (default: capabilities/ sibling of plans-dir)",
    )

    sub = ap.add_subparsers(dest="command")
    install_p = sub.add_parser("install", help="Install Praxis integration into a project")
    install_p.add_argument(
        "--project", default=".", help="Target project directory (default: current directory)"
    )
    install_p.add_argument(
        "--target",
        choices=["claude", "codex"],
        default="claude",
        help="Install target (default: claude)",
    )

    args = ap.parse_args()

    if args.command == "install":
        _install_command(Path(args.project).expanduser().resolve(), args.target)
        return

    global _plans_dir, _capabilities_dir
    _plans_dir = Path(args.plans_dir).expanduser().resolve()
    _capabilities_dir = (
        Path(args.capabilities_dir).expanduser().resolve()
        if args.capabilities_dir
        else _plans_dir.parent / "capabilities"
    )

    if not _plans_dir.exists():
        print(f"Error: plans directory '{_plans_dir}' does not exist.", file=sys.stderr)
        sys.exit(1)

    _load_capabilities()
    mcp.run()


if __name__ == "__main__":
    main()
