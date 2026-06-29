"""
Probe iikoWeb OLAP fields for expense monitor items that are missing from the
current Account.AccountHierarchySecond based P&L extract.

The script is meant to run in GitHub Actions with IIKO_WEB_* secrets. It writes
only field names, statuses, row counts, and matched public expense item labels.
"""

import json
import os
import re
import time
from datetime import datetime

import requests

from pnl_connector import STORE_IDS, WEB_HOST, web_login, web_logout


OUT_JSON = "olap_field_probe.json"
DATE_FROM = os.environ.get("PROBE_DATE_FROM", "2026-06-01")
DATE_TO = os.environ.get("PROBE_DATE_TO", "2026-07-01")
DEFAULT_PROBE_STORE_IDS = ["172412", "145308", "56178", "94945", "56188", "108119", "56458"]
PROBE_STORE_IDS = [
    int(part.strip())
    for part in os.environ.get("PROBE_STORE_IDS", ",".join(DEFAULT_PROBE_STORE_IDS)).split(",")
    if part.strip()
]
TIMEOUT = 90

BASE_FIELDS = [
    "Account.Type",
    "Account.AccountHierarchyTop",
    "Account.AccountHierarchySecond",
]

MISSING_ITEMS = [
    "Безлимитный фильтр",
    "Бракераж",
    "Настройка помола зерна",
    "Расходы на упаковку",
    "Недостача инвентаризации",
    "Излишки инвентаризации",
    "Потери/брак/порча",
    "Расходы на хоз.товары",
    "Поиск персонала",
    "Обучение персонала",
    "Медосмотр/ Медикаменты",
    "Развозка персонала",
    "Командировочные расходы",
]

CANDIDATE_FIELDS = [
    # Account hierarchy/name variants.
    "Account.Name",
    "Account",
    "Account.Id",
    "Account.Code",
    "Account.Number",
    "Account.Path",
    "Account.FullName",
    "Account.Hierarchy",
    "Account.AccountHierarchy",
    "Account.AccountHierarchyFirst",
    "Account.AccountHierarchyThird",
    "Account.AccountHierarchyFourth",
    "Account.AccountHierarchyFifth",
    "Account.AccountHierarchyLevel1",
    "Account.AccountHierarchyLevel2",
    "Account.AccountHierarchyLevel3",
    "Account.AccountHierarchyLevel4",
    # Transaction/document dimensions.
    "Transaction.Type",
    "Transaction.Name",
    "Transaction.Comment",
    "Transaction.Description",
    "Operation.Type",
    "Operation.Name",
    "Document.Type",
    "Document.Name",
    "Document.Number",
    "Document.Comment",
    "Document.Description",
    "Comment",
    "Description",
    # Product/item/write-off dimensions.
    "Product.Name",
    "Product.FullName",
    "Product.Group",
    "Product.Category",
    "Product.Type",
    "Product.Num",
    "Dish.Name",
    "Dish.Group",
    "Dish.Category",
    "Item.Name",
    "Item.Group",
    "Goods.Name",
    "Goods.Group",
    "Nomenclature.Name",
    "Nomenclature.Group",
    "Writeoff.Type",
    "Writeoff.Reason",
    "WriteoffReason.Name",
    "Reason.Name",
    "Inventory.Type",
    "Inventory.Name",
    # Counterparty/employee/cost center dimensions.
    "Contragent.Name",
    "Counteragent.Name",
    "Supplier.Name",
    "Employee.Name",
    "User.Name",
    "Department.Name",
    "CostCenter.Name",
    "CostCenter",
    "Store.Name",
]


def norm(value):
    value = str(value or "").lower().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9]+", "", value)


TARGET_NORMS = {norm(item): item for item in MISSING_ITEMS}


