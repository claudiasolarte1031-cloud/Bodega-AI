"""Pydantic v2 models for the inventory data and for every tool result.

Two groups of models live here:

1. **Data models** (`Supplier`, `Product`, `StockMovement`) validate what is read from and written
   to the JSON files in `data/`. A malformed file is reported as an error instead of being used.
2. **Output contracts** (`*Result` models) define the exact shape each tool returns to the model.
   Every tool builds one of these and returns `model_dump(mode="json")`, so the LLM always receives
   a typed, predictable structure. Every result has `status` = "ok" or "error".
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MovementType = Literal["receive", "issue", "adjust"]

# Shared timestamp pattern: ISO-8601 UTC with a trailing Z, e.g. 2026-10-26T12:00:00Z
UTC_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"


class _Strict(BaseModel):
    """Base class: unknown fields are rejected so typos in the JSON files are caught early."""

    model_config = ConfigDict(extra="forbid")


# =============================================================================================
# Data models (files in data/)
# =============================================================================================
class Supplier(_Strict):
    """A fictional supplier from data/suppliers.json."""

    supplier_id: str = Field(pattern=r"^SUP-\d{3}$")
    name: str = Field(min_length=1)
    contact_email: str
    phone: str
    lead_time_days: int = Field(ge=0, description="Days between ordering and delivery")


class Product(_Strict):
    """One stock-keeping unit (SKU) from data/inventory_db.json."""

    sku: str = Field(pattern=r"^SKU-\d{4}$")
    name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    unit: str = Field(min_length=1, description="Unit of measure: each, pair, roll, set...")
    quantity_on_hand: int = Field(ge=0)
    reorder_point: int = Field(ge=0, description="Reorder when stock is at or below this level")
    reorder_quantity: int = Field(gt=0, description="Standard quantity to order from the supplier")
    unit_cost: float = Field(ge=0)
    location: str
    supplier_id: str
    last_updated: str = Field(pattern=UTC_PATTERN)

    @property
    def is_low_stock(self) -> bool:
        """True when the product needs reordering."""
        return self.quantity_on_hand <= self.reorder_point


class StockMovement(_Strict):
    """One saved change to a product's stock level (data/stock_movements.json)."""

    movement_id: str = Field(pattern=r"^MOV-\d{6}$")
    sku: str
    movement_type: MovementType
    quantity_change: int = Field(description="Signed change: + for receive, - for issue, either for adjust")
    quantity_before: int = Field(ge=0)
    quantity_after: int = Field(ge=0)
    reason: str = Field(min_length=1)
    performed_by: str = Field(min_length=1)
    timestamp: str = Field(pattern=UTC_PATTERN)

    @model_validator(mode="after")
    def _check_math_and_sign(self) -> "StockMovement":
        """The arithmetic and the sign of the change must agree with the movement type."""
        if self.quantity_change == 0:
            raise ValueError("quantity_change cannot be zero")
        if self.quantity_before + self.quantity_change != self.quantity_after:
            raise ValueError("quantity_after must equal quantity_before + quantity_change")
        if self.movement_type == "receive" and self.quantity_change < 0:
            raise ValueError("a receive must increase stock")
        if self.movement_type == "issue" and self.quantity_change > 0:
            raise ValueError("an issue must decrease stock")
        return self


# =============================================================================================
# Output contracts (what tools return to the LLM)
# =============================================================================================
class ErrorResult(_Strict):
    """Returned by any tool that cannot do what was asked. The agent must report `message`."""

    status: Literal["error"] = "error"
    error_code: str = Field(description="Machine-readable reason, e.g. product_not_found")
    message: str = Field(description="Plain-language explanation for the user")


