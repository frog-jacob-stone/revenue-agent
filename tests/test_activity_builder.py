"""Unit tests for the server-side activity tree builder.

Mirrors the event sequences emitted by the streaming chat service so that
the persisted activity (used on chat reload) matches what the frontend
renders live from ChatWindow.tsx::onEvent.
"""
from app.services.activity_builder import (
    ActivityState,
    apply_event,
    label_for_kind,
    label_for_node,
)


def _build(events):
    activity: list[dict] = []
    state = ActivityState()
    for ev in events:
        apply_event(activity, state, ev)
    return activity


def test_label_for_node_uses_known_label():
    assert label_for_node("rev_rec_monthly", "compute_entries") == "Computing entries"


def test_label_for_node_falls_back_to_title_case():
    assert label_for_node("unknown_kind", "do_a_thing") == "Do A Thing"


def test_label_for_kind_uses_known_label():
    assert label_for_kind("content_creation") == "Content creation"


def test_label_for_kind_falls_back_to_title_case():
    assert label_for_kind("brand_new_thing") == "Brand New Thing"


def test_delta_events_dont_touch_activity():
    activity = _build([
        {"type": "delta", "text": "Hello"},
        {"type": "delta", "text": " world"},
    ])
    assert activity == []


def test_tool_call_started_then_completed_ok():
    activity = _build([
        {"type": "tool_call_started", "name": "create_post", "args": {}},
        {
            "type": "tool_call_completed",
            "name": "create_post",
            "ok": True,
            "result_summary": "{post_id…}",
        },
    ])
    assert len(activity) == 1
    line = activity[0]
    assert line["kind"] == "tool"
    assert line["label"] == "Calling create_post"
    assert line["status"] == "ok"
    assert line["detail"] == "{post_id…}"
    assert line["parentId"] is None


def test_tool_call_completed_failure_marks_status_fail():
    activity = _build([
        {"type": "tool_call_started", "name": "create_post", "args": {}},
        {
            "type": "tool_call_completed",
            "name": "create_post",
            "ok": False,
            "result_summary": "error: boom",
        },
    ])
    assert activity[0]["status"] == "fail"
    assert activity[0]["detail"] == "error: boom"


def test_full_workflow_sequence_nests_correctly():
    """End-to-end: tool -> workflow -> nodes (with sub-agent) -> completion."""
    activity = _build([
        {"type": "tool_call_started", "name": "trigger_revenue_recognition", "args": {}},
        {
            "type": "workflow_started",
            "workflow_id": "abc-123",
            "kind": "rev_rec_monthly",
        },
        {
            "type": "workflow_event",
            "event_type": "node.entered",
            "payload": {"node": "compute_entries"},
        },
        {
            "type": "workflow_event",
            "event_type": "agent.invoked",
            "actor": "orchestrator",
            "payload": {"agent_slug": "revenue-recognition"},
        },
        {
            "type": "workflow_event",
            "event_type": "agent.completed",
            "actor": "orchestrator",
            "payload": {"agent_slug": "revenue-recognition", "total_tokens": 1234},
        },
        {
            "type": "workflow_event",
            "event_type": "node.exited",
            "payload": {"node": "compute_entries"},
        },
        {
            "type": "workflow_event",
            "event_type": "workflow.completed",
            "payload": {},
        },
        {
            "type": "tool_call_completed",
            "name": "trigger_revenue_recognition",
            "ok": True,
            "result_summary": "{workflow_id…}",
        },
        {"type": "done", "answer": "Triggered.", "tool_used": "trigger_revenue_recognition"},
    ])

    kinds = [line["kind"] for line in activity]
    assert kinds == ["tool", "workflow", "node", "subagent", "node"]
    assert activity[0]["kind"] == "tool"
    assert activity[0]["status"] == "ok"  # patched by tool_call_completed
    assert activity[1]["kind"] == "workflow"
    assert activity[1]["parentId"] == activity[0]["id"]
    assert activity[1]["label"] == "Workflow: Revenue recognition"
    assert activity[1]["status"] == "ok"  # workflow.completed patched it

    # The "node.entered" line followed by "node.exited" line — both pushed.
    node_lines = [line for line in activity if line["kind"] == "node"]
    assert len(node_lines) == 2  # entered + exited (frontend behaviour preserved)
    assert all(line["parentId"] == activity[1]["id"] for line in node_lines)
    assert node_lines[0]["status"] == "running"
    assert node_lines[1]["status"] == "ok"
    assert node_lines[0]["label"] == "Computing entries"

    subagent = next(line for line in activity if line["kind"] == "subagent")
    # nested under the FIRST node line (the one open at time of agent.invoked)
    assert subagent["parentId"] == node_lines[0]["id"]
    assert subagent["label"] == "revenue-recognition"
    assert subagent["status"] == "ok"
    assert subagent["detail"] == "1.2k tokens"


def test_compact_tokens_under_1000():
    activity = _build([
        {"type": "tool_call_started", "name": "x", "args": {}},
        {"type": "workflow_started", "workflow_id": "w", "kind": "rev_rec_monthly"},
        {
            "type": "workflow_event",
            "event_type": "node.entered",
            "payload": {"node": "compute_entries"},
        },
        {
            "type": "workflow_event",
            "event_type": "agent.invoked",
            "payload": {"agent_slug": "x"},
        },
        {
            "type": "workflow_event",
            "event_type": "agent.completed",
            "payload": {"agent_slug": "x", "total_tokens": 500},
        },
    ])
    subagent = next(line for line in activity if line["kind"] == "subagent")
    assert subagent["detail"] == "500 tokens"


def test_workflow_failed_sets_fail_with_error_detail():
    activity = _build([
        {"type": "tool_call_started", "name": "trigger", "args": {}},
        {"type": "workflow_started", "workflow_id": "w", "kind": "rev_rec_monthly"},
        {
            "type": "workflow_event",
            "event_type": "workflow.failed",
            "payload": {"error": "Airtable timeout"},
        },
    ])
    wf_line = next(line for line in activity if line["kind"] == "workflow")
    assert wf_line["status"] == "fail"
    assert wf_line["detail"] == "Airtable timeout"


def test_error_event_pushes_top_level_error_line():
    activity = _build([
        {"type": "error", "message": "Something broke"},
    ])
    assert len(activity) == 1
    assert activity[0]["kind"] == "error"
    assert activity[0]["status"] == "fail"
    assert activity[0]["parentId"] is None
    assert activity[0]["label"] == "Something broke"


def test_workflow_paused_pushes_pause_line():
    activity = _build([
        {"type": "tool_call_started", "name": "trigger", "args": {}},
        {"type": "workflow_started", "workflow_id": "w", "kind": "outreach_chain"},
        {
            "type": "workflow_event",
            "event_type": "workflow.paused",
            "payload": {},
        },
    ])
    pause = next(line for line in activity if line["kind"] == "pause")
    assert pause["label"] == "Awaiting approval"
    assert pause["status"] == "ok"
