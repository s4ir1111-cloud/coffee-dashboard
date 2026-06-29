"""
pnl_connector.py — iikoWeb P&L extractor for Garden Coffee.

Writes pnl_data_raw.json in the shape expected by build_pnl_data.py:
  months[YYYY-MM] = {
    from, to,
    summary:  PL_* KPI totals,
    by_store: PL_* KPI totals by store GUID,
    olap:     {storeId: [{Account.Type, Account.AccountHierarchyTop,
                          Account.AccountHierarchySecond, sum_signed, ...}]}
  }

This is a Python port of pnl_extract.js. It uses the same iikoWeb endpoints:
  POST /api/kpi/dashboard/get-data
  POST /api/olap/init
  GET  /api/olap/fetch-status/{token}
  GET  /api/olap/fetch/{token}/table
"""

import getpass
import json
import os
import time
from datetime import date, datetime

import requests


WEB_HOST = os.environ.get("IIKO_WEB_HOST") or "https://kofeinya-garden-co-co.iikoweb.ru"
USERNAME = os.environ.get("IIKO_WEB_LOGIN") or os.environ.get("IIKO_LOGIN", "")
PASSWORD = os.environ.get("IIKO_WEB_PASSWORD") or os.environ.get("IIKO_PASSWORD", "")
OUT_FILE = "pnl_data_raw.json"
START_YEAR = int(os.environ.get("PNL_START_YEAR", "2025"))
TIMEOUT = 90
BASE_OLAP_GROUP_FIELDS = [
    "Account.Type",
    "Account.AccountHierarchyTop",
    "Account.AccountHierarchySecond",
]
DETAILED_OLAP_GROUP_FIELDS = BASE_OLAP_GROUP_FIELDS + [
    "Account.AccountHierarchyThird",
    "Account.AccountHierarchyFourth",
    "Account.Name",
]
DETAILED_OLAP_SUPPORTED = None

STORE_IDS = [
    56203, 100421, 145308, 176065, 172412, 86753, 120401, 170714,
    178149, 115697, 56197, 56190, 80486, 87392, 56193, 80477,
    56188, 156443, 59619, 56178, 94945, 108119, 56458,
]

SUMMARY_METRICS = [
    "PL_SALES_TOTAL", "PL_COS_TOTAL", "PL_EXP_TOTAL",
    "PL_OTH_EXP_TOTAL", "PL_OTH_INCOME_TOTAL",
    "PL_PROFIT_GROSS", "PL_PROFIT_GROSS_PROC",
    "PL_PROFIT_MAIN", "PL_PROFIT_MAIN_PROC",
    "PL_PROFIT_NET", "PL_PROFIT_NET_PROC",
]


def month_range(year, month):
    date_from = f"{year}-{month:02d}-01"
    if month == 12:
        date_to = f"{year + 1}-01-01"
    else:
        date_to = f"{year}-{month + 1:02d}-01"
    return date_from, date_to


def months_to_collect():
    today = date.today()
    result = []
    for year in range(START_YEAR, today.year + 1):
        max_month = today.month if year == today.year else 12
        for month in range(1, max_month + 1):
            result.append((year, month, f"{year}-{month:02d}"))
    return result


def request_json(session, method, path, **kwargs):
    resp = session.request(method, f"{WEB_HOST}{path}", timeout=TIMEOUT, **kwargs)
    if not resp.ok:
        raise requests.HTTPError(f"{method} {path}: HTTP {resp.status_code}: {resp.text[:500]}")
    try:
        return resp.json()
    except ValueError as exc:
        raise RuntimeError(f"{method} {path}: JSON parse error: {resp.text[:200]}") from exc


def web_login(session, username, password):
    resp = session.post(
        f"{WEB_HOST}/api/auth/login",
        json={"login": username.strip(), "password": password.strip()},
        headers={"disableCache": "true"},
        timeout=30,
    )
    if not resp.ok:
        raise requests.HTTPError(f"POST /api/auth/login: HTTP {resp.status_code}: {resp.text[:500]}")

    data = resp.json()
    if data.get("error"):
        message = data.get("message") or data.get("errorMessage") or "unknown auth error"
        raise RuntimeError(f"iikoWeb auth failed: {message}")

    client = data.get("clientName") or data.get("serverName") or "iikoWeb"
    user = (data.get("user") or {}).get("name") or data.get("login") or username.strip()
    print(f"  OK iikoWeb auth: {client} / {user}")
    return data


def web_logout(session):
    try:
        session.post(f"{WEB_HOST}/api/auth/logout", timeout=10)
    except Exception:
        pass


