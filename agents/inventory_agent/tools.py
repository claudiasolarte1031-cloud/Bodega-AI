"""Function tools registered with the ADK inventory agent.

How ADK uses this file:
- Each public function below becomes a tool. ADK builds the tool schema from the function name,
  the parameter names/types and the docstring, so the docstrings are written FOR THE MODEL:
  they say when to use the tool and what it must not do.
- A parameter named `tool_context` is injected by ADK (session state, invocation id, the user's
  message) and is hidden from the model.
- Every tool returns a plain dict produced by a Pydantic output model from `models.py`
  (`status` is "ok" or "error"). Tools never raise to the model: problems become ErrorResult.

Read tools: list_products, search_products, get_product, get_low_stock_report,
            get_inventory_summary, list_suppliers, list_stock_movements
Write flow: propose_stock_movement (no write) -> user types the phrase -> confirm_stock_movement
"""

from collections import defaultdict
from typing import Any

from . import config
from .models import (
    CategoryTotal,
    InventorySummaryResult,
    LowStockReportResult,
    MovementListResult,
    MovementProposal,
    MovementProposalResult,
    MovementSavedResult,
    ProductDetailResult,
    ProductListResult,
    ProductView,
    ReorderLine,
    SupplierListResult,
    error,
    proposal_or_none,
)
from .store import InventoryStore, StoreError, signed_change
from .workflow import (
    STATE_PROPOSAL_COUNTER,
    STATE_PROPOSALS,
    confirmation_phrase,
    confirmed_proposal_id,
    user_text,
)

# ADK injects ToolContext by parameter NAME. The import is optional so these tools can be unit
# tested without google-adk installed; the annotation is only documentation.
try:  # pragma: no cover - depends on the environment
    from google.adk.tools import ToolContext
except ImportError:  # pragma: no cover
    ToolContext = Any  # type: ignore[misc,assignment]

MAX_MOVEMENTS = 50          # upper bound for list_stock_movements
VALID_TYPES = ("receive", "issue", "adjust")


def _store() -> InventoryStore:
    """A store for the configured data folder (re-read every call so tests can redirect it)."""
    return InventoryStore(config.data_dir())


def _views(store: InventoryStore, products) -> list:
    """Convert products to ProductView objects with the supplier name filled in."""
    suppliers = store.suppliers()
    return [
        ProductView.from_product(p, suppliers[p.supplier_id].name if p.supplier_id in suppliers else "Unknown supplier")
        for p in products
    ]


# =============================================================================================
# Read-only tools
# =============================================================================================
def list_products(category: str = "", low_stock_only: bool = False) -> dict:
    """List products in the inventory with stock level, location, value and low-stock flag.

    Use this to answer "what do we have", "show me the Tools category" or "what is low".
    Read-only; it never changes stock.

    Args:
        category: Optional category name (case-insensitive), e.g. "Tools", "Safety",
            "Packaging", "Electrical". Leave empty for all categories.
        low_stock_only: When true, return only products at or below their reorder point.

    Returns:
        ProductListResult with `products` (sku, name, quantity_on_hand, low_stock, ...) or an error.
    """
    try:
        store = _store()
        products = store.products()
        if category.strip():
            products = [p for p in products if p.category.lower() == category.strip().lower()]
        if low_stock_only:
            products = [p for p in products if p.is_low_stock]
        views = _views(store, products)
        return ProductListResult(
            count=len(views),
            filters={"category": category.strip() or None, "low_stock_only": low_stock_only},
            products=views,
        ).model_dump(mode="json")
    except StoreError as exc:
        return error(exc.code, exc.message)


