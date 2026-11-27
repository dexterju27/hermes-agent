"""Opt-in delivery obligations carried in the first line of a task body.

No new tool or completion_contract value: that field already owns PR acceptance.
Legacy tasks retain their existing notification behavior. The header is routing
metadata supplied by the delegating assistant, never execution authorization.
"""
from __future__ import annotations

import json
from typing import Any

PREFIX = "LUNA_HANDOFF_V1 "


def delivery_contract(task: Any) -> dict:
    body = getattr(task, "body", None)
    if not isinstance(body, str):
        return {}
    first = body.split("\n", 1)[0]
    if not first.startswith(PREFIX) or len(first) > 2048:
        return {}
    try:
        contract = json.loads(first[len(PREFIX):])
    except (ValueError, TypeError):
        return {}
    return contract if isinstance(contract, dict) and contract.get("branch") in {"B", "C"} else {}


def suppress_no_change(task: Any, event: Any, mode: str) -> bool:
    """Only an optional B update may be silent; ack-only C always owes a result."""
    contract = delivery_contract(task)
    if (mode != "wake" or event.kind != "completed" or contract.get("branch") != "B"
            or contract.get("initial_answer_provided") is not True or contract.get("reply_required") is not False):
        return False
    payload = event.payload if isinstance(event.payload, dict) else {}
    result = payload.get("summary") or getattr(task, "result", None)
    return isinstance(result, str) and result.strip() == "NO_NEW_FINDINGS"


def wake_guidance(task: Any) -> str:
    contract = delivery_contract(task)
    if not contract:
        return ""
    if contract["branch"] == "C" or contract.get("reply_required") is not False:
        return ("This is an owed asynchronous result: the user has not received a complete answer. "
                "Reply with the result, useful partial work, or a clear blocker. Do not respond with a silence marker. "
                "A completion event does not itself prove an external action succeeded; report only its evidence.")
    return ("The user already received a useful initial answer. Reply with only new actionable findings or corrections. "
            "Do not repeat the initial answer or re-run this task; a failure is not a no-change result.")
