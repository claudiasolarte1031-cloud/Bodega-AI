# Project decisions — Inventory ADK agent

This file records every significant decision on this project: what was decided, why, what else was
considered, and what follows from it. Newest decisions are at the bottom.

## How to use this log

- **Add** an entry for every decision that changes scope, data shape, tooling, architecture,
  conventions or process. Use the next free ID (`D-0NN`). Never reuse or renumber IDs.
- **Don't rewrite history.** If a decision changes, add a new entry and set the old one's status to
  `Superseded by D-0NN`. Small clarifications go under *Updates*.
- **Status** is `Proposed`, `Accepted`, `Superseded by D-0NN` or `Rejected`.

### Entry template

```markdown
## D-0NN — <short title>
- **Date:** YYYY-MM-DD
- **Status:** Proposed | Accepted | Superseded by D-0NN | Rejected
- **Decided by:** <person or role> (<how>)
- **Context:** Why a decision was needed.
- **Decision:** What was decided.
- **Alternatives considered:** Options rejected and why.
- **Consequences:** What follows, including follow-up work.
- **Related:** Files, other decisions.
```

## Index

| ID | Title | Status | Date |
|---|---|---|---|
| D-001 | Fictional warehouse inventory as the project domain | Accepted | 2026-10-26 |
| D-002 | Same tooling as the ShipTrack project (ADK, uv, .env, pytest) | Accepted | 2026-10-26 |
| D-003 | One ADK agent with direct function tools; no MCP server yet | Proposed | 2026-10-26 |
| D-004 | JSON files as the data store, with a movement audit log | Proposed | 2026-10-26 |
| D-005 | Single writer, atomic writes and read-back verification | Accepted | 2026-10-26 |
| D-006 | Two-step stock changes confirmed by an exact phrase, enforced in code | Accepted | 2026-10-26 |
| D-007 | Pydantic output contracts for every tool result | Accepted | 2026-10-26 |
| D-008 | Reorder suggestion rule and advice-only scope | Proposed | 2026-10-26 |
| D-009 | Offline tests on a temp copy; live tests opt-in | Accepted | 2026-10-26 |

---

## D-001 — Fictional warehouse inventory as the project domain
- **Date:** 2026-10-26
- **Status:** Accepted
- **Decided by:** Project owner (asked for an inventory agent); details chosen by Claude
- **Context:** The owner wanted an inventory agent built like the ShipTrack CAB project.
- **Decision:** Model a fictional warehouse, *Contoso Home Supplies*, with 10 SKUs in four
  categories (Tools, Safety, Packaging, Electrical), 3 suppliers and 4 seed movements. Seed data
  includes low-stock and out-of-stock items so every feature is visible in a demo.
- **Alternatives considered:** Retail store POS inventory (needs sales/pricing logic); restaurant
  inventory (expiry dates add scope).
- **Consequences:** All names, emails (`*.example.com`) and phones (`555`) are invented.
- **Related:** `data/`

## D-002 — Same tooling as the ShipTrack project (ADK, uv, .env, pytest)
- **Date:** 2026-10-26
- **Status:** Accepted
- **Decided by:** Project owner (asked to mirror the shared files)
- **Context:** The team already knows the ShipTrack setup.
- **Decision:** Google ADK with `gemini-3.5-flash` from `MODEL_NAME`; secrets only in a git-ignored
  `.env`; `setup_project.py` creates `.venv` with uv and installs into it with an explicit
  `--python`; pytest with a `live` marker; `AGENTS.md`, Copilot instructions and this decision log.
  Dependencies: `google-adk`, `pydantic>=2`, `python-dotenv`, `pytest`. `litellm` and `mcp` are
  not included because nothing uses them yet.
- **Alternatives considered:** Copying ShipTrack's full requirements; rejected to avoid unused packages.
  OpenSpec was not set up; add it as a new decision if the team wants spec-driven changes here.
- **Consequences:** Versions are unpinned; pin them (or move to `uv.lock`) before a shared demo.
- **Related:** `requirements.txt`, `setup_project.py`, `.env.example`

## D-003 — One ADK agent with direct function tools; no MCP server yet
- **Date:** 2026-10-26
- **Status:** Proposed — awaiting project owner confirmation
- **Decided by:** Claude (initial architecture)
- **Context:** The data is local JSON and there is a single agent.
- **Decision:** Register plain Python functions as ADK tools. Keep all file access in `store.py`
  so it can later move behind an MCP server without changing tool behaviour.
- **Alternatives considered:** A loopback MCP server like ShipTrack's release-task store; adds a
  second process to start with no benefit until other agents need the same data.
- **Consequences:** Only `adk web` needs to run. Revisit when a second agent (for example a
  purchasing agent) needs inventory access.
- **Related:** `agents/inventory_agent/tools.py`, `store.py`