def query_summary(session, date_from, date_to, data_type):
    data = request_json(session, "POST", "/api/kpi/dashboard/get-data", json={
        "dataType": data_type,
        "dateFrom": date_from,
        "dateTo": date_to,
        "metricCodes": SUMMARY_METRICS,
        "storeIds": STORE_IDS,
    })
    if data.get("error"):
        raise RuntimeError(data.get("errorMessage") or f"{data_type}: unknown KPI error")
    return data.get("data") or {}


def query_olap_with_fields(session, store_id, date_from, date_to, group_fields):
    body = {
        "storeIds": [store_id],
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
                "dateFrom": date_from,
                "dateTo": date_to,
                "includeLeft": True,
                "includeRight": True,
            },
            {
                "field": "Account.Group",
                "filterType": "value_list",
                "dateFrom": None,
                "dateTo": None,
                "valueMin": None,
                "valueMax": None,
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
        raise RuntimeError(init.get("errorMessage") or f"OLAP init error for store={store_id}")
    token = init.get("data")
    if not token:
        raise RuntimeError(f"OLAP init did not return token for store={store_id}")

    status = "PENDING"
    for _ in range(80):
        time.sleep(0.75)
        status_resp = request_json(session, "GET", f"/api/olap/fetch-status/{token}")
        status = status_resp.get("data")
        if status != "PENDING":
            break
    if status != "SUCCESS":
        raise RuntimeError(f"OLAP status={status} for store={store_id}")

    table = request_json(session, "GET", f"/api/olap/fetch/{token}/table")
    return ((table.get("result") or {}).get("rawData")) or []


def query_olap(session, store_id, date_from, date_to):
    global DETAILED_OLAP_SUPPORTED

    if DETAILED_OLAP_SUPPORTED is not False:
        try:
            rows = query_olap_with_fields(
                session,
                store_id,
                date_from,
                date_to,
                DETAILED_OLAP_GROUP_FIELDS,
            )
            DETAILED_OLAP_SUPPORTED = True
            return rows
        except Exception as exc:
            DETAILED_OLAP_SUPPORTED = False
            print(f"    INFO detailed OLAP account fields unavailable, using basic fields: {exc}")

    return query_olap_with_fields(session, store_id, date_from, date_to, BASE_OLAP_GROUP_FIELDS)


def fetch_month(session, year, month, key):
    date_from, date_to = month_range(year, month)
    entry = {"from": date_from, "to": date_to, "summary": None, "by_store": None, "olap": {}}

    entry["summary"] = query_summary(session, date_from, date_to, "DATA_TOTAL")
    sales = (entry["summary"].get("PL_SALES_TOTAL") or 0) / 1e6
    net_profit = (entry["summary"].get("PL_PROFIT_NET") or 0) / 1e6
    print(f"  {key}: revenue {sales:.1f}M, net profit {net_profit:.1f}M")

    entry["by_store"] = query_summary(session, date_from, date_to, "DATA_SUMMARY_BY_STORE")

    rows_total = 0
    errors = 0
    for store_id in STORE_IDS:
        try:
            rows = query_olap(session, store_id, date_from, date_to)
            if rows:
                entry["olap"][str(store_id)] = rows
                rows_total += len(rows)
        except Exception as exc:
            errors += 1
            if errors <= 8:
                print(f"    WARN OLAP {key} store={store_id}: {exc}")
        time.sleep(0.2)

    print(f"    OLAP: {rows_total} rows, {len(entry['olap'])} stores, {errors} errors")
    return entry


def fetch_all(session):
    raw = {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "iikoWeb-olap-python",
        "store_ids": STORE_IDS,
        "months": {},
    }

    months = months_to_collect()
    print(f"Collecting {len(months)} months from {START_YEAR} to {date.today().year}")
    for year, month, key in months:
        raw["months"][key] = fetch_month(session, year, month, key)
    return raw


def save(raw):
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    olap_months = sum(1 for month in raw["months"].values() if month.get("olap"))
    print(f"Saved {OUT_FILE}: {len(raw['months'])} months, {olap_months} months with OLAP")


def main():
    global USERNAME, PASSWORD
    if not USERNAME:
        USERNAME = input("IIKO Web login: ").strip()
    if not PASSWORD:
        PASSWORD = getpass.getpass("IIKO Web password: ")

    session = requests.Session()
    print(f"Connecting to {WEB_HOST}")
    web_login(session, USERNAME, PASSWORD)
    try:
        save(fetch_all(session))
    finally:
        web_logout(session)
        print("Session closed.")


if __name__ == "__main__":
    main()
