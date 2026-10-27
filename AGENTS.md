# AGENTS.md — Inventory ADK agent

Instructions for AI coding agents (GitHub Copilot agent mode, Copilot coding agent and other
AGENTS.md-aware agents) working in this project. Always-on rules are in
`.github/copilot-instructions.md`; this file explains how to work here.

## Project in one paragraph

`inventory_agent` is a Google ADK agent (`gemini-3.5-flash`, set by `MODEL_NAME`) for a fictional
warehouse, Contoso Home Supplies. `data/` holds mock products, suppliers and a stock-movement audit
log as JSON. Seven read-only tools answer stock questions, report low stock with reorder suggestions,
summarise stock value and show history. Stock changes use a two-step flow: `propose_stock_movement`
stores a proposal in the session; `confirm_stock_movement` saves it only when the user's own message
in a later turn is exactly `Yes, confirm PROP-n`. `store.py` is the only writer of `data/`.

## Layout

See the tree in `README.md` → *Project layout*. Key files:

| File | Responsibility |
|---|---|
| `agents/inventory_agent/agent.py` | builds `root_agent`; reads the model from `MODEL_NAME` |
| `agents/inventory_agent/tools.py` | function tools; docstrings are written for the model |
| `agents/inventory_agent/workflow.py` | ADK callbacks and confirmation-phrase rules |
| `agents/inventory_agent/models.py` | Pydantic data models and tool output contracts |
| `agents/inventory_agent/store.py` | validated reads, atomic write with read-back |
| `scripts/validate_inventory_data.py` | cross-file consistency check (stdlib only) |

## Commands (from the project root, `.venv` active)

| Purpose | Command |
|---|---|
| Setup | `python setup_project.py` |
| Offline tests | `pytest` |
| Live model tests | `$env:RUN_LIVE_TESTS="1"; pytest -m live` |
| Validate data | `python scripts/validate_inventory_data.py` |
| Web UI | `adk web agents` → http://127.0.0.1:8000 → `inventory_agent` |
| Terminal chat | `adk run agents/inventory_agent` |

Install packages only into `.venv` (`uv pip install --python .venv\Scripts\python.exe ...`).

## How to work

1. **Adding a tool:** write the function in `tools.py` with a model-facing docstring, return a
   Pydantic result from `models.py` (or `error(...)`), add it to `ALL_TOOLS`, add its argument
   names to `ALLOWED_TOOL_ARGS` in `workflow.py`, mention it in `prompt.py`, and add tests.
   `test_every_registered_tool_is_allow_listed_with_its_real_arguments` fails until all agree.
2. **Any write to data/** goes through `InventoryStore` in `store.py`, never direct file writes.
3. **Data edits:** keep each SKU's latest movement equal to its `quantity_on_hand`; finish with the
   validator printing `OK`.
4. **Tests:** every new behaviour gets a pytest test using the `data_dir` fixture (temp copy).
5. **Decisions:** log any scope, data-shape, tooling or architecture decision in
   `project_decision.md` with the next free `D-0NN`.
6. **Report:** tell the user which files changed, the test and validator results, and blockers.

## Boundaries

- ✅ **Always:** keep data fictional (`*.example.com`, `555` numbers); UTC timestamps ending in `Z`;
  read the model from `MODEL_NAME`; return typed results instead of raising from tools.
- ⚠️ **Ask first:** changing JSON field names or file shapes; adding dependencies; adding tools
  that write data; deleting products or movements.
- 🚫 **Never:** weaken or bypass the confirmation rules in `workflow.py`/`tools.py`; let the model
  save without the exact phrase; invent stock numbers, prices or movement IDs; rewrite or delete
  movement history; add credentials or real personal data; open `.env` or other excluded files.

## Content exclusion

Do not open, read, summarise or quote `.env`, `.env.*` (except `.env.example`), `secrets/`,
`*.pem`, `*.key`, `*credentials*.json`, or anything under `data/real/`. If a task needs one of
these, stop and ask the user.
