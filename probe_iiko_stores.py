import json
import os
import re
import time

import requests


WEB_HOST = os.environ.get("IIKO_WEB_HOST") or "https://kofeinya-garden-co-co.iikoweb.ru"
USERNAME = os.environ.get("IIKO_WEB_LOGIN") or os.environ.get("IIKO_LOGIN", "")
PASSWORD = os.environ.get("IIKO_WEB_PASSWORD") or os.environ.get("IIKO_PASSWORD", "")
TARGETS = [
    "Кейтеринг",
    "Офис",
    "Склад Гарден ТМЦ/ОС",
    "Склад Гарден Урал ТМЦ/ОС",
    "Склад ГарденС ТМЦ/ОС",
    "Склад Республики Гарден Кофе Роастерс",
    "ТЦ Бариста",
    "ТЦ ЕКБ",
]


def request_json(session, method, path, **kwargs):
    resp = session.request(method, f"{WEB_HOST}{path}", timeout=45, **kwargs)
    return {
        "status": resp.status_code,
        "text": resp.text[:2000],
        "json": try_json(resp),
    }


def try_json(resp):
    try:
        return resp.json()
    except ValueError:
        return None


def walk(value, path="$"):
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            yield from walk(child, f"{path}[{idx}]")


def text_has_target(value):
    text = json.dumps(value, ensure_ascii=False).lower()
    return any(target.lower() in text for target in TARGETS)


def compact_match(path, value):
    if not isinstance(value, dict):
        return None
    keys = {"id", "storeId", "restaurantId", "guid", "name", "title", "caption", "code"}
    if not any(k in value for k in keys):
        return None
    return {
        "path": path,
        "id": value.get("id"),
        "storeId": value.get("storeId"),
        "restaurantId": value.get("restaurantId"),
        "guid": value.get("guid") or value.get("id"),
        "name": value.get("name") or value.get("title") or value.get("caption"),
        "code": value.get("code"),
        "raw": {k: v for k, v in value.items() if k in keys or isinstance(v, (str, int, float, bool, type(None)))},
    }


def main():
    session = requests.Session()
    login = session.post(
        f"{WEB_HOST}/api/auth/login",
        json={"login": USERNAME.strip(), "password": PASSWORD.strip()},
        headers={"disableCache": "true"},
        timeout=30,
    )
    data = login.json()
    if data.get("error"):
        raise RuntimeError(data.get("message") or "iiko auth failed")

    probes = []
    endpoints = [
        ("GET", "/api/auth", None),
        ("GET", "/api/config/get", None),
        ("GET", "/api/stores/list", None),
        ("GET", "/api/kpi-metric/stores", None),
        ("POST", "/api/kpi/dashboard/get-data", {
            "dataType": "DATA_SUMMARY_BY_STORE",
            "dateFrom": "2026-07-01",
            "dateTo": "2026-08-01",
            "metricCodes": ["PL_SALES_TOTAL", "PL_EXP_TOTAL", "PL_PROFIT_NET"],
        }),
        ("POST", "/api/kpi/dashboard/get-data", {
            "dataType": "DATA_SUMMARY_BY_STORE",
            "dateFrom": "2026-07-01",
            "dateTo": "2026-08-01",
            "metricCodes": ["PL_SALES_TOTAL", "PL_EXP_TOTAL", "PL_PROFIT_NET"],
            "storeIds": [],
        }),
        ("GET", "/api/kpi/dashboard/get-filter-data", None),
        ("POST", "/api/kpi/dashboard/get-filter-data", {}),
        ("GET", "/api/kpi/dashboard/filters", None),
        ("POST", "/api/kpi/dashboard/filters", {}),
        ("GET", "/api/kpi/dashboard/get-settings", None),
        ("GET", "/api/restaurants", None),
        ("GET", "/api/restaurant", None),
        ("GET", "/api/organization/restaurant/list", None),
        ("GET", "/api/olap/dictionaries", None),
    ]

    for method, path, body in endpoints:
        try:
            result = request_json(session, method, path, json=body) if method == "POST" else request_json(session, method, path)
        except Exception as exc:
            result = {"error": repr(exc)}
        probes.append({"method": method, "path": path, "result": result})
        time.sleep(0.2)

    matches = []
    for probe in probes:
        payload = probe.get("result", {}).get("json")
        if payload is None:
            continue
        for path, value in walk(payload):
            if text_has_target(value):
                match = compact_match(path, value)
                if match:
                    match["endpoint"] = f"{probe['method']} {probe['path']}"
                    matches.append(match)

    all_name_like = []
    for probe in probes:
        payload = probe.get("result", {}).get("json")
        if payload is None:
            continue
        for path, value in walk(payload):
            match = compact_match(path, value)
            if not match:
                continue
            name = match.get("name")
            if name and re.search(r"склад|кейтер|офис|бариста|екб|гарден", str(name), re.I):
                match["endpoint"] = f"{probe['method']} {probe['path']}"
                all_name_like.append(match)

    output = {
        "targets": TARGETS,
        "matches": matches,
        "name_like": all_name_like,
        "probe_summary": [
            {
                "endpoint": f"{p['method']} {p['path']}",
                "status": p.get("result", {}).get("status"),
                "has_json": p.get("result", {}).get("json") is not None,
                "text_head": p.get("result", {}).get("text", "")[:300],
                "error": p.get("result", {}).get("error"),
            }
            for p in probes
        ],
    }

    with open("iiko_store_probe.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    try:
        session.post(f"{WEB_HOST}/api/auth/logout", timeout=10)
    except Exception:
        pass


if __name__ == "__main__":
    main()