def search_products(query: str) -> dict:
    """Find products whose SKU, name, category or location contains the search text.

    Use this when the user describes an item by name ("drill", "tape") instead of a SKU.
    Read-only.

    Args:
        query: Text to look for (case-insensitive). Must not be empty.

    Returns:
        ProductListResult with the matching products (may be empty) or an error.
    """
    text = (query or "").strip().lower()
    if not text:
        return error("empty_query", "Tell me what to search for (a name, SKU, category or location).")
    try:
        store = _store()
        matches = [
            p for p in store.products()
            if any(text in field.lower() for field in (p.sku, p.name, p.category, p.location))
        ]
        views = _views(store, matches)
        return ProductListResult(count=len(views), filters={"query": query.strip()}, products=views).model_dump(mode="json")
    except StoreError as exc:
        return error(exc.code, exc.message)


def get_product(sku: str) -> dict:
    """Get full details of one product: stock, supplier contact and its 5 most recent movements.

    Read-only. Use the SKU exactly as shown by other tools (e.g. "SKU-1004").

    Args:
        sku: Product SKU, e.g. "SKU-1004" (case-insensitive).

    Returns:
        ProductDetailResult, or an error with code `product_not_found`.
    """
    try:
        store = _store()
        product = store.find_product(sku)
        if product is None:
            return error("product_not_found", f"No product with SKU {sku!r}. Try search_products.")
        suppliers = store.suppliers()
        supplier = suppliers.get(product.supplier_id)
        if supplier is None:
            return error("data_invalid", f"{product.sku} refers to unknown supplier {product.supplier_id}.")
        recent = [m for m in store.movements() if m.sku == product.sku][-5:][::-1]
        return ProductDetailResult(
            product=ProductView.from_product(product, supplier.name),
            supplier=supplier,
            recent_movements=recent,
        ).model_dump(mode="json")
    except StoreError as exc:
        return error(exc.code, exc.message)


