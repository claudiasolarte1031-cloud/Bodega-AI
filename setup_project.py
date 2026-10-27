"""One-command setup for the Inventory ADK agent project.

Run from anywhere:  python setup_project.py
Safe to run again; every step skips work that is already done.

Steps:
    1. check Python (3.10+ is required by google-adk)
    2. find or install uv (fast package/venv manager)
    3. create .venv (Python 3.13 when available)
    4. install requirements.txt INTO .venv (never into the system Python)
    5. create .env from .env.example and report missing keys
    6. verify: imports, data validation and the offline pytest suite

Options:
    --recreate   delete and rebuild .venv
    --skip-tests do not run pytest during verification
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# --- Paths and constants ----------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent                      # project root (folder of this file)
VENV = ROOT / ".venv"                                       # virtual environment folder
VENV_PYTHON = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
TARGET_PYTHON = "3.13"                                      # preferred interpreter for .venv
MIN_PYTHON = (3, 10)                                        # minimum supported by google-adk
REQUIRED_KEYS = ("MODEL_NAME", "GOOGLE_API_KEY")            # must be non-blank in .env
VALIDATOR = ROOT / "scripts" / "validate_inventory_data.py"


# --- Small printing helpers -------------------------------------------------------------------
def step(msg: str) -> None:
    """Print a top-level step header."""
    print(f"\n==> {msg}", flush=True)


def info(msg: str) -> None:
    """Print an indented detail line."""
    print(f"    {msg}", flush=True)


def fail(msg: str) -> None:
    """Print an error and stop the script with exit code 1."""
    print(f"\nSETUP FAILED: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    """Run a command from the project root, echoing it first.

    When `check` is True a non-zero exit code stops the setup.
    """
    info("$ " + " ".join(str(c) for c in cmd))
    result = subprocess.run([str(c) for c in cmd], cwd=ROOT, **kwargs)
    if check and result.returncode != 0:
        fail(f"command exited with code {result.returncode}: {' '.join(str(c) for c in cmd)}")
    return result


# --- Setup steps ------------------------------------------------------------------------------
def check_python() -> None:
    """Stop early when the running Python is too old for google-adk."""
    step("Checking Python")
    if sys.version_info < MIN_PYTHON:
        fail(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required; found {sys.version.split()[0]}.")
    info(f"Python {sys.version.split()[0]} at {sys.executable}")


def find_uv() -> list:
    """Return the command prefix for uv, installing it for the current user if needed."""
    step("Finding uv")
    if shutil.which("uv"):
        info("uv found on PATH")
        return ["uv"]
    probe = subprocess.run([sys.executable, "-m", "uv", "--version"], capture_output=True)
    if probe.returncode == 0:
        info("uv found as a Python module")
        return [sys.executable, "-m", "uv"]

    info("uv not found; installing it with pip")
    in_venv = sys.prefix != sys.base_prefix
    run([sys.executable, "-m", "pip", "install", "--quiet", *([] if in_venv else ["--user"]), "uv"])
    return [sys.executable, "-m", "uv"]


def create_venv(uv: list, recreate: bool) -> None:
    """Create .venv with Python 3.13, falling back to the running interpreter."""
    step("Creating the virtual environment (.venv)")
    if recreate and VENV.exists():
        info("--recreate given; deleting the existing .venv")
        shutil.rmtree(VENV)
    if VENV_PYTHON.exists():
        info(f"Reusing existing .venv ({VENV_PYTHON})")
        return
    # uv uses an installed Python 3.13 if there is one, otherwise downloads it.
    if run([*uv, "venv", VENV, "--python", TARGET_PYTHON], check=False).returncode != 0:
        info(f"Could not get Python {TARGET_PYTHON}; falling back to the Python running this script")
        run([*uv, "venv", VENV, "--python", sys.executable])


def install_requirements(uv: list) -> None:
    """Install requirements.txt into .venv only."""
    step("Installing requirements.txt into .venv")
    # --python is mandatory: without it uv may install into whatever Python launched it.
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    run([*uv, "pip", "install", "--python", VENV_PYTHON, "-r", ROOT / "requirements.txt"], env=env)


def create_env_file() -> bool:
    """Copy .env.example to .env (once) and report required keys that are still blank.

    Returns True when every required key has a value. Key values are never printed.
    """
    step("Creating .env")
    env_file, example = ROOT / ".env", ROOT / ".env.example"
    if env_file.exists():
        info(".env already exists; leaving it unchanged")
    else:
        shutil.copyfile(example, env_file)
        info("Copied .env.example to .env")

    values = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    missing = [k for k in REQUIRED_KEYS if not values.get(k)]
    if missing:
        info(f"ACTION NEEDED: set {', '.join(missing)} in .env before running the agent")
    info(f"Model: {values.get('MODEL_NAME') or '<MODEL_NAME missing>'}")
    return not missing


def verify(skip_tests: bool) -> None:
    """Check imports, validate the mock data and run the offline test suite."""
    step("Verifying the setup")
    run([VENV_PYTHON, "-c",
         "import google.adk, pydantic, pytest, dotenv; "
         "print('    google-adk', google.adk.__version__, '| pydantic', pydantic.VERSION, '| pytest', pytest.__version__)"])
    run([VENV_PYTHON, VALIDATOR])
    if skip_tests:
        info("Skipped pytest (--skip-tests)")
        return
    result = run([VENV_PYTHON, "-m", "pytest", "-q"], check=False)
    if result.returncode == 5:
        info("No tests collected; that is fine at this stage")
    elif result.returncode != 0:
        fail("pytest reported failures")


def main() -> None:
    """Parse options and run every setup step in order."""
    parser = argparse.ArgumentParser(description="Set up the Inventory ADK agent project.")
    parser.add_argument("--recreate", action="store_true", help="delete and rebuild .venv")
    parser.add_argument("--skip-tests", action="store_true", help="do not run pytest during verification")
    args = parser.parse_args()

    check_python()
    uv = find_uv()
    create_venv(uv, args.recreate)
    install_requirements(uv)
    keys_ok = create_env_file()
    verify(args.skip_tests)

    activate = r".venv\Scripts\Activate.ps1" if os.name == "nt" else "source .venv/bin/activate"
    print("\nSetup complete.")
    print(f"  1. Activate the environment:  {activate}")
    if not keys_ok:
        print("  2. Add your Google AI Studio key to .env (GOOGLE_API_KEY)")
    print("  3. Start the UI:  adk web agents   then open http://127.0.0.1:8000")
    print("  See README.md for usage and project_decision.md for why things are set up this way.")


if __name__ == "__main__":
    main()
