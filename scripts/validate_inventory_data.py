"""Cross-file consistency check for the inventory mock data.

Usage (from the project root):
    python scripts/validate_inventory_data.py            # validates ./data
    python scripts/validate_inventory_data.py <data_dir> # validates another folder

Uses only the Python standard library so it runs before any dependency is installed.
Prints `OK` and exits 0 when every rule passes; otherwise prints one line per problem and exits 1.

Rules checked:
    suppliers.json       unique supplier_id, non-blank name, lead_time_days >= 0
    inventory_db.json    unique SKU, known supplier_id, non-negative integer quantities,
                         reorder_quantity > 0, unit_cost >= 0, UTC timestamps ending in "Z"
    stock_movements.json unique sequential-looking IDs, known SKU, sign of quantity_change matches
                         movement_type, quantity_after = quantity_before + quantity_change >= 0,
                         and the latest movement of each SKU matches its current quantity_on_hand
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

# Default data folder: <project root>/data (this file lives in <project root>/scripts).
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

MOVEMENT_TYPES = {"receive", "issue", "adjust"}
MOVEMENT_ID = re.compile(r"^MOV-\d{6}$")
SKU_PATTERN = re.compile(r"^SKU-\d{4}$")


def _is_utc(ts) -> bool:
    """Return True when `ts` is an ISO-8601 UTC timestamp like 2026-10-26T12:00:00Z."""
    if not isinstance(ts, str) or not ts.endswith("Z"):
        return False
    try:
        datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
        return True
    except ValueError:
        return False


def _is_int(value) -> bool:
    """bool is a subclass of int in Python; reject it explicitly."""
    return isinstance(value, int) and not isinstance(value, bool)


def _load(path: Path, key: str, errors: list) -> list:
    """Load `{key: [...]}` from a JSON file, recording an error instead of raising."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"{path.name}: file not found")
        return []
    except json.JSONDecodeError as exc:
        errors.append(f"{path.name}: invalid JSON ({exc})")
        return []
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        errors.append(f"{path.name}: expected an object with a '{key}' list")
        return []
    return data[key]


def validate(data_dir: Path) -> list:
    """Validate every inventory file in `data_dir` and return a list of error strings."""
    errors: list = []
    suppliers = _load(data_dir / "suppliers.json", "suppliers", errors)
    products = _load(data_dir / "inventory_db.json", "products", errors)
    # The movement log may legitimately not exist yet (fresh install).
    movements_path = data_dir / "stock_movements.json"
    movements = _load(movements_path, "movements", errors) if movements_path.exists() else []

    # --- suppliers ---
    supplier_ids = set()
    for s in suppliers:
        sid = s.get("supplier_id")
        if sid in supplier_ids:
            errors.append(f"suppliers.json: duplicate supplier_id {sid}")
        supplier_ids.add(sid)
        if not str(s.get("name", "")).strip():
            errors.append(f"suppliers.json: {sid} has a blank name")
        if not _is_int(s.get("lead_time_days")) or s["lead_time_days"] < 0:
            errors.append(f"suppliers.json: {sid} lead_time_days must be a non-negative integer")

    # --- products ---
    on_hand = {}
    for p in products:
        sku = p.get("sku")
        where = f"inventory_db.json: {sku}"
        if not isinstance(sku, str) or not SKU_PATTERN.match(sku):
            errors.append(f"{where}: SKU must look like SKU-1234")
        if sku in on_hand:
            errors.append(f"{where}: duplicate SKU")
        if p.get("supplier_id") not in supplier_ids:
            errors.append(f"{where}: unknown supplier_id {p.get('supplier_id')}")
        for field in ("quantity_on_hand", "reorder_point"):
            if not _is_int(p.get(field)) or p[field] < 0:
                errors.append(f"{where}: {field} must be a non-negative integer")
        if not _is_int(p.get("reorder_quantity")) or p["reorder_quantity"] <= 0:
            errors.append(f"{where}: reorder_quantity must be a positive integer")
        if not isinstance(p.get("unit_cost"), (int, float)) or p["unit_cost"] < 0:
            errors.append(f"{where}: unit_cost must be a non-negative number")
        if not _is_utc(p.get("last_updated")):
            errors.append(f"{where}: last_updated must be UTC like 2026-10-26T12:00:00Z")
        on_hand[sku] = p.get("quantity_on_hand")

    # --- movements ---
    seen_ids = set()
    latest = {}  # sku -> (timestamp, movement_id, quantity_after)
    for m in movements:
        mid = m.get("movement_id")
        where = f"stock_movements.json: {mid}"
        if not isinstance(mid, str) or not MOVEMENT_ID.match(mid):
            errors.append(f"{where}: movement_id must look like MOV-000001")
        if mid in seen_ids:
            errors.append(f"{where}: duplicate movement_id")
        seen_ids.add(mid)
        sku, mtype, change = m.get("sku"), m.get("movement_type"), m.get("quantity_change")
        if sku not in on_hand:
            errors.append(f"{where}: unknown SKU {sku}")
        if mtype not in MOVEMENT_TYPES:
            errors.append(f"{where}: movement_type must be one of {sorted(MOVEMENT_TYPES)}")
        if not _is_int(change) or change == 0:
            errors.append(f"{where}: quantity_change must be a non-zero integer")
        elif (mtype == "receive" and change < 0) or (mtype == "issue" and change > 0):
            errors.append(f"{where}: sign of quantity_change does not match movement_type {mtype}")
        before, after = m.get("quantity_before"), m.get("quantity_after")
        if not (_is_int(before) and _is_int(after) and _is_int(change)) or before + change != after:
            errors.append(f"{where}: quantity_after must equal quantity_before + quantity_change")
        elif after < 0:
            errors.append(f"{where}: quantity_after cannot be negative")
        if not str(m.get("reason", "")).strip():
            errors.append(f"{where}: reason is required")
        if not _is_utc(m.get("timestamp")):
            errors.append(f"{where}: timestamp must be UTC like 2026-10-26T12:00:00Z")
        else:
            key = (m["timestamp"], str(mid))
            if sku not in latest or key > latest[sku][:2]:
                latest[sku] = (*key, after)

    # The most recent movement of each SKU must agree with the stored stock level.
    for sku, (_, mid, after) in latest.items():
        if sku in on_hand and on_hand[sku] != after:
            errors.append(
                f"stock_movements.json: latest movement {mid} leaves {sku} at {after}, "
                f"but inventory_db.json says {on_hand[sku]}"
            )
    return errors


def main() -> int:
    """Command-line entry point. Returns the process exit code."""
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DATA_DIR
    errors = validate(data_dir)
    if errors:
        for err in errors:
            print(f"ERROR {err}")
        print(f"FAILED: {len(errors)} problem(s) in {data_dir}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
