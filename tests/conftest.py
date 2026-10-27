"""Shared pytest fixtures.

Every test works on a TEMPORARY COPY of data/, so the real mock data is never changed.
`FakeToolContext` imitates the parts of ADK's ToolContext the tools use (state, invocation_id,
user_content), so tool and guard tests run without google-adk or a model.
"""

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_DATA = PROJECT_ROOT / "data"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Copy data/ to a temp folder and point the agent at it via INVENTORY_DATA_DIR."""
    target = tmp_path / "data"
    shutil.copytree(REAL_DATA, target)
    monkeypatch.setenv("INVENTORY_DATA_DIR", str(target))
    monkeypatch.setenv("INVENTORY_OPERATOR_NAME", "Test Operator")
    return target


class FakeToolContext:
    """Minimal stand-in for google.adk.tools.ToolContext.

    `say(text)` simulates a new user turn: it sets the message and changes the invocation id,
    exactly like ADK does for each new user message.
    """

    def __init__(self):
        self.state = {}
        self._turn = 0
        self.invocation_id = "inv-0"
        self.user_content = None

    def say(self, text: str) -> "FakeToolContext":
        self._turn += 1
        self.invocation_id = f"inv-{self._turn}"
        self.user_content = SimpleNamespace(parts=[SimpleNamespace(text=text)])
        return self


@pytest.fixture
def ctx():
    """A fresh fake tool context (one conversation)."""
    return FakeToolContext()