class ProductView(_Strict):
    """A product as shown to the user, with derived fields computed in code."""

    sku: str
    name: str
    category: str
    unit: str
    quantity_on_hand: int
    reorder_point: int
    location: str
    unit_cost: float
    stock_value: float = Field(description="quantity_on_hand x unit_cost, rounded to cents")
    low_stock: bool
    supplier_name: str

    @classmethod
    def from_product(cls, product: Product, supplier_name: str) -> "ProductView":
        """Build the view from a stored product."""
        return cls(
            sku=product.sku,
            name=product.name,
            category=product.category,
            unit=product.unit,
            quantity_on_hand=product.quantity_on_hand,
            reorder_point=product.reorder_point,
            location=product.location,
            unit_cost=product.unit_cost,
            stock_value=round(product.quantity_on_hand * product.unit_cost, 2),
            low_stock=product.is_low_stock,
            supplier_name=supplier_name,
        )


class ProductListResult(_Strict):
    """Result of list_products and search_products."""

    status: Literal["ok"] = "ok"
    count: int
    filters: dict = Field(default_factory=dict, description="Filters that were applied")
    products: List[ProductView]

    @field_validator("count")
    @classmethod
    def _non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("count cannot be negative")
        return v


class ProductDetailResult(_Strict):
    """Result of get_product: the product, its supplier and its most recent movements."""

    status: Literal["ok"] = "ok"
    product: ProductView
    supplier: Supplier
    recent_movements: List[StockMovement]


class ReorderLine(_Strict):
    """One line of the low-stock report: what to reorder and what it costs."""

    sku: str
    name: str
    quantity_on_hand: int
    reorder_point: int
    suggested_order_quantity: int
    estimated_cost: float
    supplier_id: str
    supplier_name: str
    lead_time_days: int
    out_of_stock: bool


class LowStockReportResult(_Strict):
    """Result of get_low_stock_report. A suggestion only; nothing is ordered."""

    status: Literal["ok"] = "ok"
    count: int
    total_estimated_cost: float
    lines: List[ReorderLine]


class CategoryTotal(_Strict):
    """Stock totals for one category."""

    category: str
    sku_count: int
    units_on_hand: int
    stock_value: float


class InventorySummaryResult(_Strict):
    """Result of get_inventory_summary."""

    status: Literal["ok"] = "ok"
    sku_count: int
    units_on_hand: int
    total_stock_value: float
    low_stock_count: int
    out_of_stock_count: int
    by_category: List[CategoryTotal]


class MovementListResult(_Strict):
    """Result of list_stock_movements (newest first)."""

    status: Literal["ok"] = "ok"
    count: int
    movements: List[StockMovement]


class MovementProposal(_Strict):
    """A validated but NOT yet saved stock movement, kept in the session state."""

    proposal_id: str = Field(pattern=r"^PROP-\d+$")
    sku: str
    product_name: str
    movement_type: MovementType
    quantity_change: int
    quantity_before: int
    quantity_after: int
    reason: str
    created_invocation_id: str = Field(description="ADK turn that created it; confirmation must come later")
    used: bool = False


class MovementProposalResult(_Strict):
    """Result of propose_stock_movement: what will happen and the exact phrase that confirms it."""

    status: Literal["ok"] = "ok"
    proposal: MovementProposal
    confirmation_phrase: str
    note: str = "Nothing has been saved yet."


class MovementSavedResult(_Strict):
    """Result of confirm_stock_movement after the store wrote and re-read the change."""

    status: Literal["ok"] = "ok"
    movement: StockMovement
    product: ProductView
    low_stock_after_change: bool


class SupplierListResult(_Strict):
    """Result of list_suppliers."""

    status: Literal["ok"] = "ok"
    count: int
    suppliers: List[Supplier]


def error(error_code: str, message: str) -> dict:
    """Shortcut used by tools: build and serialise an ErrorResult."""
    return ErrorResult(error_code=error_code, message=message).model_dump(mode="json")


def proposal_or_none(raw: Optional[dict]) -> Optional[MovementProposal]:
    """Rebuild a MovementProposal from session state, or None when absent or corrupted."""
    if not isinstance(raw, dict):
        return None
    try:
        return MovementProposal.model_validate(raw)
    except ValueError:
        return None
