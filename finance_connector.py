"""
finance_connector.py  —  Исторические данные из IIKO для Finance BI

Стягивает помесячную выручку / заказы за текущий и прошлый год
по всем точкам, сохраняет в finance_data_raw.json.

Запуск:
    IIKO_HOST=... IIKO_LOGIN=... IIKO_PASSWORD=... python finance_connector.py
    # или
    source iiko_credentials.sh && python finance_connector.py

Безопасность:
    НИКОГДА не отправляйте содержимое iiko_credentials.sh в чат.
"""

import os, hashlib, json, calendar, getpass, sys
from datetime import date, datetime, timedelta
import requests

# ─── Конфиг ────────────────────────────────────────────────────────────────────
HOST     = os.environ.get("IIKO_HOST",     "https://kofeinya-garden-co.iiko.it")
USERNAME = os.environ.get("IIKO_LOGIN",    "")
PASSWORD = os.environ.get("IIKO_PASSWORD", "")
OUT_FILE = "finance_data_raw.json"

# Сколько лет назад тянуть данные для г/г сравнения
YEARS_BACK = 1

# ─── Auth ───────────────────────────────────────────────────────────────────────
def login(host, username, password):
    pass_hash = hashlib.sha1(password.encode("utf-8")).hexdigest()
    resp = requests.get(
        f"{host}/resto/api/auth",
        params={"login": username, "pass": pass_hash},
        timeout=20
    )
    resp.raise_for_status()
    token = resp.text.strip()
    if not token or "Error" in token:
        raise ValueError(f"Ошибка авторизации: {token}")
    return token

def logout(host, token):
    try:
        requests.get(f"{host}/resto/api/logout", params={"key": token}, timeout=10)
    except Exception:
        pass

# ─── OLAP запрос ────────────────────────────────────────────────────────────────
def olap(host, token, date_from, date_to):
    """
    Возвращает агрегированную выручку/заказы по точкам за период.
    date_from, date_to — строки "YYYY-MM-DD", to — НЕ включительно (IIKO-style).
    """
    body = {
        "reportType": "SALES",
        "groupByRowFields": ["Department"],
        "groupByColFields": [],
        "aggregateFields": [
            "DishSumInt",
            "DishDiscountSumInt",
            "UniqOrderId.OrdersCount"
        ],
        "filters": {
            "OpenDate.Typed": {
                "filterType": "DateRange",
                "periodType": "CUSTOM",
                "from": date_from,
                "to": date_to
            }
        }
    }
    resp = requests.post(
        f"{host}/resto/api/v2/reports/olap",
        params={"key": token},
        json=body,
        timeout=120
    )
    resp.raise_for_status()
    return resp.json()

# ─── Утилиты дат ────────────────────────────────────────────────────────────────
def months_in_year(year):
    """Список всех завершённых месяцев в году + текущий (незавершённый)."""
    today = date.today()
    months = []
    for month in range(1, 13):
        if year < today.year or (year == today.year and month <= today.month):
            months.append((year, month))
    return months

def month_date_range(year, month):
    """Возвращает (date_from, date_to) для IIKO — date_to = первое число следующего месяца."""
    first = date(year, month, 1)
    if month == 12:
        last_plus_one = date(year + 1, 1, 1)
    else:
        last_plus_one = date(year, month + 1, 1)
    return first.strftime("%Y-%m-%d"), last_plus_one.strftime("%Y-%m-%d")

# ─── Обработка данных ───────────────────────────────────────────────────────────
def parse_olap(data):
    """Парсит ответ OLAP в dict: {dept_name: {revenue, orders}}"""
    result = {}
    for row in data.get("data", []):
        dept = row.get("Department", "").strip()
        if not dept:
            continue
        revenue = float(row.get("DishDiscountSumInt", 0) or 0)
        orders  = int(row.get("UniqOrderId.OrdersCount", 0) or 0)
        if dept in result:
            result[dept]["revenue"] += revenue
            result[dept]["orders"]  += orders
        else:
            result[dept] = {"revenue": revenue, "orders": orders}
    return result

# ─── Основной сбор ─────────────────────────────────────────────────────────────
def fetch_all(host, token):
    today = date.today()
    current_year  = today.year
    previous_year = today.year - YEARS_BACK

    raw = {
        "years": {}
    }

    for year in [previous_year, current_year]:
        raw["years"][str(year)] = {}
        months = months_in_year(year)
        for (y, m) in months:
            date_from, date_to = month_date_range(y, m)
            key = f"{y}-{str(m).zfill(2)}"
            print(f"  Загрузка {key} ({date_from} → {date_to})...", end=" ", flush=True)
            try:
                data = olap(host, token, date_from, date_to)
                parsed = parse_olap(data)
                raw["years"][str(year)][key] = parsed
                total = sum(v["revenue"] for v in parsed.values())
                print(f"OK  ({len(parsed)} точек, {total/1e6:.1f} млн ₽)")
            except Exception as e:
                print(f"ОШИБКА: {e}")
                raw["years"][str(year)][key] = {}

    return raw

# ─── Запись результата ─────────────────────────────────────────────────────────
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

    print(f"Подключение к {HOST}...")
    token = login(HOST, USERNAME, PASSWORD)
    print("Авторизован. Начинаем сбор данных...\n")

    try:
        raw = fetch_all(HOST, token)
    finally:
        logout(HOST, token)
        print("Токен освобождён.")

    save(raw)

if __name__ == "__main__":
    main()
