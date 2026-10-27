"""Code-enforced guards for the inventory agent (ADK callbacks and confirmation rules).

The LLM decides WHICH tool to call, but the rules that protect the data are enforced here in
code, not in the prompt (see project_decision.md D-006):

1. `guard_tool_call` (ADK `before_tool_callback`) runs before every tool call. It refuses
   unknown tools and unexpected arguments, so the model cannot invent parameters.
2. Saving a stock movement is two-step. `propose_stock_movement` only validates and stores a
   proposal in the session. `confirm_stock_movement` saves it only when the USER's own message
   in the same turn is exactly the confirmation phrase, e.g. "Yes, confirm PROP-3", the proposal
   was created in an EARLIER turn, and it has not been used before.
3. `remember_user_message` (ADK `before_agent_callback`) counts the user's turns in the session
   state (useful for debugging in the ADK web UI "State" tab). Telling an earlier-turn proposal
   from one created in this turn uses ADK's `invocation_id`, which changes every turn.

Nothing in this module imports google-adk, so it is unit-testable with simple fake contexts.
"""

import re
from typing import Any, Dict, Optional

# Session-state keys (kept in one place so tools and tests agree).
STATE_PROPOSALS = "inventory_proposals"         # {proposal_id: MovementProposal dict}
STATE_PROPOSAL_COUNTER = "inventory_proposal_counter"
STATE_TURN_COUNT = "inventory_turn_count"

# Allowed tools and the exact argument names each one accepts.
# `tool_context` is injected by ADK and never appears in `args`.
ALLOWED_TOOL_ARGS: Dict[str, set] = {
    "list_products": {"category", "low_stock_only"},
    "search_products": {"query"},
    "get_product": {"sku"},
    "get_low_stock_report": set(),
    "get_inventory_summary": set(),
    "list_suppliers": set(),
    "list_stock_movements": {"sku", "limit"},
    "propose_stock_movement": {"sku", "movement_type", "quantity", "reason"},
    "confirm_stock_movement": {"proposal_id"},
}

# "Yes, confirm PROP-12" - case-insensitive, extra spaces and a trailing period/! allowed.
_CONFIRM_RE = re.compile(r"^\s*yes\s*,?\s*confirm\s+(PROP-\d+)\s*[.!]?\s*$", re.IGNORECASE)


def confirmation_phrase(proposal_id: str) -> str:
    """The exact phrase the user must type to save a proposal."""
    return f"Yes, confirm {proposal_id}"


def user_text(context: Any) -> str:
    """Return the plain text of the user's message for the current turn ('' if none).

    Works with ADK's CallbackContext/ToolContext (`user_content` is a google.genai Content with
    `parts`, each possibly having `.text`) and with the simple fakes used in tests.
    """
    content = getattr(context, "user_content", None)
    parts = getattr(content, "parts", None) or []
    return " ".join(p.text for p in parts if getattr(p, "text", None)).strip()


def confirmed_proposal_id(text: str) -> Optional[str]:
    """Return the proposal ID when `text` is exactly a confirmation phrase, otherwise None."""
    match = _CONFIRM_RE.match(text or "")
    return match.group(1).upper() if match else None


# ---------------------------------------------------------------------------------------------
# ADK callbacks
# ---------------------------------------------------------------------------------------------
def remember_user_message(callback_context: Any) -> None:
    """before_agent_callback: count turns in the session state.

    Returning None lets the agent run normally.
    """
    state = callback_context.state
    state[STATE_TURN_COUNT] = int(state.get(STATE_TURN_COUNT, 0) or 0) + 1
    return None


def guard_tool_call(tool: Any, args: Dict[str, Any], tool_context: Any) -> Optional[dict]:
    """before_tool_callback: refuse unknown tools and unexpected arguments.

    Returning a dict makes ADK skip the real tool and hand this dict to the model instead.
    Returning None lets the call proceed.
    """
    name = getattr(tool, "name", str(tool))
    if name not in ALLOWED_TOOL_ARGS:
        return {"status": "error", "error_code": "tool_not_allowed",
                "message": f"The tool {name!r} is not available to the inventory agent."}
    unexpected = set(args or {}) - ALLOWED_TOOL_ARGS[name]
    if unexpected:
        return {"status": "error", "error_code": "unexpected_arguments",
                "message": f"{name} does not accept: {', '.join(sorted(unexpected))}."}
    return None