def request_json(session, method, path, **kwargs):
    resp = session.request(method, f"{WEB_HOST}{path}", timeout=TIMEOUT, **kwargs)
    if not resp.ok:
        raise requests.HTTPError(f"{method} {path}: HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def query_olap(session, group_fields, store_ids):
    body = {
        "storeIds": store_ids,
        "olapType": "TRANSACTIONS",
        "groupFields": group_fields,
        "dataFields": ["sum_signed"],
        "calculatedFields": [{
            "name": "sum_signed",
            "title": "Сумма",
            "description": "Сумма",
            "formula": "[Sum.Outgoing]-[Sum.Incoming]",
            "type": "MONEY",
            "canSum": True,
        }],
        "filters": [
            {
                "filterType": "date_range",
                "field": "DateTime.OperDayFilter",
                "dateFrom": DATE_FROM,
                "dateTo": DATE_TO,
                "includeLeft": True,
                "includeRight": True,
            },
            {
                "field": "Account.Group",
                "filterType": "value_list",
                "valueList": ["INCOME_EXPENSES"],
                "includeLeft": True,
                "includeRight": False,
                "inclusiveList": True,
            },
        ],
        "includeVoidTransactions": False,
        "includeNonBusinessPaymentTypes": False,
    }

    init = request_json(session, "POST", "/api/olap/init", json=body)
    if init.get("error"):
        return {"ok": False, "stage": "init", "error": init.get("errorMessage") or init.get("message")}

    token = init.get("data")
    if not token:
        return {"ok": False, "stage": "init", "error": "missing token"}

    status = "PENDING"
    status_resp = {}
    for _ in range(60):
        time.sleep(0.5)
        status_resp = request_json(session, "GET", f"/api/olap/fetch-status/{token}")
        status = status_resp.get("data")
        if status != "PENDING":
            break

    if status != "SUCCESS":
        return {
            "ok": False,
            "stage": "status",
            "error": status,
            "status_response": status_resp,
        }

    table = request_json(session, "GET", f"/api/olap/fetch/{token}/table")
    rows = ((table.get("result") or {}).get("rawData")) or []
    return {"ok": True, "rows": rows}


def summarize_rows(rows, field):
    values = {}
    matches = []
    for row in rows:
        value = row.get(field)
        if value is not None and str(value).strip():
            label = str(value).strip()
            values[label] = values.get(label, 0) + abs(row.get("sum_signed") or 0)

        haystack = " | ".join(str(v) for v in row.values() if v is not None)
        hay_norm = norm(haystack)
        for target_norm, target in TARGET_NORMS.items():
            if target_norm and target_norm in hay_norm:
                matches.append({
                    "target": target,
                    "field_value": value,
                    "sum_signed": row.get("sum_signed"),
                    "row": {k: row.get(k) for k in row if k in BASE_FIELDS or k == field},
                })

    top_values = sorted(values.items(), key=lambda kv: abs(kv[1]), reverse=True)[:25]
    return {
        "row_count": len(rows),
        "distinct_values": len(values),
        "top_values": [{"value": k, "amount": v} for k, v in top_values],
        "matches": matches[:50],
    }


def main():
    username = os.environ.get("IIKO_WEB_LOGIN") or os.environ.get("IIKO_LOGIN", "")
    password = os.environ.get("IIKO_WEB_PASSWORD") or os.environ.get("IIKO_PASSWORD", "")
    if not username or not password:
        raise RuntimeError("IIKO_WEB_LOGIN/IIKO_WEB_PASSWORD are required")

    report = {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "date_from": DATE_FROM,
        "date_to": DATE_TO,
        "probe_store_ids": PROBE_STORE_IDS,
        "missing_items": MISSING_ITEMS,
        "fields": [],
    }

    session = requests.Session()
    web_login(session, username, password)
    try:
        for field in CANDIDATE_FIELDS:
            group_fields = BASE_FIELDS + [field]
            print(f"probe {field}")
            try:
                result = query_olap(session, group_fields, PROBE_STORE_IDS)
            except Exception as exc:
                result = {"ok": False, "stage": "exception", "error": str(exc)[:300]}

            entry = {"field": field, "ok": bool(result.get("ok"))}
            if result.get("ok"):
                entry.update(summarize_rows(result["rows"], field))
            else:
                entry["stage"] = result.get("stage")
                entry["error"] = result.get("error")
            report["fields"].append(entry)
    finally:
        web_logout(session)

    valid_fields = [f["field"] for f in report["fields"] if f.get("ok")]
    fields_with_matches = [f["field"] for f in report["fields"] if f.get("matches")]
    report["summary"] = {
        "valid_field_count": len(valid_fields),
        "valid_fields": valid_fields,
        "fields_with_matches": fields_with_matches,
    }

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
