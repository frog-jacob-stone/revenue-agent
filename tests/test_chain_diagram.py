"""Tests for app.orchestrator.diagram.

Chain definitions are registered at module import time. Importing
`app.orchestrator.chains` triggers `register_all()` so `get_chain(...)` works.
"""
from __future__ import annotations

import pytest

from app.orchestrator.chain import get_chain
from app.orchestrator.chains import register_all
from app.orchestrator.diagram import chain_to_dict, chain_to_mermaid
from app.orchestrator.steps import CritiqueStep


@pytest.fixture(scope="module", autouse=True)
def _ensure_chains_registered() -> None:
    register_all()


# -----------------------------------------------------------------------------
# Pinned snapshot — rev_rec_monthly (most complex; covers skip_if collapsing
# and on_approve_label rendering). Re-bless when the chain changes.
# -----------------------------------------------------------------------------

REV_REC_EXPECTED = """\
flowchart TD
    start([Start])
    done([Done])
    s0["Task: Sync Harvest → Airtable and validate projects"]
    s1[/"Checkpoint: Configure incomplete projects<br/>on approve: requeue validation"/]
    s2["Task: Compute revenue entries"]
    s3[/"Execution: Write revenue recognition entries to Airtable"/]
    g1{"validation passed?"}
    g2{"validation failed?"}
    start --> s0
    s0 --> g1
    g1 -- "validation passed" --> g2
    g1 -- "no" --> s1
    s1 --> g2
    g2 -- "validation failed" --> done
    g2 -- "no" --> s2
    s2 --> s3
    s3 --> done
"""


def test_rev_rec_monthly_snapshot() -> None:
    chain = get_chain("rev_rec_monthly")
    assert chain_to_mermaid(chain) == REV_REC_EXPECTED


# -----------------------------------------------------------------------------
# Structural assertions — all four registered chains
# -----------------------------------------------------------------------------

ALL_KINDS = ["content_creation", "content_publish", "outreach_chain", "rev_rec_monthly"]


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_starts_with_flowchart_header(kind: str) -> None:
    src = chain_to_mermaid(get_chain(kind))
    assert src.splitlines()[0] == "flowchart TD"


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_has_terminal_nodes(kind: str) -> None:
    src = chain_to_mermaid(get_chain(kind))
    assert "start([Start])" in src
    assert "done([Done])" in src


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_has_one_node_per_step(kind: str) -> None:
    chain = get_chain(kind)
    src = chain_to_mermaid(chain)
    for i in range(len(chain.steps)):
        # Each step's node id appears at least once in a definition position.
        assert any(
            line.lstrip().startswith(f"s{i}[") or line.lstrip().startswith(f"s{i}{{")
            for line in src.splitlines()
        ), f"step s{i} not defined in {kind} diagram"


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_critique_back_edges(kind: str) -> None:
    """Every CritiqueStep must emit a 'fail (retry)' edge to its target step
    and a 'budget exhausted' edge to the fail terminal."""
    chain = get_chain(kind)
    src = chain_to_mermaid(chain)
    for i, step in enumerate(chain.steps):
        if not isinstance(step, CritiqueStep):
            continue
        target_id = f"s{step.critiques_step_index}"
        assert f's{i} -- "fail (retry)" --> {target_id}' in src
        assert f's{i} -- "budget exhausted" --> fail' in src
        assert f's{i} -- "pass" --> ' in src
        # Fail node only emitted when at least one critique exists
        assert "fail([Failed])" in src


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_skip_if_emits_labeled_gate(kind: str) -> None:
    """Each step with skip_if must be reachable via a labeled diamond gate
    (unless it shares its label with the immediately prior step, in which
    case the prior gate covers it)."""
    chain = get_chain(kind)
    src = chain_to_mermaid(chain)
    last_label: str | None = None
    for i, step in enumerate(chain.steps):
        if step.skip_if is None:
            last_label = None
            continue
        label = step.skip_if_label or "may skip"
        if label == last_label:
            # Collapsed under previous gate; nothing new expected.
            continue
        # A new gate must exist with this label.
        assert f'g{i}{{"{label}?"}}' in src
        assert f'g{i} -- "{label}" --> ' in src
        assert f'g{i} -- "no" --> s{i}' in src
        last_label = label