def get_low_stock_report() -> dict:
    """Build a reorder suggestion for every product at or below its reorder point.

    The suggested quantity brings stock back above the reorder point using the product's
    standard reorder quantity; cost uses the stored unit cost. This is ADVICE ONLY: it does not
    place purchase orders or change stock.

    Returns:
        LowStockReportResult with one line per product, out-of-stock items first.
    """
    try:
        store = _store()
        suppliers = store.suppliers()
        lines = []
        for p in store.products():
            if not p.is_low_stock:
                continue
            # Order in whole multiples of reorder_quantity until stock exceeds the reorder point.
            shortfall = p.reorder_point - p.quantity_on_hand + 1
            multiples = max(1, -(-shortfall // p.reorder_quantity))  # ceiling division
            qty = multiples * p.reorder_quantity
            sup = suppliers.get(p.supplier_id)
            lines.append(ReorderLine(
                sku=p.sku, name=p.name,
                quantity_on_hand=p.quantity_on_hand, reorder_point=p.reorder_point,
                suggested_order_quantity=qty, estimated_cost=round(qty * p.unit_cost, 2),
                supplier_id=p.supplier_id,
                supplier_name=sup.name if sup else "Unknown supplier",
                lead_time_days=sup.lead_time_days if sup else 0,
                out_of_stock=p.quantity_on_hand == 0,
            ))
        # Most urgent first: out of stock, then lowest stock relative to the reorder point.
        lines.sort(key=lambda l: (not l.out_of_stock, l.quantity_on_hand - l.reorder_point, l.sku))
        return LowStockReportResult(
            count=len(lines),
            total_estimated_cost=round(sum(l.estimated_cost for l in lines), 2),
            lines=lines,
        ).model_dump(mode="json")
    except StoreError as exc:
        return error(exc.code, exc.message)


def get_inventory_summary() -> dict:
    """Summarise the whole inventory: SKU count, units, total stock value and totals by category.

    Read-only. Values are computed in code from quantity_on_hand x unit_cost.

    Returns:
        InventorySummaryResult or an error.
    """
    try:
        products = _store().products()
        totals = defaultdict(lambda: {"sku_count": 0, "units": 0, "value": 0.0})
        for p in products:
            t = totals[p.category]
            t["sku_count"] += 1
            t["units"] += p.quantity_on_hand
            t["value"] += p.quantity_on_hand * p.unit_cost
        by_category = [
            CategoryTotal(category=c, sku_count=t["sku_count"], units_on_hand=t["units"], stock_value=round(t["value"], 2))
            for c, t in sorted(totals.items())
        ]
        return InventorySummaryResult(
            sku_count=len(products),
            units_on_hand=sum(p.quantity_on_hand for p in products),
            total_stock_value=round(sum(c.stock_value for c in by_category), 2),
            low_stock_count=sum(1 for p in products if p.is_low_stock),
            out_of_stock_count=sum(1 for p in products if p.quantity_on_hand == 0),
            by_category=by_category,
        ).model_dump(mode="json")
    except StoreError as exc:
        return error(exc.code, exc.message)


def list_suppliers() -> dict:
    """List all suppliers with contact details and lead time in days. Read-only.

    Returns:
        SupplierListResult or an error.
    """
    try:
        suppliers = list(_store().suppliers().values())
        return SupplierListResult(count=len(suppliers), suppliers=suppliers).model_dump(mode="json")
    except StoreError as exc:
        return error(exc.code, exc.message)


def list_stock_movements(sku: str = "", limit: int = 10) -> dict:
    """Show the stock movement history (audit log), newest first. Read-only.

    Args:
        sku: Optional SKU to filter by. Leave empty for all products.
        limit: How many movements to return, 1 to 50 (default 10).

    Returns:
        MovementListResult or an error.
    """
    if not isinstance(limit, int) or limit < 1 or limit > MAX_MOVEMENTS:
        return error("invalid_limit", f"limit must be between 1 and {MAX_MOVEMENTS}.")
    try:
        store = _store()
        movements = store.movements()
        if sku.strip():
            product = store.find_product(sku)
            if product is None:
                return error("product_not_found", f"No product with SKU {sku!r}.")
            movements = [m for m in movements if m.sku == product.sku]
        newest = list(reversed(movements))[:limit]
        return MovementListResult(count=len(newest), movements=newest).model_dump(mode="json")
    except StoreError as exc:
        return error(exc.code, exc.message)


# =============================================================================================
# Write flow (two steps, guarded in code)
# =============================================================================================
def propose_stock_movement(
    sku: str, movement_type: str, quantity: int, reason: str, tool_context: ToolContext
) -> dict:
    """Prepare (but DO NOT save) a stock change and return the phrase the user must type.

    Use this when the user wants to receive goods, issue/consume items, or correct a count.
    Show the user the before/after quantities and the `confirmation_phrase`, then STOP and wait.
    Never call confirm_stock_movement in the same turn as this tool.

    Args:
        sku: Product SKU, e.g. "SKU-1004".
        movement_type: "receive" (stock in), "issue" (stock out) or "adjust" (count correction).
        quantity: For receive/issue a positive number of units. For adjust the signed change,
            e.g. -2 for two damaged units or 3 for three units found.
        reason: Why the stock changes (required), e.g. "PO 4471 delivered" or "cycle count".

    Returns:
        MovementProposalResult with `confirmation_phrase`, or an error (unknown SKU, invalid
        quantity, insufficient stock, missing reason).
    """
    mtype = (movement_type or "").strip().lower()
    if mtype not in VALID_TYPES:
        return error("invalid_movement_type", "movement_type must be receive, issue or adjust.")
    if not isinstance(quantity, int) or isinstance(quantity, bool):
        return error("invalid_quantity", "quantity must be a whole number.")
    if not (reason or "").strip():
        return error("reason_required", "Ask the user for the reason for this stock movement.")
    try:
        change = signed_change(mtype, quantity)  # type: ignore[arg-type]
        product = _store().find_product(sku)
    except StoreError as exc:
        return error(exc.code, exc.message)
    if product is None:
        return error("product_not_found", f"No product with SKU {sku!r}.")
    after = product.quantity_on_hand + change
    if after < 0:
        return error(
            "insufficient_stock",
            f"Only {product.quantity_on_hand} {product.unit} of {product.sku} on hand; cannot remove {abs(change)}.",
        )

    state = tool_context.state
    number = int(state.get(STATE_PROPOSAL_COUNTER, 0) or 0) + 1
    proposal = MovementProposal(
        proposal_id=f"PROP-{number}",
        sku=product.sku,
        product_name=product.name,
        movement_type=mtype,  # type: ignore[arg-type]
        quantity_change=change,
        quantity_before=product.quantity_on_hand,
        quantity_after=after,
        reason=reason.strip(),
        created_invocation_id=str(getattr(tool_context, "invocation_id", "")),
    )
    # Re-assign whole values (not in-place edits) so ADK records the state change.
    proposals = dict(state.get(STATE_PROPOSALS) or {})
    proposals[proposal.proposal_id] = proposal.model_dump(mode="json")
    state[STATE_PROPOSALS] = proposals
    state[STATE_PROPOSAL_COUNTER] = number
    return MovementProposalResult(
        proposal=proposal, confirmation_phrase=confirmation_phrase(proposal.proposal_id)
    ).model_dump(mode="json")


def confirm_stock_movement(proposal_id: str, tool_context: ToolContext) -> dict:
    """Save a previously proposed stock movement. ONLY works after the user's explicit confirmation.

    Call this only when the user's latest message is exactly the confirmation phrase shown with
    the proposal, e.g. "Yes, confirm PROP-3". Any other wording ("ok", "do it", "approve") is
    refused in code; in that case tell the user the exact phrase to type.

    Args:
        proposal_id: The proposal to save, e.g. "PROP-3".

    Returns:
        MovementSavedResult with the saved movement and new stock level, or an error such as
        `confirmation_required`, `proposal_not_found`, `proposal_already_used`, `stale_proposal`.
    """
    pid = (proposal_id or "").strip().upper()

    # Rule 1: the USER's own message in this turn must be exactly "Yes, confirm <pid>".
    if confirmed_proposal_id(user_text(tool_context)) != pid:
        return error(
            "confirmation_required",
            f"Not saved. To save it, the user must reply exactly: {confirmation_phrase(pid or 'PROP-n')}",
        )

    state = tool_context.state
    proposals = dict(state.get(STATE_PROPOSALS) or {})
    proposal = proposal_or_none(proposals.get(pid))
    if proposal is None:
        return error("proposal_not_found", f"There is no proposal {pid} in this conversation.")
    # Rule 2: one use only.
    if proposal.used:
        return error("proposal_already_used", f"{pid} was already saved. Propose a new movement if needed.")
    # Rule 3: the proposal must come from an earlier turn, so the user has actually seen it.
    if proposal.created_invocation_id == str(getattr(tool_context, "invocation_id", "")):
        return error("confirmation_required", f"{pid} was just created; show it to the user and wait for confirmation.")

    try:
        movement, product = _store().apply_movement(
            sku=proposal.sku,
            movement_type=proposal.movement_type,
            quantity_change=proposal.quantity_change,
            reason=proposal.reason,
            performed_by=config.operator_name(),
            expected_before=proposal.quantity_before,
        )
        supplier_name = _store().suppliers().get(product.supplier_id)
    except StoreError as exc:
        # Nothing was saved (or it could not be verified): keep the proposal unused and report.
        return error(exc.code, exc.message)

    proposals[pid] = {**proposals[pid], "used": True}
    state[STATE_PROPOSALS] = proposals
    return MovementSavedResult(
        movement=movement,
        product=ProductView.from_product(product, supplier_name.name if supplier_name else "Unknown supplier"),
        low_stock_after_change=product.is_low_stock,
    ).model_dump(mode="json")


# Every tool the agent gets, in the order the model sees them.
ALL_TOOLS = [
    list_products,
    search_products,
    get_product,
    get_low_stock_report,
    get_inventory_summary,
    list_suppliers,
    list_stock_movements,
    propose_stock_movement,
    confirm_stock_movement,
]
