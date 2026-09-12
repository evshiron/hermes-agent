"""Agent-facing tools for persistent, per-session goals."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Optional

from hermes_cli.goals import GoalManager
from tools.registry import registry, tool_error


def _manager(session_id: Optional[str]) -> GoalManager:
    key = str(session_id or "").strip()
    if not key:
        raise ValueError("goal tools require a persistent session")
    return GoalManager(key)


def _payload(manager: GoalManager) -> dict[str, Any]:
    state = manager.state
    return {
        "session_id": manager.session_id,
        "goal": asdict(state) if state is not None else None,
    }


def create_goal(objective: str, *, session_id: Optional[str], max_turns: Optional[int] = None) -> str:
    """Create or replace the current session's goal."""
    manager = _manager(session_id)
    manager.set(objective, max_turns=max_turns)
    return json.dumps(_payload(manager), ensure_ascii=False)


def get_goal(*, session_id: Optional[str]) -> str:
    """Return the current session's goal state."""
    return json.dumps(_payload(_manager(session_id)), ensure_ascii=False)


def update_goal(action: str, *, session_id: Optional[str], reason: str = "") -> str:
    """Pause, resume, complete, or clear the current session's goal."""
    manager = _manager(session_id)
    normalized = str(action or "").strip().lower()
    if normalized == "pause":
        if manager.pause(reason=reason or "agent-paused") is None:
            return tool_error("No goal exists in this session.")
    elif normalized == "resume":
        if manager.resume(reset_budget=False) is None:
            return tool_error("No goal exists in this session.")
    elif normalized == "complete":
        if manager.state is None:
            return tool_error("No goal exists in this session.")
        manager.mark_done(reason or "completed by agent")
    elif normalized == "clear":
        if manager.state is None:
            return tool_error("No goal exists in this session.")
        manager.clear()
    else:
        return tool_error("action must be one of: pause, resume, complete, clear")
    return json.dumps(_payload(manager), ensure_ascii=False)


CREATE_GOAL_SCHEMA = {
    "name": "create_goal",
    "description": (
        "Create or replace the persistent goal for this conversation. Use when the user asks "
        "to establish a goal, north-star objective, or keep working autonomously toward an outcome."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "objective": {"type": "string", "description": "The concrete outcome to pursue."},
            "max_turns": {
                "type": "integer", "minimum": 1,
                "description": "Optional maximum autonomous turns before pausing.",
            },
        },
        "required": ["objective"],
    },
}

GET_GOAL_SCHEMA = {
    "name": "get_goal",
    "description": "Read the persistent goal and its current status for this conversation.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

UPDATE_GOAL_SCHEMA = {
    "name": "update_goal",
    "description": (
        "Change the current conversation goal lifecycle. Pause or resume ongoing work, mark an "
        "achieved goal complete, or clear a goal that should be discarded."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["pause", "resume", "complete", "clear"]},
            "reason": {"type": "string", "description": "Optional reason for the lifecycle change."},
        },
        "required": ["action"],
    },
}


registry.register(
    name="create_goal", toolset="goal", schema=CREATE_GOAL_SCHEMA,
    handler=lambda args, **kw: create_goal(
        args.get("objective") or "", session_id=kw.get("session_id"), max_turns=args.get("max_turns")),
    emoji="🎯")
registry.register(
    name="get_goal", toolset="goal", schema=GET_GOAL_SCHEMA,
    handler=lambda _args, **kw: get_goal(session_id=kw.get("session_id")), emoji="🎯")
registry.register(
    name="update_goal", toolset="goal", schema=UPDATE_GOAL_SCHEMA,
    handler=lambda args, **kw: update_goal(
        args.get("action") or "", session_id=kw.get("session_id"), reason=args.get("reason") or ""),
    emoji="🎯")

