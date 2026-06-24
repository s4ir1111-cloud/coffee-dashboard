"""
pnl_connector.py  —  P&L данные из IIKO для Finance BI дашборда

Получает из IIKO за текущий и прошлый год:
  1. SALES OLAP  → выручка + себестоимость блюд (DishDiscountSumInt, DishCostSumInt)
  2. FINANCE OLAP → статьи расходов ДДС (финансовый журнал операций)

Сохраняет в pnl_data_raw.json

Запуск:
    source iiko_credentials.sh && python pnl_connector.py

Безопасность:
    НИКОГДА не отправляйте содержимое iiko_credentials.sh в чат!
"""

import os, hashlib, json, calendar, getpass
from datetime import date, datetime
import requests

# ─── Конфиг ────────────────────────────────────────────────────────────────────
HOST     = os.environ.get("IIKO_HOST",     "https://kofeinya-garden-co.iiko.it")
USERNAME = os.environ.get("IIKO_LOGIN",    "")
PASSWORD = os.environ.get("IIKO_PASSWORD", "")
OUT_FILE = "pnl_data_raw.json"
YEARS_BACK = 1   # 1 год назад для г/г сравнения

# ─── Статьи расходов из IIKO (как они называются в системе) ───────────────────
# Если название в IIKO отличается — скорректируйте здесь
EXPENSE_ARTICLES = [
    "Безлимитный фильтр",
    "Бракераж",
    "Настройка помола зерна",
    "Расходы на упаковку",
    "Расход продуктов/Себестоимость",
    "Недостача инвентаризации",
    "Излишки инвентаризации",
    "Потери/брак/порча",
    "Расходы на хоз.товары",
    "ГСМ",
    "Ремонт и обслуживание помещений",
    "Коммунальные услуги",
    "ТО и ремонт оборудования, инвентаря",
    "Оформление торгового зала(дизайн и озеленение)",
    "Транспортные расходы",
    "Прочие ТМЦ списанные",
    "Инвентарь списанный",
    "Бой посуды",
    "Канцтовары",
    "Поиск персонала",
    "Обучение персонала",
    "Медосмотр/ Медикаменты",
    "Развозка персонала",
    "Командировочные расходы",
    "Бесплатная еда для сотрудников",
    "Бесплатные напитки для сотрудников",
    "Лицензии/ПО/сертификация",
    "Гостю",
    "Первый Гость и Подарок в День Рождения",
    "Расходы на рекламу, дизайн и маркетинг",
    "Представительские расходы",
    "Расходы из Амортизационного фонда",
    # Если ФОТ записан отдельной статьёй — добавьте её здесь:
    # "ФОТ", "Зарплата", "Выплата зарплаты",
]

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

# ─── SALES OLAP: выручка + себестоимость ────────────────────────────────────────
def olap_sales(host, token, date_from, date_to):
    """Выручка и заказы по точкам."""
    body = {
        "reportType": "SALES",
        "groupByRowFields": ["Department"],
        "groupByColFields": [],
        "aggregateFields": [
            "DishDiscountSumInt",    # Выручка (после скидок)
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

def parse_sales(data):
    """→ {dept: {revenue, orders}}"""
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

# ─── FINANCE OLAP: статьи расходов ─────────────────────────────────────────────
def olap_finance(host, token, date_from, date_to):
    """
    Финансовые операции из журнала IIKO.

    Правильные параметры (подтверждено диагностикой):
      reportType       = "TRANSACTIONS"
      groupByRowFields = ["Department", "CashFlowCategory"]
      aggregateFields  = ["Amount"]
      filter           = DateTime.DateTyped
    """
    body = {
        "reportType": "TRANSACTIONS",
        "groupByRowFields": ["Department", "CashFlowCategory"],
        "groupByColFields": [],
        "aggregateFields": ["Amount"],
        "filters": {
            "DateTime.DateTyped": {
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

def parse_finance(data):
    """→ {dept: {article: sum}}  (поле: CashFlowCategory / Amount)"""
    result = {}
    for row in data.get("data", []):
        dept    = row.get("Department", "").strip()
        article = (row.get("CashFlowCategory") or "").strip()
        amount  = float(row.get("Amount", 0) or 0)
        if not dept or not article:
            continue
        # В TRANSACTIONS расходы могут быть отрицательными — берём abs для расходных статей,
        # но сохраняем знак чтобы build_pnl_data.py мог отличить доходы от расходов
        if dept not in result:
            result[dept] = {}
        result[dept][article] = result[dept].get(article, 0) + amount

    # Лог
    all_articles = set()
    for d in result.values():
        all_articles.update(d.keys())
    if all_articles:
        print(f"    Найдено статей CashFlowCategory: {len(all_articles)}")
        for a in sorted(all_articles)[:10]:
            print(f"      - {a}")
        if len(all_articles) > 10:
            print(f"      ... и ещё {len(all_articles)-10}")
    else:
        print(f"    ⚠ Статей не найдено")
    return result

# ─── Утилиты дат ────────────────────────────────────────────────────────────────
def months_in_year(year):
    today = date.today()
    return [(year, m) for m in range(1, 13)
            if year < today.year or (year == today.year and m <= today.month)]

def month_date_range(year, month):
    first = date(year, month, 1)
    last_plus_one = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return first.strftime("%Y-%m-%d"), last_plus_one.strftime("%Y-%m-%d")

# ─── Основной сбор ─────────────────────────────────────────────────────────────
def fetch_all(host, token):
    today = date.today()
    raw = {"years": {}}

    for year in [today.year - YEARS_BACK, today.year]:
        raw["years"][str(year)] = {}
        for (y, m) in months_in_year(year):
            date_from, date_to = month_date_range(y, m)
            key = f"{y}-{str(m).zfill(2)}"
            print(f"\n  {key} ({date_from} → {date_to})")

            month_data = {"sales": {}, "finance": {}}

            # 1. Sales (выручка + себестоимость)
            try:
                sales_raw = olap_sales(host, token, date_from, date_to)
                month_data["sales"] = parse_sales(sales_raw)
                pts_count = len(month_data["sales"])
                total_rev = sum(v["revenue"] for v in month_data["sales"].values())
                print(f"    Sales: {pts_count} точек, {total_rev/1e6:.1f} млн ₽")
            except Exception as e:
                print(f"    ⚠ Sales ОШИБКА: {e}")

            # 2. Finance (статьи расходов из TRANSACTIONS OLAP)
            try:
                fin_raw = olap_finance(host, token, date_from, date_to)
                month_data["finance"] = parse_finance(fin_raw)
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

    print(f"\nПодключение к {HOST}...")
    token = login(HOST, USERNAME, PASSWORD)
    print("Авторизован. Начинаем сбор P&L данных...\n")

    try:
        raw = fetch_all(HOST, token)
    finally:
        logout(HOST, token)
        print("\nТокен освобождён.")

    save(raw)

if __name__ == "__main__":
    main()
