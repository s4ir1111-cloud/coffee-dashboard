"""
pnl_connector.py  —  P&L данные через iikoWeb KPI Dashboard API

API: https://kofeinya-garden-co-co.iikoweb.ru/api/
Auth: POST /api/auth (cookie-based session)

Использует предрассчитанные P&L метрики IIKO:
  POST /api/kpi/dashboard/get-data  (dataType=DATA_TOTAL)

Метрики:
  PL_SALES_TOTAL       Выручка
  PL_COS_TOTAL         Себестоимость
  PL_EXP_TOTAL         Расходы
  PL_OTH_EXP_TOTAL     Прочие расходы
  PL_OTH_INCOME_TOTAL  Прочие доходы
  PL_PROFIT_GROSS      Валовая прибыль
  PL_PROFIT_GROSS_PROC Маржа валовой прибыли
  PL_PROFIT_MAIN       Операционная прибыль
  PL_PROFIT_MAIN_PROC  Маржа операционной прибыли
  PL_PROFIT_NET        Чистая прибыль
  PL_PROFIT_NET_PROC   Маржа чистой прибыли

Сохраняет в pnl_data_raw.json

БЕЗОПАСНОСТЬ: НИКОГДА не отправляйте содержимое iiko_credentials.sh в чат!
"""

import os, json, hashlib, getpass
from datetime import date, datetime
import requests

# ─── Конфиг ─────────────────────────────────────────────────────────────────
WEB_HOST = "https://kofeinya-garden-co-co.iikoweb.ru"
USERNAME = os.environ.get("IIKO_LOGIN", "")
PASSWORD = os.environ.get("IIKO_PASSWORD", "")
OUT_FILE = "pnl_data_raw.json"
START_YEAR = 2025

# Все 23 точки Garden Coffee
STORE_IDS = [
    56203, 100421, 145308, 176065, 172412, 86753,  120401, 170714,
    178149, 115697, 56197,  56190,  80486,  87392,  56193,  80477,
    56188,  156443, 59619,  56178,  94945,  108119, 56458
]

SUMMARY_METRICS = [
    "PL_SALES_TOTAL", "PL_COS_TOTAL", "PL_EXP_TOTAL",
    "PL_OTH_EXP_TOTAL", "PL_OTH_INCOME_TOTAL",
    "PL_PROFIT_GROSS", "PL_PROFIT_GROSS_PROC",
    "PL_PROFIT_MAIN",  "PL_PROFIT_MAIN_PROC",
    "PL_PROFIT_NET",   "PL_PROFIT_NET_PROC"
]

# ─── Auth ─────────────────────────────────────────────────────────────────────
def web_login(session, username, password):
    """POST /api/auth — пробуем SHA256, SHA1, MD5, plaintext"""
    variants = {
        "SHA256": hashlib.sha256(password.encode()).hexdigest(),
        "SHA1":   hashlib.sha1(password.encode()).hexdigest(),
        "MD5":    hashlib.md5(password.encode()).hexdigest(),
        "plain":  password,
    }
    for name, pwd in variants.items():
        resp = session.post(f"{WEB_HOST}/api/auth",
                            json={"login": username, "password": pwd}, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("authorized"):
                print(f"  ✓ iikoWeb авторизован ({name}): {data.get('clientName')}")
                return data
        elif resp.status_code == 401:
            continue
        else:
            resp.raise_for_status()
    raise ValueError("iikoWeb: не удалось авторизоваться. "
                     "Если проблема сохраняется — используйте JS-экстрактор pnl_extract_new.js")

def web_logout(session):
    try:
        session.post(f"{WEB_HOST}/api/auth/logout", timeout=10)
    except Exception:
        pass

# ─── P&L данные за месяц ──────────────────────────────────────────────────────
def fetch_month_pnl(session, year, month):
    """DATA_TOTAL для одного месяца → dict с PL_* метриками"""
    date_from = f"{year}-{month:02d}-01"
    if month == 12:
        date_to = f"{year + 1}-01-01"
    else:
        date_to = f"{year}-{month + 1:02d}-01"

    resp = session.post(
        f"{WEB_HOST}/api/kpi/dashboard/get-data",
        json={
            "dataType":    "DATA_TOTAL",
            "dateFrom":    date_from,
            "dateTo":      date_to,
            "metricCodes": SUMMARY_METRICS,
            "storeIds":    STORE_IDS
        },
        timeout=30
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("error"):
        raise ValueError(data.get("errorMessage", "unknown error"))

    return data.get("data", {})

# ─── Месяцы для сбора ─────────────────────────────────────────────────────────
def months_to_collect():
    today = date.today()
    result = []
    for year in range(START_YEAR, today.year + 1):
        last_month = today.month if year == today.year else 12
        for month in range(1, last_month + 1):
            result.append((year, month))
    return result

# ─── Основной сбор ────────────────────────────────────────────────────────────
def fetch_all(session):
    raw = {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "store_ids":    STORE_IDS,
        "months":       {}
    }

    months = months_to_collect()
    print(f"\n  Месяцев для сбора: {len(months)}")

    for year, month in months:
        key = f"{year}-{month:02d}"
        try:
            data = fetch_month_pnl(session, year, month)
            raw["months"][key] = data
            sales = data.get("PL_SALES_TOTAL", 0) / 1e6
            gp    = data.get("PL_PROFIT_GROSS_PROC", 0) * 100
            op    = data.get("PL_PROFIT_MAIN_PROC", 0) * 100
            print(f"  ✓ {key}: {sales:.1f}M₽  вал.прибыль {gp:.1f}%  опер. {op:.1f}%")
        except Exception as e:
            print(f"  ⚠ {key}: {e}")
            raw["months"][key] = None

    return raw

# ─── Сохранение ───────────────────────────────────────────────────────────────
def save(raw):
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    months_ok = sum(1 for v in raw["months"].values() if v)
    print(f"\n✓ Сохранено: {OUT_FILE}  ({months_ok} месяцев с данными)")

# ─── Точка входа ──────────────────────────────────────────────────────────────
def main():
    global USERNAME, PASSWORD
    if not USERNAME:
        USERNAME = input("IIKO логин: ")
    if not PASSWORD:
        PASSWORD = getpass.getpass("IIKO пароль: ")

    session = requests.Session()
    print(f"\n=== iikoWeb ({WEB_HOST}) ===")
    web_login(session, USERNAME, PASSWORD)

    try:
        raw = fetch_all(session)
    finally:
        web_logout(session)
        print("Сессия закрыта.")

    save(raw)

if __name__ == "__main__":
    main()
