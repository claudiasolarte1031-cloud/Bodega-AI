"""JSON-file inventory store: the ONLY code that reads or writes the files in data/.

Design (see project_decision.md D-005):
- Every read re-loads the files, so manual edits to the JSON are picked up without a restart.
- Every record is validated with the Pydantic models; a malformed file raises `StoreError`
  and is never overwritten.
- The single write operation, `apply_movement`, is atomic: it writes the product file and the
  movement log to temporary files and swaps them in with `os.replace`. After writing it re-reads
  the files and only reports success when the saved values match.
- A process-wide lock serialises writes so two tool calls cannot interleave.
"""

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pydantic import ValidationError

from .models import MovementType, Product, StockMovement, Supplier

PRODUCTS_FILE = "inventory_db.json"
SUPPLIERS_FILE = "suppliers.json"
MOVEMENTS_FILE = "stock_movements.json"

# One lock for the whole process: ADK may run tool calls concurrently.
_WRITE_LOCK = threading.Lock()


class StoreError(RuntimeError):
    """A data file is missing, unreadable or invalid, or a write could not be verified."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def utc_now() -> str:
    """Current time as an ISO-8601 UTC string with a trailing Z (seconds precision)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def signed_change(movement_type: MovementType, quantity: int) -> int:
    """Convert the user's quantity into a signed stock change.

    receive: +quantity (quantity must be > 0)
    issue:   -quantity (quantity must be > 0)
    adjust:  quantity as given (may be negative, must not be 0) - used for cycle counts/damage
    """
    if movement_type in ("receive", "issue"):
        if quantity <= 0:
            raise StoreError("invalid_quantity", f"A {movement_type} needs a positive quantity.")
        return quantity if movement_type == "receive" else -quantity
    if quantity == 0:
        raise StoreError("invalid_quantity", "An adjustment cannot be zero.")
    return quantity


class InventoryStore:
    """Read/write access to the three JSON files in one data folder."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    # ----------------------------------------------------------------------------------------
    # Low-level file helpers
    # ----------------------------------------------------------------------------------------
    def _read_list(self, filename: str, key: str, required: bool = True) -> list:
        """Read `{key: [...]}` from a file. A missing optional file counts as an empty list."""
        path = self.data_dir / filename
        if not path.exists():
            if required:
                raise StoreError("data_missing", f"{filename} was not found in {self.data_dir}.")
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreError("data_unreadable", f"{filename} could not be read: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get(key), list):
            raise StoreError("data_invalid", f"{filename} must contain an object with a '{key}' list.")
        return data[key]

    def _write_atomic(self, filename: str, payload: dict) -> None:
        """Write JSON to a temp file in the same folder, then atomically replace the target."""
        target = self.data_dir / filename
        fd, tmp = tempfile.mkstemp(prefix=f".{filename}.", suffix=".tmp", dir=self.data_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, target)
        except BaseException:
            # Never leave half-written temp files behind.
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    # ----------------------------------------------------------------------------------------
    # Validated reads
    # ----------------------------------------------------------------------------------------
    def suppliers(self) -> Dict[str, Supplier]:
        """All suppliers keyed by supplier_id."""
        try:
            items = [Supplier.model_validate(s) for s in self._read_list(SUPPLIERS_FILE, "suppliers")]
        except ValidationError as exc:
            raise StoreError("data_invalid", f"{SUPPLIERS_FILE} has an invalid record: {exc.errors()[0]['msg']}") from exc
        return {s.supplier_id: s for s in items}

    def products(self) -> List[Product]:
        """All products, in file order."""
        try:
            return [Product.model_validate(p) for p in self._read_list(PRODUCTS_FILE, "products")]
        except ValidationError as exc:
            raise StoreError("data_invalid", f"{PRODUCTS_FILE} has an invalid record: {exc.errors()[0]['msg']}") from exc

    def movements(self) -> List[StockMovement]:
        """All saved movements, in file order (oldest first). The file is optional."""
        raw = self._read_list(MOVEMENTS_FILE, "movements", required=False)
        try:
            return [StockMovement.model_validate(m) for m in raw]
        except ValidationError as exc:
            raise StoreError("data_invalid", f"{MOVEMENTS_FILE} has an invalid record: {exc.errors()[0]['msg']}") from exc

    def find_product(self, sku: str) -> Optional[Product]:
        """Return the product with this SKU (case-insensitive), or None."""
        wanted = (sku or "").strip().upper()
        return next((p for p in self.products() if p.sku.upper() == wanted), None)

    # ----------------------------------------------------------------------------------------
    # The single write operation
    # ----------------------------------------------------------------------------------------
    def apply_movement(
        self,
        sku: str,
        movement_type: MovementType,
        quantity_change: int,
        reason: str,
        performed_by: str,
        expected_before: Optional[int] = None,
    ) -> Tuple[StockMovement, Product]:
        """Save one stock movement and the new stock level, atomically.

        Args:
            sku: product to change.
            movement_type: receive, issue or adjust.
            quantity_change: signed change (see `signed_change`).
            reason: why the stock changed (required, shown in the audit log).
            performed_by: operator name recorded on the movement.
            expected_before: when given, the write is refused if the stock changed since the
                proposal was made (protects against stale confirmations).

        Returns:
            The saved movement and the updated product, both re-read from disk.

        Raises:
            StoreError: unknown SKU, stale stock, negative result, invalid data or failed read-back.
        """
        if not (reason or "").strip():
            raise StoreError("reason_required", "A reason is required for every stock movement.")

        with _WRITE_LOCK:
            raw_products = self._read_list(PRODUCTS_FILE, "products")
            products = self.products()                    # validates the whole file first
            movements = self.movements()
            index = next((i for i, p in enumerate(products) if p.sku == sku), None)
            if index is None:
                raise StoreError("product_not_found", f"No product with SKU {sku}.")
            product = products[index]

            before = product.quantity_on_hand
            if expected_before is not None and before != expected_before:
                raise StoreError(
                    "stale_proposal",
                    f"Stock of {sku} changed from {expected_before} to {before} since the proposal; "
                    "propose the movement again.",
                )
            after = before + quantity_change
            if after < 0:
                raise StoreError(
                    "insufficient_stock",
                    f"Only {before} {product.unit} of {sku} on hand; cannot remove {abs(quantity_change)}.",
                )

            now = utc_now()
            next_number = max((int(m.movement_id.split("-")[1]) for m in movements), default=0) + 1
            movement = StockMovement(
                movement_id=f"MOV-{next_number:06d}",
                sku=sku,
                movement_type=movement_type,
                quantity_change=quantity_change,
                quantity_before=before,
                quantity_after=after,
                reason=reason.strip(),
                performed_by=performed_by,
                timestamp=now,
            )

            # Update only the two fields that change; keep every other key exactly as stored.
            raw_products[index] = {**raw_products[index], "quantity_on_hand": after, "last_updated": now}
            raw_movements = [m.model_dump(mode="json") for m in movements] + [movement.model_dump(mode="json")]

            # Movement log first: if the product write then failed, the validator would flag the
            # mismatch instead of silently losing the audit record.
            self._write_atomic(MOVEMENTS_FILE, {"movements": raw_movements})
            self._write_atomic(PRODUCTS_FILE, {"products": raw_products})

            # Read-back verification: report success only if disk matches what we meant to save.
            saved_product = self.find_product(sku)
            saved_movement = next((m for m in self.movements() if m.movement_id == movement.movement_id), None)
            if saved_product is None or saved_product.quantity_on_hand != after or saved_movement != movement:
                raise StoreError("write_not_verified", "The change could not be verified after saving.")
            return saved_movement, saved_product
