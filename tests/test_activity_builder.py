"""Unit tests for the server-side activity tree builder.

Mirrors the event sequences emitted by the streaming chat service so that
the persisted activity (used on chat reload) matches what the frontend
renders live from ChatWindow.tsx::onEvent.
"""
from app.services.activity_builder import (
    ActivityState,
    apply_event,
    label_for_tool_step,
)


def _build(events):
    activity: list[dict] = []
    state = ActivityState()
    for ev in events:
        apply_event(activity, state, ev)
    return activity


def test_label_for_tool_step_uses_known_label():
    label = label_for_tool_step("trigger_revenue_recognition", "compute_entries")
    assert label == "Computing entries"


def test_label_for_tool_step_falls_back_to_title_case():
    assert label_for_tool_step("unknown_tool", "do_a_thing") == "Do A Thing"


def test_delta_events_dont_touch_activity():
    activity = _build([
        {"type": "delta", "text": "Hello"},
        {"type": "delta", "text": " world"},
    ])
    assert activity == []


def test_tool_call_started_then_completed_ok():
    activity = _build([
        {"type": "tool_call_started", "name": "trigger_revenue_recognition", "args": {}},
        {
            "type": "tool_call_completed",
            "name": "trigger_revenue_recognition",
            "ok": True,
            "result_summary": "{entry_count…}",
        },
    ])
    assert len(activity) == 1
    line = activity[0]
    assert line["kind"] == "tool"
    assert line["label"] == "Calling trigger_revenue_recognition"
    assert line["status"] == "ok"
    assert line["detail"] == "{entry_count…}"
    assert line["parentId"] is None


def test_tool_call_completed_failure_marks_status_fail():
    activity = _build([
        {"type": "tool_call_started", "name": "trigger_revenue_recognition", "args": {}},
        {
            "type": "tool_call_completed",
            "name": "trigger_revenue_recognition",
            "ok": False,
            "result_summary": "error: boom",
        },
    ])
    assert activity[0]["status"] == "fail"
    assert activity[0]["detail"] == "error: boom"


def test_tool_step_events_nest_under_tool():
    activity = _build([
        {"type": "tool_call_started", "name": "trigger_revenue_recognition", "args": {}},
        {"type": "tool_step_started", "name": "validate_and_sync"},
        {"type": "tool_step_completed", "name": "validate_and_sync", "ok": True},
        {"type": "tool_step_started", "name": "compute_entries"},
        {"type": "tool_step_completed", "name": "compute_entries", "ok": True},
        {
            "type": "tool_call_completed",
            "name": "trigger_revenue_recognition",
            "ok": True,
            "result_summary": "{entry_count…}",
        },
    ])

    kinds = [line["kind"] for line in activity]
    assert kinds == ["tool", "node", "node"]
    tool_line, step1, step2 = activity
    assert step1["parentId"] == tool_line["id"]
    assert step2["parentId"] == tool_line["id"]
    assert step1["label"] == "Validating and syncing"
    assert step1["status"] == "ok"
    assert step2["label"] == "Computing entries"
    assert step2["status"] == "ok"
    assert tool_line["status"] == "ok"


def test_tool_step_completed_with_ok_false_marks_fail():
    activity = _build([
        {"type": "tool_call_started", "name": "trigger_revenue_recognition", "args": {}},
        {"type": "tool_step_started", "name": "compute_entries"},
        {"type": "tool_step_completed", "name": "compute_entries", "ok": False},
    ])
    step = next(line for line in activity if line["kind"] == "node")
    assert step["status"] == "fail"


def test_agent_task_tool_events_nest_under_parent_tool():
    activity = _build([
        {"type": "tool_call_started", "name": "ask_agent", "args": {}},
        {
            "type": "agent_task_tool_started",
            "agent_slug": "revenue-ops",
            "name": "get_revenue_data",
            "args": {},
        },
        {
            "type": "agent_task_tool_completed",
            "agent_slug": "revenue-ops",
            "name": "get_revenue_data",
            "ok": True,
            "result_summary": "{records…}",
        },
        {
            "type": "tool_call_completed",
            "name": "ask_agent",
            "ok": True,
            "result_summary": "{answer…}",
        },
    ])
    nested = next(line for line in activity if line["kind"] == "tool" and line["parentId"])
    parent = next(line for line in activity if line["parentId"] is None)
    assert nested["parentId"] == parent["id"]
    assert nested["label"] == "revenue-ops → get_revenue_data"
    assert nested["status"] == "ok"
    assert nested["detail"] == "{records…}"


def test_error_event_pushes_top_level_error_line():
    activity = _build([
        {"type": "error", "message": "Something broke"},
    ])
    assert len(activity) == 1
    assert activity[0]["kind"] == "error"
    assert activity[0]["status"] == "fail"
    assert activity[0]["parentId"] is None
    assert activity[0]["label"] == "Something broke"
