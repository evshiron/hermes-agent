"""Agent goal tools share the persisted per-session GoalManager state."""

import json

from hermes_cli import goals
from tools.goal_tool import create_goal, get_goal, update_goal


def test_goal_tools_create_read_and_update_the_same_session():
    goals._DB_CACHE.clear()
    created = json.loads(create_goal("ship the release", session_id="goal-tool-session", max_turns=7))
    assert created["goal"]["goal"] == "ship the release"
    assert created["goal"]["max_turns"] == 7

    assert json.loads(get_goal(session_id="goal-tool-session"))["goal"]["status"] == "active"
    assert json.loads(update_goal("pause", session_id="goal-tool-session"))["goal"]["status"] == "paused"
    assert json.loads(update_goal("resume", session_id="goal-tool-session"))["goal"]["status"] == "active"
    completed = json.loads(update_goal(
        "complete", session_id="goal-tool-session", reason="verified"))
    assert completed["goal"]["status"] == "done"
    assert completed["goal"]["last_reason"] == "verified"


def test_goal_tools_are_isolated_by_session():
    goals._DB_CACHE.clear()
    create_goal("only alpha", session_id="goal-alpha")
    assert json.loads(get_goal(session_id="goal-alpha"))["goal"]["goal"] == "only alpha"
    assert json.loads(get_goal(session_id="goal-beta"))["goal"] is None
