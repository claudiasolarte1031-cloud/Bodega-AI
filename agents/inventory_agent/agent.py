"""ADK entry point: builds `root_agent`, the object `adk web` and `adk run` look for.

Wiring:
    model                 -> MODEL_NAME from .env / environment (never hard-coded)
    instruction           -> prompt.INSTRUCTION
    tools                 -> tools.ALL_TOOLS (plain Python functions; ADK wraps them as FunctionTools)
    before_agent_callback -> workflow.remember_user_message (per-turn bookkeeping)
    before_tool_callback  -> workflow.guard_tool_call (tool allow-list and argument check)

Importing this module validates the configuration first, so a missing MODEL_NAME or
GOOGLE_API_KEY fails immediately with a message naming the variable, before any model call.
"""

from google.adk.agents import Agent

from .config import load_model_settings
from .prompt import AGENT_DESCRIPTION, INSTRUCTION
from .tools import ALL_TOOLS
from .workflow import guard_tool_call, remember_user_message

_settings = load_model_settings()

root_agent = Agent(
    name="inventory_agent",              # must be a valid identifier; shown in the ADK UI
    model=_settings.model_name,          # e.g. gemini-3.5-flash (ADK native Gemini integration)
    description=AGENT_DESCRIPTION,
    instruction=INSTRUCTION,
    tools=ALL_TOOLS,
    before_agent_callback=remember_user_message,
    before_tool_callback=guard_tool_call,
)
