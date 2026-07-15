import json
import os
import re
import time
from datetime import datetime

import requests


WEB_HOST = os.environ.get("IIKO_WEB_HOST") or "https://kofeinya-garden-co-co.iikoweb.ru"
USERNAME = os.environ.get("IIKO_WEB_LOGIN") or os.environ.get("IIKO_LOGIN", "")
PASSWORD = os.environ.get("IIKO_WEB_PASSWORD") or os.environ.get("IIKO_PASSWORD", "")

DATE_FROM = os.environ.get("PROBE_DATE_FROM", "2026-06-01")
DATE_TO = os.environ.get("PROBE_DATE_TO", "2026-07-01")

STORE_IDS = [
    100421, 145308, 176065, 172412, 86753, 120401, 170714,
    178149, 115697, 56197, 56190, 80486, 87392, 56193, 80477,
    56188, 156443, 59619, 56178, 94945, 108119, 56458, 56203,
]
SAMPLE_STORE_IDS = [80477, 56178, 56203]

TARGET_NAMES = [
    "ИП Бокслер Обжарка",
    "Кейтеринг",
    "Московская",
    "Офис",
    "Склад Гарден ТМЦ/ОС",
    "Склад Гарден Урал ТМЦ/ОС",
    "Склад ГарденС ТМЦ/ОС",
    "Склад Республики Гарден Кофе Роастерс",
    "Софилэнд",
    "ТЦ Бариста",
    "ТЦ ЕКБ",
    "Цех (инвест)",
]

CANDIDATE_FIELDS = ["Department", "Department.Code", "Conception"]

METADATA_ENDPOINTS = [
    ("GET", "/api/olap/fields", None),
    ("GET", "/api/olap/fields?olapType=TRANSACTIONS", None),
    ("GET", "/api/olap/metadata", None),
    ("GET", "/api/olap/metadata?olapType=TRANSACTIONS", None),
    ("GET", "/api/olap/schema", None),
    ("GET", "/api/olap/schema/TRANSACTIONS", None),
    ("GET", "/api/olap/get-fields", None),
    ("POST", "/api/olap/get-fields", {"olapType": "TRANSACTIONS"}),
    ("POST", "/api/olap/fields", {"olapType": "TRANSACTIONS"}),
]


