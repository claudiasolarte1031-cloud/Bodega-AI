"""Inventory agent package.

`adk web agents` / `adk run agents/inventory_agent` discover this package and read
`inventory_agent.agent.root_agent`.

The `agent` sub-module is loaded lazily (on first attribute access) instead of with the usual
`from . import agent`. That lets tests and the data validator import `tools`, `store` and `models`
without google-adk installed and without MODEL_NAME / GOOGLE_API_KEY being set.
ADK still finds the agent because `hasattr(package, "agent")` triggers the import below.
"""

import importlib


def __getattr__(name):
    """Import `agent` only when someone asks for it (PEP 562 module __getattr__)."""
    if name == "agent":
        return importlib.import_module(f"{__name__}.agent")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
