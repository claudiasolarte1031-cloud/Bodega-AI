# Run the Inventory ADK Agent

Run every command from the project root (the folder that contains `agents/`). Before starting, set
your Google AI Studio key in `.env` as `GOOGLE_API_KEY=...`. Keep the key private; never commit `.env`.

## First-time setup

```bash
python setup_project.py
```

## PowerShell

```powershell
.\.venv\Scripts\Activate.ps1
adk web agents --host 127.0.0.1 --port 8000 --no_use_local_storage
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

If `adk` is not found, call the venv executable directly:

```powershell
.\.venv\Scripts\adk.exe web agents --host 127.0.0.1 --port 8000 --no_use_local_storage
```

## Git Bash

```bash
source .venv/Scripts/activate
adk web agents --host 127.0.0.1 --port 8000 --no_use_local_storage
```

## macOS / Linux

```bash
source .venv/bin/activate
adk web agents --host 127.0.0.1 --port 8000 --no_use_local_storage
```

## Open the UI

Open http://127.0.0.1:8000 and select `inventory_agent`. Try `hola`, then
`¿Qué productos están bajos de stock?`. Stop the server with `Ctrl+C`.

Terminal chat instead of the browser: `adk run agents/inventory_agent`.

If port 8000 is busy, use another port (for example `--port 8001`) in the command and the URL.
After changing `.env` or the agent code, restart the server.
