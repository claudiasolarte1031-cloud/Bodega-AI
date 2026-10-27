# Inventory Agent (Google ADK)

> **Resumen en español:** Agente de inventario construido con Google ADK y Gemini. Responde preguntas
> de stock, genera un reporte de bajo inventario con sugerencias de reorden y registra movimientos de
> stock (entradas, salidas y ajustes) **solo** después de que el usuario escribe la frase exacta de
> confirmación (`Yes, confirm PROP-n`). Los datos son ficticios y viven en `data/*.json`. Para
> empezar: `python setup_project.py`, pon tu `GOOGLE_API_KEY` en `.env` y ejecuta `adk web agents`.
> El agente responde en el idioma en que le escribas (español o inglés).

`inventory_agent` is a single Google ADK agent for a fictional warehouse (Contoso Home Supplies).
It answers stock questions, builds a low-stock reorder report, summarises stock value, shows the
audit log, and records stock movements through a guarded two-step flow. All numbers come from
code over local JSON files; the model only chooses tools and explains results.

## Contents

- [Project layout](#project-layout)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Using the agent](#using-the-agent)
- [How it works](#how-it-works)
- [Data files](#data-files)
- [Tests and validation](#tests-and-validation)
- [Troubleshooting](#troubleshooting)

## Project layout

```
inventory_project/
├── README.md                     ← this file
├── AGENTS.md                     ← rules for AI coding agents (Copilot etc.) working in this repo
├── RUN_ADK.md                    ← short run guide for PowerShell and Git Bash
├── project_decision.md           ← decision log (D-001, D-002, ...)
├── setup_project.py              ← one-command setup (venv, dependencies, .env, verification)
├── requirements.txt              ← google-adk, pydantic, python-dotenv, pytest
├── .env.example                  ← copy to .env (git-ignored) and add GOOGLE_API_KEY
├── .gitignore
├── pytest.ini                    ← pytest config (tests/, `live` marker)
├── .github/
│   └── copilot-instructions.md   ← always-on Copilot rules for this project
├── agents/                       ← `adk web agents` lists every agent package in here
│   └── inventory_agent/
│       ├── __init__.py           ← lazy-loads agent.py (so tools import without ADK/keys)
│       ├── agent.py              ← builds `root_agent` (model, prompt, tools, callbacks)
│       ├── config.py             ← reads .env / environment; fails fast on missing settings
│       ├── prompt.py             ← system instruction and description
│       ├── tools.py              ← the 9 function tools the model can call
│       ├── workflow.py           ← ADK callbacks: tool allow-list, confirmation-phrase parsing
│       ├── models.py             ← Pydantic data models and tool output contracts
│       └── store.py              ← the only code that reads/writes data/*.json (atomic writes)
├── data/
│   ├── inventory_db.json         ← products (SKU, stock, reorder point, cost, location, supplier)
│   ├── suppliers.json            ← fictional suppliers and lead times
│   └── stock_movements.json      ← audit log of every stock change (append-only)
├── scripts/
│   └── validate_inventory_data.py← cross-file consistency check (stdlib only)
└── tests/
    ├── conftest.py               ← temp copy of data/, fake ADK ToolContext
    ├── test_read_tools.py        ← read-only tools, filters, totals, error handling
    ├── test_stock_movements.py   ← propose/confirm flow and confirmation rules
    ├── test_workflow_and_config.py ← guards, config, prompt contract, validator, ADK wiring
    └── test_live_agent.py        ← opt-in real-model tests (RUN_LIVE_TESTS=1)
```

## Quick start

Requirements: Python 3.10+ (3.13 recommended) and a Google AI Studio API key.

```powershell
# 1. From the project root: create .venv, install requirements, create .env, verify
python setup_project.py

# 2. Activate the environment
.\.venv\Scripts\Activate.ps1          # PowerShell
# source .venv/Scripts/activate       # Git Bash on Windows
# source .venv/bin/activate           # macOS / Linux

# 3. Put your key in .env:  GOOGLE_API_KEY=AIza...

# 4. Start the ADK web UI and open http://127.0.0.1:8000, then pick `inventory_agent`
adk web agents --host 127.0.0.1 --port 8000 --no_use_local_storage
```

Prefer the terminal? `adk run agents/inventory_agent` starts an interactive chat in the console.

Installing by hand instead of with the script? Always target the venv, never the system Python:

```powershell
uv venv .venv --python 3.13
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
Copy-Item .env.example .env
```

## Configuration

Settings are read from the process environment first, then from `.env` in the project root
(resolved relative to `config.py`, not the current folder). `.env` is git-ignored; never commit it.

| Variable | Required | Purpose |
|---|---|---|
| `MODEL_NAME` | yes | Gemini model, default `gemini-3.5-flash`. Never hard-coded in code. |
| `GOOGLE_API_KEY` | yes | Google AI Studio key. Checked for presence only; never printed. |
| `GOOGLE_GENAI_USE_VERTEXAI` | no | `FALSE` = use AI Studio rather than Vertex AI. |
| `INVENTORY_OPERATOR_NAME` | no | Name saved in `performed_by` on movements (default `Inventory Operator`). |
| `INVENTORY_DATA_DIR` | no | Folder with the JSON files (default `./data`). Tests point it at a temp copy. |

If `MODEL_NAME` or `GOOGLE_API_KEY` is blank, loading the agent fails immediately with a message
naming the variable. Restart `adk web` after editing `.env`.

## Using the agent

Example conversation (you can write in Spanish or English):

| You type | What happens |
|---|---|
| `hola` | Greeting plus an overview from `get_inventory_summary`: 10 SKUs, total value, 4 low / 1 out of stock |
| `¿Qué productos están bajos de stock?` | `get_low_stock_report`: SKU-1008 (out of stock) first, suggested order quantities and estimated cost |
| `Busca el producto "drill"` | `search_products("drill")`: SKU-1001 and SKU-1002 (product names in the data are English) |
| `Show me SKU-1004` | `get_product`: stock, location, supplier contact and recent movements |
| `Receive 20 SKU-1004, reason PO 4471 delivered` | `propose_stock_movement`: shows 9 → 29 and the phrase `Yes, confirm PROP-1`. **Nothing saved yet.** |
| `ok` | Not a confirmation. The agent repeats the exact phrase. |
| `Yes, confirm PROP-1` | `confirm_stock_movement`: saves `MOV-000005`, stock is now 29 |
| `Show the movement history` | `list_stock_movements`, newest first |

Movement types:

- `receive` — goods in; positive quantity (e.g. 20).
- `issue` — goods out; positive quantity (e.g. 4 removes four units). Cannot go below zero.
- `adjust` — count correction; signed quantity (e.g. `-2` damaged, `3` found).

What the agent will **not** do: place purchase orders, contact suppliers, create or delete
products, change prices or edit suppliers. The low-stock report is advice only.

## How it works

```
User ──► ADK runner ──► inventory_agent (Gemini, prompt.py)
                           │  before_agent_callback: remember_user_message (turn counter)
                           │  before_tool_callback:  guard_tool_call (allow-list + argument check)
                           ▼
                        tools.py ──► store.py ──► data/*.json
                           ▲            (validated reads, atomic write + read-back)
                           └── models.py (Pydantic output contracts for every result)
```

**Tools** (`tools.py`). Seven read-only tools and two write-flow tools:

| Tool | Writes? | Returns (`models.py`) |
|---|---|---|
| `list_products(category, low_stock_only)` | no | `ProductListResult` |
| `search_products(query)` | no | `ProductListResult` |
| `get_product(sku)` | no | `ProductDetailResult` |
| `get_low_stock_report()` | no | `LowStockReportResult` |
| `get_inventory_summary()` | no | `InventorySummaryResult` |
| `list_suppliers()` | no | `SupplierListResult` |
| `list_stock_movements(sku, limit)` | no | `MovementListResult` |
| `propose_stock_movement(sku, movement_type, quantity, reason)` | session state only | `MovementProposalResult` |
| `confirm_stock_movement(proposal_id)` | **yes** (via store) | `MovementSavedResult` |

Any failure returns `ErrorResult` (`status: "error"`, `error_code`, `message`) instead of raising,
so the model can explain it.

**Rules enforced in code, not only in the prompt** (D-006):

1. Only the nine tools above may be called, and only with their declared argument names.
2. `confirm_stock_movement` saves only when the user's own message **in that turn** is exactly
   `Yes, confirm PROP-n` (case and spacing tolerant). "ok", "sí", "approve" are refused.
3. The proposal must come from an **earlier** turn (ADK `invocation_id`), so the user has seen it.
4. Each proposal can be used **once**.
5. If stock changed since the proposal, the save is refused as `stale_proposal`.
6. Stock can never go negative; every movement needs a reason.

**Store** (`store.py`). Re-reads and validates the files on every call. The single write,
`apply_movement`, holds a lock, writes the movement log and the product file through temp files
plus `os.replace` (atomic), re-reads both and reports success only if they match.

**Reorder suggestion.** For each product at or below its reorder point, the suggested quantity is
the smallest multiple of `reorder_quantity` that lifts stock above the reorder point; cost is
quantity × `unit_cost`. Out-of-stock items are listed first.

## Data files

All data is fictional (`*.example.com` supplier emails, `555` phone numbers). Timestamps are UTC
with a trailing `Z`.

- `data/inventory_db.json` — `{"products": [...]}`; fields: `sku`, `name`, `category`, `unit`,
  `quantity_on_hand`, `reorder_point`, `reorder_quantity`, `unit_cost`, `location`,
  `supplier_id`, `last_updated`.
- `data/suppliers.json` — `{"suppliers": [...]}`; `supplier_id`, `name`, `contact_email`,
  `phone`, `lead_time_days`.
- `data/stock_movements.json` — `{"movements": [...]}`; `movement_id` (`MOV-000001`), `sku`,
  `movement_type`, `quantity_change` (signed), `quantity_before`, `quantity_after`, `reason`,
  `performed_by`, `timestamp`. Written only by `store.py`.

If you edit the JSON by hand, keep the latest movement of each SKU equal to its
`quantity_on_hand`, then run the validator.

## Tests and validation

```powershell
pytest                                               # offline suite (no model calls)
python scripts/validate_inventory_data.py            # prints OK and exits 0 when data is consistent
$env:RUN_LIVE_TESTS="1"; pytest -m live              # real Gemini calls (needs a valid key)
```

Offline tests run every tool against a temporary copy of `data/` with a fake ADK `ToolContext`,
so the real data is never changed and no key is needed. The ADK wiring test is skipped automatically
when google-adk is not installed. Live tests use the real `InMemoryRunner` and also work on a temp copy.

## Troubleshooting

- **`MODEL_NAME is missing or blank` / `GOOGLE_API_KEY is missing or blank`** — set it in `.env`
  and restart `adk web`.
- **"API key not valid"** — AI Studio keys start with `AIza`; check the key in Google AI Studio.
- **`CERTIFICATE_VERIFY_FAILED`** — a corporate network is inspecting HTTPS; use a network
  without TLS inspection.
- **`inventory_agent` not in the UI dropdown** — run `adk web agents` from the project root
  (the argument is the folder that *contains* the agent package).
- **Agent says `data_invalid` or `data_missing`** — run the validator to see which record is wrong.
- **Port 8000 busy** — use `--port 8001` and open that port.
- **PowerShell blocks activation** — `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned`.

## Verification status

On 2026-10-26 the offline suite passed: **46 passed, 3 skipped** (2 opt-in live tests and the ADK
wiring test, because google-adk could not be installed in the build environment). The data
validator printed `OK`. The ADK wiring and live tests have **not** been run yet; run `pytest` after
`setup_project.py` installs google-adk, then the live tests with your key.