def test_no_critique_no_fail_node() -> None:
    """Chains without any CritiqueStep must not emit the Failed terminal."""
    src = chain_to_mermaid(get_chain("content_publish"))
    assert "fail([Failed])" not in src


def test_outreach_has_two_back_edges_to_step_4() -> None:
    """Outreach has voice + accuracy critics, both targeting the draft step."""
    src = chain_to_mermaid(get_chain("outreach_chain"))
    assert 's5 -- "fail (retry)" --> s4' in src
    assert 's6 -- "fail (retry)" --> s4' in src


# -----------------------------------------------------------------------------
# chain_to_dict
# -----------------------------------------------------------------------------


def test_chain_to_dict_rev_rec_shape() -> None:
    d = chain_to_dict(get_chain("rev_rec_monthly"))
    assert d["kind"] == "rev_rec_monthly"
    assert d["pattern"] == "supervised_automation"
    assert d["agent_slug"] == "revenue-recognition"
    assert d["step_count"] == 4
    assert len(d["steps"]) == 4

    checkpoint = d["steps"][1]
    assert checkpoint["kind"] == "checkpoint"
    assert checkpoint["has_skip_if"] is True
    assert checkpoint["skip_if_label"] == "validation passed"
    assert checkpoint["on_approve_label"] == "requeue validation"
    assert checkpoint["has_on_approve_callback"] is True
    assert checkpoint["action_type"] == "configure_rev_rec_projects"

    write = d["steps"][3]
    assert write["kind"] == "execution"
    assert write["skip_if_label"] == "validation failed"


def test_chain_to_dict_outreach_critics() -> None:
    d = chain_to_dict(get_chain("outreach_chain"))
    voice = d["steps"][5]
    accuracy = d["steps"][6]
    assert voice["kind"] == "critique"
    assert voice["critiques_step_index"] == 4
    assert voice["max_attempts"] == 3
    assert accuracy["kind"] == "critique"
    assert accuracy["critiques_step_index"] == 4
    assert accuracy["max_attempts"] == 2


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_chain_to_dict_step_count_matches(kind: str) -> None:
    chain = get_chain(kind)
    d = chain_to_dict(chain)
    assert d["step_count"] == len(chain.steps)
    assert len(d["steps"]) == len(chain.steps)
    for i, sd in enumerate(d["steps"]):
        assert sd["index"] == i


# -----------------------------------------------------------------------------
# /chains API endpoints
# -----------------------------------------------------------------------------


async def test_list_chains_returns_all_four(client) -> None:
    resp = await client.get("/chains")
    assert resp.status_code == 200
    kinds = {row["kind"] for row in resp.json()}
    assert kinds == set(ALL_KINDS)


async def test_list_chains_filters_by_agent_slug(client) -> None:
    resp = await client.get("/chains", params={"agent_slug": "revenue-recognition"})
    assert resp.status_code == 200
    kinds = [row["kind"] for row in resp.json()]
    assert kinds == ["rev_rec_monthly"]


async def test_get_chain_structure(client) -> None:
    resp = await client.get("/chains/rev_rec_monthly")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pattern"] == "supervised_automation"
    assert body["step_count"] == 4
    assert body["steps"][1]["skip_if_label"] == "validation passed"
    assert body["steps"][1]["has_on_approve_callback"] is True


async def test_get_chain_structure_404(client) -> None:
    resp = await client.get("/chains/does_not_exist")
    assert resp.status_code == 404


async def test_get_chain_diagram_is_plain_text(client) -> None:
    resp = await client.get("/chains/outreach_chain/diagram")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert body.startswith("flowchart TD")
    # Two critique back-edges to the draft step
    assert 's5 -- "fail (retry)" --> s4' in body
    assert 's6 -- "fail (retry)" --> s4' in body
