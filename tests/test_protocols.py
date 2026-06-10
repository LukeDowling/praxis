"""Tests for protocol editing tools: patch_protocol and add_protocol_step."""
import re

import pytest

from praxis import server


STEPS_14 = "\n".join(f"{i}. Step {i}" for i in range(1, 15))
NOTES = "Lockstep systems: factory-cli.\nWhat factory-cli does NOT automate:\n- pricing\n- tax"


@pytest.fixture
def plans_dir(tmp_path, monkeypatch):
    """Point the server at an isolated plans directory for each test."""
    monkeypatch.setattr(server, "_plans_dir", tmp_path)
    return tmp_path


@pytest.fixture
def npi(plans_dir):
    """A multi-section protocol mirroring the new-product-integration case."""
    server.create_protocol(
        name="new-product-integration",
        title="New Product Integration",
        trigger="integrate product, onboard sku, new product",
        inputs_description="product spec, sku list",
        steps=STEPS_14,
        output_description="all 14 steps verified green",
        notes=NOTES,
    )
    return "new-product-integration"


def _section_block(text: str, heading: str) -> str:
    """Raw markdown block for a section, from its '## heading' to the next '## ' or EOF."""
    m = re.search(rf"(## {heading}\b.*?)(?=\n## |\Z)", text, re.DOTALL)
    assert m, f"section '{heading}' not found in file"
    return m.group(1)


# ── patch_protocol ──────────────────────────────────────────────────────────────

def test_patch_existing_section(npi):
    new_notes = NOTES + "\n- shipping labels"
    server.patch_protocol(npi, "Notes", new_notes)
    assert server.get_section(npi, "Notes") == new_notes


def test_patch_section_is_case_insensitive(npi):
    server.patch_protocol(npi, "nOtEs", "rewritten")
    assert server.get_section(npi, "Notes") == "rewritten"


def test_patch_missing_section_raises(npi):
    with pytest.raises(ValueError, match="Section 'Glossary' not found"):
        server.patch_protocol(npi, "Glossary", "x")
    # And it did not silently create the section.
    assert server._find_section_key(server._load(npi).sections, "Glossary") is None


def test_patch_missing_protocol_raises(plans_dir):
    with pytest.raises(FileNotFoundError, match="not found"):
        server.patch_protocol("does-not-exist", "Notes", "x")


def test_patch_non_protocol_plan_raises(plans_dir):
    server.create_plan(name="just-a-plan", title="Just A Plan", objective="do things")
    with pytest.raises(ValueError, match="is not a protocol"):
        server.patch_protocol("just-a-plan", "Objective", "x")


def test_patch_leaves_other_sections_byte_identical(npi):
    path = server._plan_path(npi)
    before = path.read_text()

    server.patch_protocol(npi, "Notes", NOTES + "\n- shipping labels")
    after = path.read_text()

    # Untouched sections and frontmatter are unchanged byte-for-byte.
    assert after.split("---", 2)[1] == before.split("---", 2)[1]  # frontmatter block
    for heading in ("Trigger", "Required Inputs", "Steps", "Output"):
        assert _section_block(after, heading) == _section_block(before, heading)
    # The targeted section did change.
    assert _section_block(after, "Notes") != _section_block(before, "Notes")


def test_patch_is_idempotent_noop(npi):
    path = server._plan_path(npi)
    server.patch_protocol(npi, "Steps", STEPS_14 + "\n15. Step 15")
    once = path.read_text()
    server.patch_protocol(npi, "Steps", server.get_section(npi, "Steps"))
    assert path.read_text() == once


# ── add_protocol_step ────────────────────────────────────────────────────────────

def test_add_protocol_step_continues_numbering(npi):
    server.add_protocol_step(npi, "Verify webhooks fire")
    assert server.get_section(npi, "Steps").splitlines()[-1] == "15. Verify webhooks fire"


def test_add_protocol_step_strips_caller_numbering(npi):
    server.add_protocol_step(npi, "15. Verify webhooks fire")
    assert server.get_section(npi, "Steps").splitlines()[-1] == "15. Verify webhooks fire"


def test_add_protocol_step_only_touches_steps(npi):
    path = server._plan_path(npi)
    before = path.read_text()
    server.add_protocol_step(npi, "Verify webhooks fire")
    after = path.read_text()
    for heading in ("Trigger", "Required Inputs", "Output", "Notes"):
        assert _section_block(after, heading) == _section_block(before, heading)


def test_add_step_falls_back_to_bullet_when_unnumbered(plans_dir):
    server.create_protocol(
        name="bullet-proto",
        title="Bullet Proto",
        trigger="x",
        inputs_description="i",
        steps="- first thing\n- second thing",
        output_description="o",
    )
    server.add_protocol_step("bullet-proto", "third thing")
    assert server.get_section("bullet-proto", "Steps").splitlines()[-1] == "- third thing"


def test_add_step_missing_steps_section_raises(plans_dir):
    # A protocol with no Steps section (build the file directly via the model).
    from praxis.models import Plan
    server._save(Plan(name="no-steps", title="No Steps", status="protocol",
                      sections={"Trigger": "x", "Notes": "y"}))
    with pytest.raises(ValueError, match="no 'Steps' section"):
        server.add_protocol_step("no-steps", "anything")


def test_add_step_non_protocol_raises(plans_dir):
    server.create_plan(name="plain", title="Plain")
    with pytest.raises(ValueError, match="is not a protocol"):
        server.add_protocol_step("plain", "anything")