def norm(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


TARGET_NORMS = {norm(name): name for name in TARGET_NAMES}


def request_json(session, method, path, **kwargs):
    resp = session.request(method, f"{WEB_HOST}{path}", timeout=60, **kwargs)
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    return resp.status_code, payload, resp.text[:500]


def login(session):
    status, payload, text = request_json(
        session,
        "POST",
        "/api/auth/login",
        json={"login": USERNAME.strip(), "password": PASSWORD.strip()},
        headers={"disableCache": "true"},
    )
    if status >= 400 or not isinstance(payload, dict) or payload.get("error"):
        raise RuntimeError(f"auth failed: HTTP {status} {text}")


def olap_query(session, group_fields, store_ids_marker):
    body = {
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
    if isinstance(store_ids_marker, int):
        body["storeIds"] = [store_ids_marker]
    elif store_ids_marker == "all_known":
        body["storeIds"] = STORE_IDS
    elif store_ids_marker == "empty":
        body["storeIds"] = []

    status, init, text = request_json(session, "POST", "/api/olap/init", json=body)
    if status >= 400 or not isinstance(init, dict) or init.get("error"):
        message = init.get("errorMessage") if isinstance(init, dict) else text
        return {"ok": False, "stage": "init", "status": status, "message": message}

    token = init.get("data")
    if not token:
        return {"ok": False, "stage": "init", "status": status, "message": "no token"}

    final_status = "PENDING"
    for _ in range(80):
        time.sleep(0.75)
        _, status_payload, _ = request_json(session, "GET", f"/api/olap/fetch-status/{token}")
        final_status = status_payload.get("data") if isinstance(status_payload, dict) else None
        if final_status != "PENDING":
            break
    if final_status != "SUCCESS":
        return {"ok": False, "stage": "status", "message": final_status}

    status, table, text = request_json(session, "GET", f"/api/olap/fetch/{token}/table")
    rows = ((table or {}).get("result") or {}).get("rawData") or []
    return {"ok": True, "rows": rows[:3000], "row_count": len(rows)}


def summarize_rows(field, rows):
    values = {}
    matches = []
    for row in rows:
        value = row.get(field)
        if value is None:
            continue
        key = str(value)
        values[key] = values.get(key, 0) + (row.get("sum_signed") or 0)
        n = norm(key)
        if n in TARGET_NORMS or any(target in n for target in TARGET_NORMS):
            matches.append({"target": TARGET_NORMS.get(n, key), "value": key, "sum": row.get("sum_signed")})
    top_values = sorted(values.items(), key=lambda item: abs(item[1] or 0), reverse=True)[:40]
    return {
        "field": field,
        "unique_values": len(values),
        "matches": matches[:80],
        "sample_values": [{"value": key, "sum": total} for key, total in top_values],
    }


def compact_metadata(payload):
    text = json.dumps(payload, ensure_ascii=False) if payload is not None else ""
    hits = []
    for field in CANDIDATE_FIELDS:
        if field.lower() in text.lower():
            hits.append(field)
    return {"hits": hits, "text_head": text[:2000]}


def main():
    session = requests.Session()
    login(session)

    metadata = []
    for method, path, body in METADATA_ENDPOINTS:
        kwargs = {"json": body} if body is not None else {}
        try:
            status, payload, text = request_json(session, method, path, **kwargs)
            metadata.append({
                "endpoint": f"{method} {path}",
                "status": status,
                "errorMessage": payload.get("errorMessage") if isinstance(payload, dict) else None,
                "summary": compact_metadata(payload),
            })
        except Exception as exc:
            metadata.append({"endpoint": f"{method} {path}", "error": repr(exc)})

    field_results = []
    for field in CANDIDATE_FIELDS:
        samples = []
        for store_id in SAMPLE_STORE_IDS:
            result = olap_query(session, [field], store_id)
            if result.get("ok"):
                summary = summarize_rows(field, result.get("rows") or [])
                summary["store_id"] = store_id
                summary["row_count"] = result.get("row_count")
                samples.append(summary)
            else:
                samples.append({
                    "store_id": store_id,
                    "ok": False,
                    "stage": result.get("stage"),
                    "message": result.get("message"),
                })
                if result.get("stage") == "init":
                    break
        field_results.append({"field": field, "samples": samples})

    no_store_results = []
    for marker in ["omitted", "empty"]:
        result = olap_query(session, ["Account.AccountHierarchySecond"], marker)
        no_store_results.append({
            "storeIds": marker,
            "ok": result.get("ok"),
            "stage": result.get("stage"),
            "message": result.get("message"),
            "row_count": result.get("row_count"),
        })

    output = {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "period": {"from": DATE_FROM, "to": DATE_TO},
        "targets": TARGET_NAMES,
        "metadata": metadata,
        "field_results": field_results,
        "no_store_results": no_store_results,
    }

    all_known_scan = []
    for store_id in STORE_IDS:
        for field in CANDIDATE_FIELDS:
            result = olap_query(session, [field], store_id)
            if result.get("ok"):
                summary = summarize_rows(field, result.get("rows") or [])
                if summary["matches"]:
                    all_known_scan.append({
                        "store_id": store_id,
                        "field": field,
                        "matches": summary["matches"],
                        "sample_values": summary["sample_values"],
                    })
    output["all_known_scan_matches"] = all_known_scan

    with open("olap_dimension_probe.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    try:
        session.get(f"{WEB_HOST}/api/auth/logout", timeout=10)
    except Exception:
        pass


if __name__ == "__main__":
    main()
