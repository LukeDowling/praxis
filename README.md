# Praxis

Self-extending agent infrastructure — durable plans, protocols, and live capability creation via MCP.

Praxis is an MCP server that gives AI agents persistent, structured memory across sessions. Plans are Markdown files on disk. Agents read them, write to them, and can install new tools into the running server without a restart.

---

## Concepts

**Plans** — Markdown files with YAML frontmatter stored in a `plans/` directory. Each plan has a status (`active`, `complete`, `archived`), a set of named sections (Objective, Next Actions, Open Questions, etc.), and optional inline subgoals that can be promoted to child plans.

**Protocols** — Reusable playbooks stored as plans with `status: protocol`. Agents discover them via `find_protocol` before executing any recurring action type.

**Capabilities** — Python files in a `capabilities/` directory that are `exec`'d into the running server on startup, and hot-loaded when installed mid-session. Agents can propose, review, and install new MCP tools without restarting.

---

## Requirements

- Python 3.11+

---

## Installation

```bash
git clone <repo>
cd praxis
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

---

## Running the server

```bash
praxis --plans-dir /path/to/plans
```

The `--capabilities-dir` defaults to a `capabilities/` sibling of `--plans-dir`. Override it explicitly if needed:

```bash
praxis --plans-dir plans/ --capabilities-dir capabilities/
```

---

## Claude Code integration

### 1 — Install

Run this once from inside the project:

```bash
praxis install --target claude --project .
```

This writes:
- `plans/` and `capabilities/` — created if they don't exist
- `.claude/commands/praxis.md` — the `/praxis` slash command
- `.claude/run-praxis-mcp` — executable that starts the MCP server
- `.mcp.json` — MCP server config (merged into any existing file)

The runner script detects `.venv` or `venv` automatically, falling back to the system `python3`.

Restart your Claude Code session to pick up the new command and MCP server.

### 2 — Use it

Type `/praxis` at the start of a session. The command instructs Claude to:

1. Call `get_briefing` — loads active plans, next actions, open questions
2. Call `get_agent_protocol` — loads the session rules
3. Load all write/read tool schemas
4. Call `list_protocols` — discovers available playbooks

Pass an optional focus area to scope the briefing:

```
/praxis focus on csvapi
```

---

## Codex integration

```bash
praxis install --target codex --project .
```

This writes:
- `plans/` and `capabilities/` — created if they don't exist
- `plugins/praxis/.codex-plugin/plugin.json` — plugin manifest
- `plugins/praxis/.mcp.json` — MCP server config pointing at the runner script
- `plugins/praxis/scripts/run-praxis-mcp` — executable that starts the server (detects `.venv` or `venv`)
- `.agents/plugins/marketplace.json` — registers the plugin as installed by default

Restart Codex to pick up the plugin.

---

## Plan file format

Plans are Markdown files with YAML frontmatter:

```markdown
---
name: my-plan
status: active
parent: parent-plan-name   # optional
---

# My Plan Title

## Objective

What we are trying to achieve.

## Next Actions

- Draft the proposal
- Send to stakeholders

## Open Questions

- Which pricing tier applies here?

## Current Decisions

- [2026-05-18] We will lead with the import validation use case
  Rationale: Highest signal from early interviews
```

Subgoals are `### Subgoal A: Title` blocks nested inside a `## Top-Level Subgoals` section. A subgoal can be promoted to its own plan file with `split_subgoal`.

---

## Capability file format

Capabilities are plain Python files containing one or more `@mcp.tool()` decorated functions. The `mcp` object is injected at load time — do not import it.

```python
@mcp.tool()
def my_tool(arg: str) -> str:
    """Description shown to the agent."""
    return f"result: {arg}"
```

Capabilities are loaded from `capabilities/` on startup and can be installed mid-session via `install_capability` without restarting the server.

---

## Tool reference

| Tool | Description |
|------|-------------|
| `get_briefing` | Session-start digest: active plans, next actions, open questions |
| `list_plans` | All plans with hierarchy and status |
| `get_plan` | Full plan with sections and subgoals |
| `get_plans` | Batch version of `get_plan` |
| `get_section` | Single named section from a plan |
| `get_decisions` | Structured decision history from a plan |
| `search_plans` | Substring search across all plan content |
| `create_plan` | Create a new plan file |
| `set_status` | Set plan status (active / complete / archived) |
| `update_section` | Overwrite a named section |
| `add_next_action` | Append an action item |
| `complete_next_action` | Remove a completed action item |
| `log_decision` | Append a timestamped decision record |
| `add_subgoal` | Append an inline subgoal |
| `split_subgoal` | Promote a subgoal to a child plan |
| `query_plans` | Aggregate a section across all plans |
| `list_protocols` | All available protocol playbooks |
| `find_protocol` | Find a protocol matching an action description |
| `create_protocol` | Create a new reusable protocol |
| `list_capabilities` | All capability specs and installed tools |
| `create_capability_spec` | Propose a new tool (pending user approval) |
| `install_capability` | Install an approved capability spec live |
| `patch_capability` | Find-and-replace patch on an installed capability |
| `accept_patch` | Finalise a pending patch |
| `revert_capability` | Undo the last patch |
| `get_agent_protocol` | Return the session protocol rules |
