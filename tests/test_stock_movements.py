"""Tests for the two-step write flow and the code-enforced confirmation rules."""

import json
import subprocess
import sys

from agents.inventory_agent import tools
from agents.inventory_agent.models import MovementProposalResult, MovementSavedResult
from conftest import PROJECT_ROOT


def _on_hand(data_dir, sku):
    products = json.loads((data_dir / "inventory_db.json").read_text())["products"]
    return next(p["quantity_on_hand"] for p in products if p["sku"] == sku)


def _movement_count(data_dir):
    return len(json.loads((data_dir / "stock_movements.json").read_text())["movements"])


def _propose(ctx, **kwargs):
    ctx.say("receive 20 goggles")                        # user turn that leads to the proposal
    args = dict(sku="SKU-1004", movement_type="receive", quantity=20, reason="PO 4471 delivered")
    args.update(kwargs)
    return tools.propose_stock_movement(tool_context=ctx, **args)


# --- propose ---------------------------------------------------------------------------------
def test_propose_does_not_write_and_returns_phrase(data_dir, ctx):
    result = _propose(ctx)
    MovementProposalResult.model_validate(result)
    assert result["proposal"]["quantity_before"] == 9
    assert result["proposal"]["quantity_after"] == 29
    assert result["confirmation_phrase"] == "Yes, confirm PROP-1"
    assert _on_hand(data_dir, "SKU-1004") == 9               # nothing saved
    assert _movement_count(data_dir) == 4


def test_propose_rejects_bad_input(data_dir, ctx):
    assert _propose(ctx, movement_type="sell")["error_code"] == "invalid_movement_type"
    assert _propose(ctx, quantity=0)["error_code"] == "invalid_quantity"
    assert _propose(ctx, quantity=-3)["error_code"] == "invalid_quantity"      # receive must be > 0
    assert _propose(ctx, reason="  ")["error_code"] == "reason_required"
    assert _propose(ctx, sku="SKU-0000")["error_code"] == "product_not_found"
    assert _propose(ctx, movement_type="issue", quantity=10)["error_code"] == "insufficient_stock"


def test_issue_and_adjust_use_signed_changes(data_dir, ctx):
    assert _propose(ctx, movement_type="issue", quantity=4)["proposal"]["quantity_change"] == -4
    assert _propose(ctx, movement_type="adjust", quantity=-2)["proposal"]["quantity_after"] == 7


# --- confirm ---------------------------------------------------------------------------------
def test_exact_phrase_in_later_turn_saves_once(data_dir, ctx):
    pid = _propose(ctx)["proposal"]["proposal_id"]
    ctx.say(f"yes,  confirm {pid.lower()}!")                 # case/spacing tolerant
    result = tools.confirm_stock_movement(pid, tool_context=ctx)
    MovementSavedResult.model_validate(result)
    assert result["movement"]["movement_id"] == "MOV-000005"
    assert result["movement"]["performed_by"] == "Test Operator"
    assert result["product"]["quantity_on_hand"] == 29
    assert result["low_stock_after_change"] is False
    assert _on_hand(data_dir, "SKU-1004") == 29
    assert _movement_count(data_dir) == 5

    # Same phrase again: refused, no second write.
    ctx.say(f"Yes, confirm {pid}")
    assert tools.confirm_stock_movement(pid, tool_context=ctx)["error_code"] == "proposal_already_used"
    assert _on_hand(data_dir, "SKU-1004") == 29


def test_vague_approval_is_refused(data_dir, ctx):
    pid = _propose(ctx)["proposal"]["proposal_id"]
    for reply in ("ok", "sí", "yes", "do it", "confirm", f"yes confirm {pid} please", "Yes, confirm PROP-99"):
        ctx.say(reply)
        assert tools.confirm_stock_movement(pid, tool_context=ctx)["error_code"] == "confirmation_required", reply
    assert _on_hand(data_dir, "SKU-1004") == 9


def test_cannot_confirm_in_the_same_turn_as_the_proposal(data_dir, ctx):
    # Even if the user's message already contained the phrase, the proposal must be shown first.
    ctx.say("Yes, confirm PROP-1")
    tools.propose_stock_movement("SKU-1004", "receive", 5, "test", tool_context=ctx)
    assert tools.confirm_stock_movement("PROP-1", tool_context=ctx)["error_code"] == "confirmation_required"
    assert _on_hand(data_dir, "SKU-1004") == 9


def test_unknown_proposal(data_dir, ctx):
    ctx.say("Yes, confirm PROP-7")
    assert tools.confirm_stock_movement("PROP-7", tool_context=ctx)["error_code"] == "proposal_not_found"


def test_stale_proposal_is_refused(data_dir, ctx):
    pid = _propose(ctx)["proposal"]["proposal_id"]
    # Someone else changes the stock between proposal and confirmation.
    path = data_dir / "inventory_db.json"
    data = json.loads(path.read_text())
    next(p for p in data["products"] if p["sku"] == "SKU-1004")["quantity_on_hand"] = 3
    path.write_text(json.dumps(data))
    ctx.say(f"Yes, confirm {pid}")
    assert tools.confirm_stock_movement(pid, tool_context=ctx)["error_code"] == "stale_proposal"
    assert _movement_count(data_dir) == 4


def test_saved_data_still_passes_the_validator(data_dir, ctx):
    pid = _propose(ctx, movement_type="issue", quantity=9)["proposal"]["proposal_id"]
    ctx.say(f"Yes, confirm {pid}")
    assert tools.confirm_stock_movement(pid, tool_context=ctx)["low_stock_after_change"] is True
    run = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "validate_inventory_data.py"), str(data_dir)],
        capture_output=True, text=True,
    )
    assert run.returncode == 0, run.stdout
    assert run.stdout.strip() == "OK"
    assert not list(data_dir.glob("*.tmp")) and not list(data_dir.glob(".*.tmp"))   # no temp leftovers