## D-004 — JSON files as the data store, with a movement audit log
- **Date:** 2026-10-26
- **Status:** Proposed — awaiting project owner confirmation
- **Decided by:** Claude
- **Context:** A demo needs persistent, inspectable data without a database server.
- **Decision:** `inventory_db.json` holds current stock; `stock_movements.json` is an append-only log
  with signed `quantity_change`, before/after quantities, reason, operator and UTC timestamp.
  The latest movement per SKU must match `quantity_on_hand` (checked by the validator).
- **Alternatives considered:** SQLite (better concurrency, less readable in a demo); stock derived
  only from the log (slower to read, harder to edit by hand).
- **Consequences:** Single-process use only. Move to SQLite or a real service before multi-user use.
- **Related:** `data/`, `scripts/validate_inventory_data.py`

## D-005 — Single writer, atomic writes and read-back verification
- **Date:** 2026-10-26
- **Status:** Accepted
- **Decided by:** Claude (same pattern as ShipTrack D-035)
- **Context:** A failed or partial write must never corrupt the data or be reported as success.
- **Decision:** `InventoryStore.apply_movement` is the only write. It holds a lock, validates the
  whole file first, refuses negative stock and stale proposals, writes via temp file + `os.replace`
  (log first, then products), re-reads both files and reports success only if they match.
  A malformed file is reported as `data_invalid` and never overwritten.
- **Alternatives considered:** Writing in place (can leave half-written JSON).
- **Consequences:** If the process dies between the two replaces, the validator reports the mismatch.
- **Related:** `agents/inventory_agent/store.py`

## D-006 — Two-step stock changes confirmed by an exact phrase, enforced in code
- **Date:** 2026-10-26
- **Status:** Accepted
- **Decided by:** Claude (same principle as ShipTrack D-038/D-046)
- **Context:** A model may misread "ok" or act on its own; stock changes must be deliberate.
- **Decision:** `propose_stock_movement` validates and stores a proposal in session state.
  `confirm_stock_movement` saves only if the user's own message in that turn is exactly
  `Yes, confirm PROP-n`, the proposal came from an earlier turn (`invocation_id` differs) and was
  not used before. `before_tool_callback` also refuses unknown tools and invented arguments.
- **Alternatives considered:** Prompt-only confirmation (not enforceable); accepting "yes"/"sí"
  (ambiguous when several proposals exist).
- **Consequences:** Users must type an English phrase even in a Spanish conversation; the agent shows
  it verbatim. Confirmation is not authentication.
- **Related:** `workflow.py`, `tools.py`, `tests/test_stock_movements.py`

## D-007 — Pydantic output contracts for every tool result
- **Date:** 2026-10-26
- **Status:** Accepted
- **Decided by:** Claude (addresses the ShipTrack rubric gap "outputs conform to Pydantic models")
- **Context:** Tool results should have a fixed, testable shape.
- **Decision:** Each tool returns one `*Result` model from `models.py` serialised with
  `model_dump(mode="json")`; failures return `ErrorResult`. Tests validate results against the models.
  Free-form conversational replies are not schema-validated.
- **Alternatives considered:** ADK `output_schema` on the agent; it forces every reply into JSON
  and, in many ADK versions, cannot be combined with tools.
- **Consequences:** New tools must add a result model.
- **Related:** `agents/inventory_agent/models.py`

## D-008 — Reorder suggestion rule and advice-only scope
- **Date:** 2026-10-26
- **Status:** Proposed — awaiting project owner confirmation
- **Decided by:** Claude
- **Context:** The low-stock report needs a repeatable suggested quantity.
- **Decision:** A product is low when `quantity_on_hand <= reorder_point`. The suggestion is the
  smallest multiple of `reorder_quantity` that lifts stock above the reorder point; cost is
  quantity × `unit_cost`; out-of-stock first. The agent cannot place orders or contact suppliers.
- **Alternatives considered:** Demand forecasting from issue history (needs more data); fixed
  `reorder_quantity` regardless of shortfall (may leave stock below the reorder point).
- **Consequences:** Supplier lead time is shown but not used in the quantity.
- **Related:** `tools.get_low_stock_report`

## D-009 — Offline tests on a temp copy; live tests opt-in
- **Date:** 2026-10-26
- **Status:** Accepted
- **Decided by:** Claude
- **Context:** Tests must not change the real data or spend model quota.
- **Decision:** Fixture `data_dir` copies `data/` to a temp folder via `INVENTORY_DATA_DIR`; a fake
  `ToolContext` simulates turns. The ADK wiring test skips when google-adk is absent. Live tests
  (`-m live`) run only with `RUN_LIVE_TESTS=1` and a key.
- **Consequences:** Offline passes do not prove live model behaviour.
- **Updates:** 2026-10-26 — Built in an environment without access to PyPI: offline suite
  46 passed, 3 skipped (ADK wiring + 2 live); validator `OK`. ADK wiring and live tests still need
  a run after `setup_project.py`.
- **Related:** `tests/`
