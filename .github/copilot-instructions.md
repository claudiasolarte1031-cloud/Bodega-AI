# Copilot instructions — Inventory ADK agent

- This project is a Google ADK agent in `agents/inventory_agent/` using Gemini via `MODEL_NAME`.
  Never hard-code a model name or API key.
- All data in `data/` is fictional. Keep emails on `*.example.com` and timestamps in UTC with `Z`.
- Tools live in `tools.py`, return Pydantic results from `models.py` (or `error(...)`), and never
  raise to the model. Every new tool must also be added to `ALL_TOOLS`, to `ALLOWED_TOOL_ARGS` in
  `workflow.py` and to the prompt, with tests.
- Only `store.py` writes to `data/`. Writes are atomic and verified by re-reading.
- Stock changes require `propose_stock_movement` and then the user's exact
  `Yes, confirm PROP-n` in a later turn. Do not weaken these rules.
- Comment code for a reader new to ADK: explain why, not only what.
- After any change run `pytest` and `python scripts/validate_inventory_data.py`.
- Record decisions in `project_decision.md` (next free `D-0NN`, never renumber).
- Install packages only into `.venv`.
