"""Configuration loading for the inventory agent.

Settings come from the process environment first and then from the git-ignored `.env` file in the
project root. The `.env` path is resolved relative to THIS file, not the current working directory,
so the agent behaves the same whether it is started from the project root, from `agents/`, or by
pytest.

Required settings (checked only when the ADK agent is built, see `load_model_settings`):
    MODEL_NAME      Gemini model name, e.g. gemini-3.5-flash
    GOOGLE_API_KEY  Google AI Studio key (never printed or passed to the model)

Optional settings:
    INVENTORY_DATA_DIR       folder holding the JSON files (default: <project root>/data)
    INVENTORY_OPERATOR_NAME  name recorded in `performed_by` on stock movements
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# <project root>/agents/inventory_agent/config.py -> parents[2] is the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_OPERATOR = "Inventory Operator"

# override=False: variables already set in the process environment win over the .env file.
load_dotenv(ENV_FILE, override=False)


class ConfigError(RuntimeError):
    """Raised when a required setting is missing. The message names the variable, never its value."""


@dataclass(frozen=True)
class ModelSettings:
    """Settings the ADK agent needs before it can talk to Gemini."""

    model_name: str


def load_model_settings() -> ModelSettings:
    """Validate and return the model settings, failing fast with a clear message.

    Raises:
        ConfigError: when MODEL_NAME or GOOGLE_API_KEY is missing or blank.
    """
    model_name = os.getenv("MODEL_NAME", "").strip()
    if not model_name:
        raise ConfigError("MODEL_NAME is missing or blank. Set it in .env (see .env.example).")
    # The key is only checked for presence; its value stays in the environment where ADK reads it.
    if not os.getenv("GOOGLE_API_KEY", "").strip():
        raise ConfigError("GOOGLE_API_KEY is missing or blank. Set it in .env (see .env.example).")
    return ModelSettings(model_name=model_name)


def data_dir() -> Path:
    """Return the folder that holds inventory_db.json, suppliers.json and stock_movements.json.

    Read on every call (not cached) so tests can point INVENTORY_DATA_DIR at a temporary copy.
    """
    override = os.getenv("INVENTORY_DATA_DIR", "").strip()
    return Path(override) if override else DEFAULT_DATA_DIR


def operator_name() -> str:
    """Return the name written to `performed_by` on recorded stock movements."""
    return os.getenv("INVENTORY_OPERATOR_NAME", "").strip() or DEFAULT_OPERATOR
