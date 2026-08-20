#!/usr/bin/env python3
"""Extract monthly sales, SKU, purchasing and raw-material data from iikoServer."""

import hashlib
import json
import os
from datetime import date

import requests

HOST = os.environ.get("IIKO_HOST", "https://kofeinya-garden-co.iiko.it").rstrip("/")
LOGIN = os.environ.get("IIKO_LOGIN", "")
PASSWORD = os.environ.get("IIKO_PASSWORD", "")
MONTH = os.environ.get("IIKO_ANALYTICS_MONTH", "2026-07")
OUT = "iiko_extended_data.json"


def month_range(key):
    year, month = map(int, key.split("-"))
    return f"{key}-01", f"{year + (month == 12):04d}-{1 if month == 12 else month + 1:02d}-01"


def login():
    response = requests.get(f"{HOST}/resto/api/auth", params={
        "login": LOGIN, "pass": hashlib.sha1(PASSWORD.encode()).hexdigest()
    }, timeout=30)
    response.raise_for_status()
    return response.text.strip()


def olap(token, report_type, rows, aggregates, date_field, start, end):
    response = requests.post(
        f"{HOST}/resto/api/v2/reports/olap", params={"key": token}, timeout=180,
        json={
            "reportType": report_type, "groupByRowFields": rows,
            "groupByColFields": [], "aggregateFields": aggregates,
            "filters": {date_field: {"filterType": "DateRange", "periodType": "CUSTOM", "from": start, "to": end}},
        },
    )
    response.raise_for_status()
    return response.json().get("data", [])


def main():
    start, end = month_range(MONTH)
    token = login()
    output = {"generated_at": date.today().isoformat(), "period": MONTH, "sources": {}}
    try:
        queries = {
            "sales_checks": ("SALES", ["Department"], ["DishDiscountSumInt", "UniqOrderId.OrdersCount"], "OpenDate.Typed"),
            "menu_sku": ("SALES", ["Department", "DishName", "DishGroup", "DishCategory"], ["DishAmountInt", "DishDiscountSumInt", "ProductCostBase.ProductCost"], "OpenDate.Typed"),
            "sales_by_hour": ("SALES", ["Department", "OpenDate.Typed", "HourOpen"], ["DishDiscountSumInt", "UniqOrderId.OrdersCount"], "OpenDate.Typed"),
            "purchases": ("TRANSACTIONS", ["DateTime.DateTyped", "Department", "Counteragent.Name", "Product.Id", "Product.Name", "Product.MeasureUnit", "InvoiceNumber"], ["Amount.In", "Sum.Incoming", "Product.AvgSum"], "DateTime.DateTyped"),
            "raw_material": ("TRANSACTIONS", ["DateTime.DateTyped", "Department", "Product.Id", "Product.Name", "Product.MeasureUnit", "Account.Name", "Contr-Account.Name"], ["Amount.Out", "Sum.Outgoing"], "DateTime.DateTyped"),
        }
        for name, args in queries.items():
            try:
                rows = olap(token, *args, start, end)
                output["sources"][name] = {"status": "available", "rows": rows}
                print(f"{name}: {len(rows)} rows")
            except Exception as exc:
                output["sources"][name] = {"status": "error", "error": type(exc).__name__, "rows": []}
                print(f"{name}: {type(exc).__name__}")
    finally:
        requests.get(f"{HOST}/resto/api/logout", params={"key": token}, timeout=20)
    with open(OUT, "w", encoding="utf-8") as target:
        json.dump(output, target, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"extended connector failed: {type(exc).__name__}")
        raise SystemExit(1)
