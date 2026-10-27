"""Tests for the ADK callbacks (guards), configuration, prompt contract and data validator."""

import importlib
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agents.inventory_agent import config, prompt, tools, workflow
from conftest import PROJECT_ROOT


# --- guard_tool_call ---------------------------------------------------------------------------
def test_every_registered_tool_is_allow_listed_with_its_real_arguments():
    import inspect
    for fn in tools.ALL_TOOLS:
        params = set(inspect.signature(fn).parameters) - {"tool_context"}
        assert workflow.ALLOWED_TOOL_ARGS[fn.__name__] == params, fn.__name__
    assert set(workflow.ALLOWED_TOOL_ARGS) == {fn.__name__ for fn in tools.ALL_TOOLS}


def test_guard_allows_known_tool_and_arguments():
    tool = SimpleNamespace(name="get_product")
    assert workflow.guard_tool_call(tool, {"sku": "SKU-1001"}, None) is None


def test_guard_blocks_unknown_tool():
    result = workflow.guard_tool_call(SimpleNamespace(name="delete_product"), {}, None)
    assert result["error_code"] == "tool_not_allowed"


def test_guard_blocks_invented_arguments():
    tool = SimpleNamespace(name="confirm_stock_movement")
    result = workflow.guard_tool_call(tool, {"proposal_id": "PROP-1", "force": True}, None)
    assert result["error_code"] == "unexpected_arguments"


def test_remember_user_message_counts_turns():
    cb = SimpleNamespace(state={})
    workflow.remember_user_message(cb)
    workflow.remember_user_message(cb)
    assert cb.state[workflow.STATE_TURN_COUNT] == 2


@pytest.mark.parametrize("text,expected", [
    ("Yes, confirm PROP-1", "PROP-1"),
    ("yes confirm prop-12.", "PROP-12"),
    ("  YES,CONFIRM PROP-3 ! ", "PROP-3"),
    ("confirm PROP-1", None),
    ("Yes, confirm PROP-1 and PROP-2", None),
    ("no", None),
    ("", None),
])
def test_confirmation_phrase_parser(text, expected):
    assert workflow.confirmed_proposal_id(text) == expected


# --- configuration -----------------------------------------------------------------------------
def test_config_requires_model_name(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "  ")
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key-for-tests")
    with pytest.raises(config.ConfigError, match="MODEL_NAME"):
        config.load_model_settings()


def test_config_requires_api_key_without_revealing_values(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "gemini-test")
    monkeypatch.setenv("GOOGLE_API_KEY", "")
    with pytest.raises(config.ConfigError) as exc:
        config.load_model_settings()
    assert "GOOGLE_API_KEY" in str(exc.value) and "gemini-test" not in str(exc.value)


def test_config_returns_model_name(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "gemini-test")
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key-for-tests")
    assert config.load_model_settings().model_name == "gemini-test"


def test_data_dir_and_operator_defaults(monkeypatch):
    monkeypatch.delenv("INVENTORY_DATA_DIR", raising=False)
    monkeypatch.delenv("INVENTORY_OPERATOR_NAME", raising=False)
    assert config.data_dir() == PROJECT_ROOT / "data"
    assert config.operator_name() == "Inventory Operator"


# --- prompt contract ---------------------------------------------------------------------------
def test_prompt_mentions_every_tool_and_the_confirmation_rule():
    for fn in tools.ALL_TOOLS:
        assert fn.__name__ in prompt.INSTRUCTION
    assert "Yes, confirm PROP-1" in prompt.INSTRUCTION
    assert "same turn" in prompt.INSTRUCTION


def test_every_tool_has_a_docstring_for_the_model():
    for fn in tools.ALL_TOOLS:
        assert fn.__doc__ and len(fn.__doc__.strip()) > 40, fn.__name__


# --- data validator ----------------------------------------------------------------------------
def _validate(path):
    return subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "validate_inventory_data.py"), str(path)],
        capture_output=True, text=True,
    )


def test_validator_accepts_the_shipped_data():
    run = _validate(PROJECT_ROOT / "data")
    assert run.returncode == 0 and run.stdout.strip() == "OK"


def test_validator_catches_movement_and_stock_mismatch(data_dir):
    path = data_dir / "inventory_db.json"
    data = json.loads(path.read_text())
    next(p for p in data["products"] if p["sku"] == "SKU-1004")["quantity_on_hand"] = 10
    path.write_text(json.dumps(data))
    run = _validate(data_dir)
    assert run.returncode == 1 and "MOV-000002" in run.stdout


def test_validator_catches_unknown_supplier(data_dir):
    path = data_dir / "inventory_db.json"
    data = json.loads(path.read_text())
    data["products"][0]["supplier_id"] = "SUP-999"
    path.write_text(json.dumps(data))
    assert "unknown supplier_id" in _validate(data_dir).stdout


# --- ADK wiring (skipped when google-adk is not installed) -------------------------------------
def test_root_agent_is_built_from_environment(monkeypatch):
    pytest.importorskip("google.adk")
    monkeypatch.setenv("MODEL_NAME", "gemini-test-model")
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key-for-tests")
    module = importlib.reload(importlib.import_module("agents.inventory_agent.agent"))
    agent = module.root_agent
    assert agent.name == "inventory_agent"
    assert agent.model == "gemini-test-model"
    assert agent.before_tool_callback is workflow.guard_tool_call
    assert len(agent.tools) == len(tools.ALL_TOOLS)


def test_importing_tools_does_not_build_the_agent():
    """Tools must be importable without google-adk or model settings (lazy package __init__)."""
    code = "import sys, agents.inventory_agent.tools; print('agents.inventory_agent.agent' in sys.modules)"
    run = subprocess.run([sys.executable, "-c", code], cwd=PROJECT_ROOT, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "False"
