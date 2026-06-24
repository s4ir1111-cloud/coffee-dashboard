"""
pnl_connector.py  —  P&L данные через iikoWeb API

API: https://kofeinya-garden-co-co.iikoweb.ru/api/
Auth: POST /api/auth  (cookie-based session)

Получает за текущий и прошлый год:
  1. Выручку из IIKO OLAP SALES (resto/api)
  2. Расходы по счетам 6.xx из iikoWeb Finance Journal

Сохраняет в pnl_data_raw.json

Безопасность:
    НИКОГДА не отправляйте содержимое iiko_credentials.sh в чат!
"""

import os, json, calendar, getpass, hashlib
from datetime import date, datetime
import requests

# ─── Конфиг ────────────────────────────────────────────────────────────────────
IIKO_HOST_OLD  = os.environ.get("IIKO_HOST",     "https://kofeinya-garden-co.iiko.it")
WEB_HOST       = "https://kofeinya-garden-co-co.iikoweb.ru"
USERNAME       = os.environ.get("IIKO_LOGIN",    "")
PASSWORD       = os.environ.get("IIKO_PASSWORD", "")
OUT_FILE       = "pnl_data_raw.json"
YEARS_BACK     = 1

# ─── iikoWeb Auth ───────────────────────────────────────────────────────────────
def web_login(session, username, password):
    """POST /api/auth → устанавливает session cookie"""
    # Пробуем несколько вариантов: plaintext, SHA1, MD5
    password_sha1 = hashlib.sha1(password.encode("utf-8")).hexdigest()
    password_md5  = hashlib.md5(password.encode("utf-8")).hexdigest()

    for pwd_variant in [password, password_sha1, password_md5]:
        resp = session.post(
            f"{WEB_HOST}/api/auth",
            json={"login": username, "password": pwd_variant},
            timeout=30
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("authorized"):
                print(f"  ✓ iikoWeb: {data.get('clientName')} / {data.get('user', {}).get('name')}")
                return data
        elif resp.status_code == 401:
            continue
        else:
            resp.raise_for_status()

    raise ValueError("iikoWeb: не удалось авторизоваться (проверьте логин/пароль)")

def web_logout(session):
    try:
        session.post(f"{WEB_HOST}/api/auth/logout", timeout=10)
    except Exception:
        pass

# ─── Получение справочников ─────────────────────────────────────────────────────
def get_expense_accounts(session):
    """Все расходные счета (type=EXPENSES)"""
    resp = session.get(f"{WEB_HOST}/api/dictionary/accounts?includeDeleted=false", timeout=30)
    resp.raise_for_status()
    all_accounts = resp.json().get("data", [])
    expense = [a for a in all_accounts if a.get("type") == "EXPENSES" and not a.get("deleted")]
    print(f"  Расходных счетов: {len(expense)}")
    return expense

def get_stores(session):
    """Список ресторанов/точек"""
    resp = session.get(f"{WEB_HOST}/api/stores/list", timeout=30)
    resp.raise_for_status()
    stores = resp.json().get("stores", [])
    print(f"  Точек: {len(stores)}")
    return stores

# ─── IIKO OLAP SALES (выручка) — старый API ────────────────────────────────────
def olap_login(host, username, password):
    ph = hashlib.sha1(password.encode("utf-8")).hexdigest()
    resp = requests.get(f"{host}/resto/api/auth",
                        params={"login": username, "pass": ph}, timeout=20)
    resp.raise_for_status()
    token = resp.text.strip()
    if not token or "Error" in token:
        raise ValueError(f"OLAP auth error: {token}")
    return token

def olap_logout(host, token):
    try:
        requests.get(f"{host}/resto/api/logout", params={"key": token}, timeout=10)
    except Exception:
        pass

def olap_sales(host, token, date_from, date_to):
    body = {
        "reportType": "SALES",
        "groupByRowFields": ["Department"],
        "groupByColFields": [],
        "aggregateFields": ["DishDiscountSumInt", "UniqOrderId.OrdersCount"],
        "filters": {
            "OpenDate.Typed": {
                "filterType": "DateRange", "periodType": "CUSTOM",
                "from": date_from, "to": date_to
            }
        }
    }
    resp = requests.post(f"{host}/resto/api/v2/reports/olap",
                         params={"key": token}, json=body, timeout=120)
    resp.raise_for_status()
    result = {}
    for row in resp.json().get("data", []):
        dept = row.get("Department", "").strip()
        if not dept:
            continue
        result[dept] = {
            "revenue": float(row.get("DishDiscountSumInt", 0) or 0),
            "orders":  int(row.get("UniqOrderId.OrdersCount", 0) or 0)
        }
    return result

# ─── iikoWeb Finance: транзакции по счёту ──────────────────────────────────────
def get_account_transactions(session, account_id, store_id, date_from, date_to):
    """
    POST /api/finance/account-transactions
    Возвращает список записей [{sum, date, department, cashFlowCategory, ...}]
    """
    all_records = []
    page = 1
    page_size = 200

    while True:
        resp = session.post(
            f"{WEB_HOST}/api/finance/account-transactions",
            json={
                "accountId": account_id,
                "storeId":   store_id,
                "from":      date_from,
                "to":        date_to,
                "pageSize":  page_size,
                "page":      page
            },
            timeout=60
        )
        if resp.status_code != 200:
            break
        data = resp.json().get("data", {})
        records = data.get("records", [])
        all_records.extend(records)
        if len(records) < page_size:
            break
        page += 1

    return all_records

# ─── Сбор расходов по счетам ────────────────────────────────────────────────────
def fetch_expenses(session, accounts, stores, date_from, date_to):
    """
    → {account_name: {store_name: sum}}
    """
    result = {}
    total_calls = len(accounts) * len(stores)
    call_no = 0

    for acct in accounts:
        acct_id   = acct["id"]
        acct_name = acct.get("name", {}).get("customValue", "") or acct.get("code", "")
        acct_code = acct.get("code", "")

        for store in stores:
            call_no += 1
            store_id   = store["id"]
            store_name = store.get("name", str(store_id))

            try:
                records = get_account_transactions(
                    session, acct_id, store_id, date_from, date_to
                )
                if records:
                    total = sum(float(r.get("sum", 0) or 0) for r in records)
                    if total != 0:
                        if acct_name not in result:
                            result[acct_name] = {}
                        result[acct_name][store_name] = (
                            result[acct_name].get(store_name, 0) + total
                        )
            except Exception as e:
                pass  # молча пропускаем ошибки по отдельным счетам

        if call_no % 50 == 0 or call_no == total_calls:
            filled = len([v for v in result.values() if v])
            print(f"      {call_no}/{total_calls}, счетов с данными: {filled}")

    return result

# ─── Утилиты дат ────────────────────────────────────────────────────────────────
def months_in_year(year):
    today = date.today()
    return [(year, m) for m in range(1, 13)
            if year < today.year or (year == today.year and m <= today.month)]

def month_date_range_iso(year, month):
    first = date(year, month, 1)
    last_excl = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return (
        first.strftime("%Y-%m-%dT00:00:00.000Z"),
        last_excl.strftime("%Y-%m-%dT00:00:00.000Z")
    )

def month_date_range_olap(year, month):
    first = date(year, month, 1)
    last_excl = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return first.strftime("%Y-%m-%d"), last_excl.strftime("%Y-%m-%d")

# ─── Основной сбор ─────────────────────────────────────────────────────────────
def fetch_all(web_session, olap_token):
    today = date.today()
    raw = {"years": {}}

    accounts = get_expense_accounts(web_session)
    stores   = get_stores(web_session)

    for year in [today.year - YEARS_BACK, today.year]:
        raw["years"][str(year)] = {}
        for (y, m) in months_in_year(year):
            key      = f"{y}-{str(m).zfill(2)}"
            iso_from, iso_to   = month_date_range_iso(y, m)
            olap_from, olap_to = month_date_range_olap(y, m)

            print(f"\n  {key} ({olap_from} → {olap_to})")

            month_data = {"sales": {}, "finance": {}}

            # 1. Выручка
            try:
                month_data["sales"] = olap_sales(IIKO_HOST_OLD, olap_token, olap_from, olap_to)
                total_rev = sum(v["revenue"] for v in month_data["sales"].values())
                print(f"    Sales: {len(month_data['sales'])} точек, {total_rev/1e6:.2f} млн ₽")
            except Exception as e:
                print(f"    ⚠ Sales ОШИБКА: {e}")

            # 2. Расходы
            try:
                print(f"    Finance: {len(accounts)} счетов × {len(stores)} точек...")
                expenses = fetch_expenses(web_session, accounts, stores, iso_from, iso_to)
                month_data["finance"] = expenses
                total_exp = sum(
                    v for dept in expenses.values() for v in dept.values()
                )
                print(f"    Finance: {len(expenses)} счетов с данными, {total_exp/1e6:.2f} млн ₽")
            except Exception as e:
                print(f"    ⚠ Finance ОШИБКА: {e}")

            raw["years"][str(year)][key] = month_data

    return raw

# ─── Запись ─────────────────────────────────────────────────────────────────────
def save(raw):
    raw["generated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    print(f"\n✓ Сохранено: {OUT_FILE}")

# ─── Точка входа ───────────────────────────────────────────────────────────────
def main():
    global USERNAME, PASSWORD
    if not USERNAME:
        USERNAME = input("IIKO логин: ")
    if not PASSWORD:
        PASSWORD = getpass.getpass("IIKO пароль: ")

    print(f"\n=== iikoWeb ({WEB_HOST}) ===")
    web_session = requests.Session()
    web_login(web_session, USERNAME, PASSWORD)

    print(f"\n=== OLAP API ({IIKO_HOST_OLD}) ===")
    olap_token = olap_login(IIKO_HOST_OLD, USERNAME, PASSWORD)
    print("  ✓ OLAP авторизован")

    try:
        raw = fetch_all(web_session, olap_token)
    finally:
        web_logout(web_session)
        olap_logout(IIKO_HOST_OLD, olap_token)
        print("\nТокены освобождены.")

    save(raw)

if __name__ == "__main__":
    main()
