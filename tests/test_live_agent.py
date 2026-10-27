"""Opt-in live test against the real Gemini model through the real ADK runner.

Runs only when RUN_LIVE_TESTS=1 and a real GOOGLE_API_KEY is configured; otherwise skipped.
Uses a temporary copy of data/ (fixture `data_dir`), so the real files are never changed.

    PowerShell:  $env:RUN_LIVE_TESTS="1"; pytest -m live
"""

import asyncio
import importlib
import json
import os

import pytest

pytestmark = pytest.mark.live


def _require_live():
    if os.getenv("RUN_LIVE_TESTS") != "1":
        pytest.skip("set RUN_LIVE_TESTS=1 to call the real model")
    pytest.importorskip("google.adk")
    from agents.inventory_agent import config  # loads .env
    if not os.getenv("GOOGLE_API_KEY", "").strip() or not os.getenv("MODEL_NAME", "").strip():
        pytest.skip("MODEL_NAME and GOOGLE_API_KEY are required for live tests")
    return config


async def _conversation(messages):
    """Send each message in one session; return (list of reply texts, list of tool names called)."""
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    agent_module = importlib.reload(importlib.import_module("agents.inventory_agent.agent"))
    runner = InMemoryRunner(agent=agent_module.root_agent, app_name="inventory_live_test")
    session = await runner.session_service.create_session(app_name="inventory_live_test", user_id="tester")
    replies, tool_calls = [], []
    for text in messages:
        reply = []
        async for event in runner.run_async(
            user_id="tester", session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=text)]),
        ):
            for call in event.get_function_calls() or []:
                tool_calls.append(call.name)
            if event.content and event.content.parts:
                reply.extend(p.text for p in event.content.parts if getattr(p, "text", None))
        replies.append("".join(reply))
    return replies, tool_calls


def test_live_low_stock_question_uses_the_tool(data_dir):
    _require_live()
    replies, calls = asyncio.run(_conversation(["Which products are low on stock?"]))
    assert any(c in calls for c in ("get_low_stock_report", "list_products"))
    assert "SKU-1008" in replies[-1]


def test_live_stock_change_needs_exact_confirmation(data_dir):
    _require_live()
    replies, calls = asyncio.run(_conversation([
        "Receive 20 units of SKU-1004, reason: PO 4471 delivered",
        "ok",                                   # vague approval: must not save
    ]))
    assert "propose_stock_movement" in calls
    assert "PROP-1" in replies[0]
    products = json.loads((data_dir / "inventory_db.json").read_text())["products"]
    assert next(p for p in products if p["sku"] == "SKU-1004")["quantity_on_hand"] == 9
