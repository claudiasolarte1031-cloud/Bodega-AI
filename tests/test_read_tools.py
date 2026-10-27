"""Tests for the read-only tools: correct numbers, filters, typed output and error handling."""

import json

from agents.inventory_agent import tools
from agents.inventory_agent.models import (
    ErrorResult,
    InventorySummaryResult,
    LowStockReportResult,
    MovementListResult,
    ProductDetailResult,
    ProductListResult,
    SupplierListResult,
)


def test_list_products_returns_all_products_as_typed_result(data_dir):
    result = tools.list_products()
    ProductListResult.model_validate(result)          # output contract holds
    assert result["status"] == "ok"
    assert result["count"] == 10


def test_list_products_filters_by_category_case_insensitive(data_dir):
    result = tools.list_products(category="packaging")
    assert {p["sku"] for p in result["products"]} == {"SKU-1005", "SKU-1006", "SKU-1007"}


def test_list_products_low_stock_only(data_dir):
    result = tools.list_products(low_stock_only=True)
    # SKU-1002 (6<=8), 1004 (9<=15), 1006 (35<=40), 1008 (0<=5)
    assert {p["sku"] for p in result["products"]} == {"SKU-1002", "SKU-1004", "SKU-1006", "SKU-1008"}
    assert all(p["low_stock"] for p in result["products"])


def test_stock_value_is_computed_in_code(data_dir):
    drill = next(p for p in tools.list_products()["products"] if p["sku"] == "SKU-1001")
    assert drill["stock_value"] == round(14 * 79.90, 2)
    assert drill["supplier_name"].startswith("Adventure Works Tools")


def test_search_products_matches_name_and_rejects_empty_query(data_dir):
    assert [p["sku"] for p in tools.search_products("drill")["products"]] == ["SKU-1001", "SKU-1002"]
    empty = tools.search_products("   ")
    ErrorResult.model_validate(empty)
    assert empty["error_code"] == "empty_query"


def test_get_product_includes_supplier_and_recent_movements(data_dir):
    result = tools.get_product("sku-1004")              # case-insensitive
    ProductDetailResult.model_validate(result)
    assert result["product"]["quantity_on_hand"] == 9
    assert result["supplier"]["supplier_id"] == "SUP-001"
    assert result["recent_movements"][0]["movement_id"] == "MOV-000002"


def test_get_product_unknown_sku_is_an_error_not_an_exception(data_dir):
    result = tools.get_product("SKU-9999")
    assert result == {**result, "status": "error", "error_code": "product_not_found"}


def test_low_stock_report_suggests_quantities_and_costs(data_dir):
    result = tools.get_low_stock_report()
    LowStockReportResult.model_validate(result)
    lines = {l["sku"]: l for l in result["lines"]}
    assert result["lines"][0]["sku"] == "SKU-1008"            # out of stock comes first
    assert lines["SKU-1008"]["out_of_stock"] is True
    assert lines["SKU-1008"]["suggested_order_quantity"] == 15
    assert lines["SKU-1002"]["estimated_cost"] == round(25 * 24.50, 2)
    assert result["total_estimated_cost"] == round(sum(l["estimated_cost"] for l in result["lines"]), 2)


def test_low_stock_report_orders_enough_multiples(data_dir):
    # Make a deep shortfall: 0 on hand, reorder point 100, reorder quantity 40 -> 3 x 40 = 120.
    path = data_dir / "inventory_db.json"
    data = json.loads(path.read_text())
    data["products"][0].update(quantity_on_hand=0, reorder_point=100, reorder_quantity=40)
    path.write_text(json.dumps(data))
    line = next(l for l in tools.get_low_stock_report()["lines"] if l["sku"] == "SKU-1001")
    assert line["suggested_order_quantity"] == 120


def test_inventory_summary_totals(data_dir):
    result = tools.get_inventory_summary()
    InventorySummaryResult.model_validate(result)
    assert result["sku_count"] == 10
    assert result["low_stock_count"] == 4
    assert result["out_of_stock_count"] == 1
    assert {c["category"] for c in result["by_category"]} == {"Electrical", "Packaging", "Safety", "Tools"}


def test_list_suppliers(data_dir):
    result = tools.list_suppliers()
    SupplierListResult.model_validate(result)
    assert result["count"] == 3


def test_list_stock_movements_newest_first_with_limit_and_filter(data_dir):
    result = tools.list_stock_movements(limit=2)
    MovementListResult.model_validate(result)
    assert [m["movement_id"] for m in result["movements"]] == ["MOV-000004", "MOV-000003"]
    assert tools.list_stock_movements(sku="SKU-1004")["count"] == 1
    assert tools.list_stock_movements(limit=0)["error_code"] == "invalid_limit"
    assert tools.list_stock_movements(sku="SKU-0000")["error_code"] == "product_not_found"


def test_missing_data_file_is_reported(data_dir):
    (data_dir / "inventory_db.json").unlink()
    assert tools.list_products()["error_code"] == "data_missing"


def test_corrupt_data_file_is_reported(data_dir):
    (data_dir / "inventory_db.json").write_text("{not json")
    assert tools.get_inventory_summary()["error_code"] == "data_unreadable"


def test_invalid_record_is_reported(data_dir):
    path = data_dir / "inventory_db.json"
    data = json.loads(path.read_text())
    data["products"][0]["quantity_on_hand"] = -5
    path.write_text(json.dumps(data))
    assert tools.list_products()["error_code"] == "data_invalid"
