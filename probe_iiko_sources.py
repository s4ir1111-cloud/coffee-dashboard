#!/usr/bin/env python3
"""Safe iiko schema probe: stores field metadata, never credentials or tokens."""

import hashlib
import json
import os
from datetime import date, timedelta

import requests

HOST = os.environ.get("IIKO_HOST", "https://kofeinya-garden-co.iiko.it").rstrip("/")
LOGIN = os.environ.get("IIKO_LOGIN", "")
PASSWORD = os.environ.get("IIKO_PASSWORD", "")
OUT = "iiko_source_probe.json"
REPORT_TYPES = ["SALES", "TRANSACTIONS", "STOCK", "DELIVERIES"]
KEYWORDS = {
    "sales_checks": ["чек", "заказ", "order", "сумм", "dish", "блюд", "катег", "group"],
    "menu_sku": ["блюд", "dish", "товар", "product", "себесто", "cost", "катег", "group"],
    "purchase_prices": ["постав", "supplier", "приход", "purchase", "цена", "price", "документ"],
    "workforce": ["сотруд", "employee", "явк", "attendance", "смен", "shift", "час"],
    "raw_material": ["списан", "write", "расход", "consum", "ингреди", "ingredient", "товар", "product"],
}


def auth():
    if not LOGIN or not PASSWORD:
        raise RuntimeError("IIKO_LOGIN/IIKO_PASSWORD are required")
    response = requests.get(
        f"{HOST}/resto/api/auth",
        params={"login": LOGIN, "pass": hashlib.sha1(PASSWORD.encode()).hexdigest()},
        timeout=30,
    )
    response.raise_for_status()
    token = response.text.strip()
    if not token or "error" in token.lower():
        raise RuntimeError("iiko authentication failed")
    return token


def flatten_columns(payload):
    source = payload.get("data", payload) if isinstance(payload, dict) else payload
    result = []
    if isinstance(source, dict):
        iterator = source.items()
    elif isinstance(source, list):
        iterator = ((row.get("fieldName") or row.get("id") or "", row) for row in source)
    else:
        return result
    for field, meta in iterator:
        if not field or not isinstance(meta, dict):
            continue
        result.append({
            "field": field,
            "title": str(meta.get("name") or meta.get("title") or meta.get("caption") or ""),
            "type": str(meta.get("type") or meta.get("dataType") or ""),
        })
    return result


def relevant(columns):
    result = {key: [] for key in KEYWORDS}
    for row in columns:
        haystack = f"{row['field']} {row['title']}".casefold().replace("ё", "е")
        for domain, words in KEYWORDS.items():
            if any(word in haystack for word in words):
                result[domain].append(row)
    return result


def endpoint_status(token, path):
    try:
        response = requests.get(f"{HOST}{path}", params={"key": token}, timeout=20)
        return {"status": response.status_code, "content_type": response.headers.get("content-type", "")}
    except Exception as exc:
        return {"status": "error", "message": type(exc).__name__}


def main():
    token = auth()
    output = {"generated_at": date.today().isoformat(), "host": HOST, "reports": {}, "endpoints": {}}
    try:
        for report_type in REPORT_TYPES:
            response = requests.get(
                f"{HOST}/resto/api/v2/reports/olap/columns",
                params={"key": token, "reportType": report_type},
                timeout=30,
            )
            entry = {"http_status": response.status_code, "columns": 0, "all_columns": [], "relevant": {}}
            if response.ok:
                columns = flatten_columns(response.json())
                entry.update({"columns": len(columns), "all_columns": columns, "relevant": relevant(columns)})
            else:
                entry["error"] = response.text[:200]
            output["reports"][report_type] = entry
            print(f"{report_type}: HTTP {response.status_code}, {entry['columns']} columns")
        for path in [
            "/resto/api/employees", "/resto/api/employees/attendance",
            "/resto/api/attendance", "/resto/api/v2/attendance",
            "/resto/api/products", "/resto/api/suppliers",
        ]:
            output["endpoints"][path] = endpoint_status(token, path)
            print(f"{path}: {output['endpoints'][path]['status']}")
    finally:
        requests.get(f"{HOST}/resto/api/logout", params={"key": token}, timeout=20)
    with open(OUT, "w", encoding="utf-8") as target:
        json.dump(output, target, ensure_ascii=False, indent=2)
    print(f"Saved safe metadata to {OUT}")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        print(f"iiko probe failed: HTTP {status}")
        raise SystemExit(1)
    except requests.RequestException as exc:
        print(f"iiko probe failed: {type(exc).__name__}")
        raise SystemExit(1)
    except Exception as exc:
        print(f"iiko probe failed: {type(exc).__name__}")
        raise SystemExit(1)
