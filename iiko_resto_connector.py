"""
Коннектор iikoServer (resto/api) -> данные для дашборда продаж кофеен.

Это "классический" API iikoServer, доступный по адресу самого сервера
(в вашем случае https://kofeinya-garden-co.iiko.it), с авторизацией
по логину/паролю сотрудника iikoOffice.

ВАЖНО про безопасность:
- Пароль НЕ хранится в этом файле.
- Если заданы переменные окружения IIKO_LOGIN и IIKO_PASSWORD, скрипт
  использует их (для автоматического/фонового запуска по расписанию).
- Иначе скрипт спросит логин и пароль интерактивно (пароль не
  отображается на экране при вводе).
- В конце скрипт обязательно разлогинивается (logout), чтобы освободить
  слот лицензии.

Использование:
    python3 iiko_resto_connector.py

Что делает:
1. Логинится в iikoServer, получает токен (key).
2. Строит OLAP-отчёт по продажам за сегодня: выручка, кол-во заказов,
   средний чек — по точкам (Department) и по часам (HourOpen).
3. Строит OLAP-отчёт по продажам с начала месяца по сегодня — по точкам
   (для план/факт).
4. Строит топ позиций по выручке с начала месяца по сегодня.
5. Сохраняет всё в dashboard_data.json.
6. Разлогинивается.
"""

import getpass
import hashlib
import json
import os
from datetime import date, timedelta

import requests

HOST = os.environ.get("IIKO_HOST", "https://kofeinya-garden-co.iiko.it")
VERIFY_SSL = True  # если сервер с самоподписанным сертификатом, поставьте False


def login(host: str, username: str, password: str) -> str:
    """Авторизация: возвращает токен (key)."""
    pass_hash = hashlib.sha1(password.encode("utf-8")).hexdigest()
    resp = requests.get(
        f"{host}/resto/api/auth",
        params={"login": username, "pass": pass_hash},
        verify=VERIFY_SSL,
        timeout=20,
    )
    if not resp.ok:
        print(f"   HTTP {resp.status_code}, ответ сервера: {resp.text}")
    resp.raise_for_status()
    return resp.text.strip()


def logout(host: str, token: str) -> None:
    requests.get(
        f"{host}/resto/api/logout",
        params={"key": token},
        verify=VERIFY_SSL,
        timeout=20,
    )


def _olap(host: str, token: str, body: dict) -> dict:
    resp = requests.post(
        f"{host}/resto/api/v2/reports/olap",
        params={"key": token},
        json=body,
        verify=VERIFY_SSL,
        timeout=60,
    )
    if not resp.ok:
        print(f"   HTTP {resp.status_code}, ответ сервера: {resp.text[:1000]}")
    resp.raise_for_status()
    return resp.json()


def _olap_columns(host: str, token: str, report_type: str = "SALES") -> dict:
    """Возвращает каталог доступных колонок OLAP с локализованными названиями."""
    resp = requests.get(
        f"{host}/resto/api/v2/reports/olap/columns",
        params={"key": token, "reportType": report_type},
        verify=VERIFY_SSL,
        timeout=30,
    )
    if not resp.ok:
        print(f"   HTTP {resp.status_code}, ответ сервера: {resp.text[:1000]}")
    resp.raise_for_status()
    return resp.json()


def _find_olap_column(columns: dict, russian_name: str) -> str:
    """Находит техническое имя OLAP-поля по названию из интерфейса iikoOffice."""
    source = columns.get("data", columns) if isinstance(columns, dict) else columns
    entries = source.items() if isinstance(source, dict) else (
        (item.get("fieldName") or item.get("id"), item) for item in source
    )
    wanted = russian_name.casefold().replace("ё", "е").strip()
    for field_name, meta in entries:
        if not field_name or not isinstance(meta, dict):
            continue
        display_name = str(meta.get("name") or meta.get("title") or "")
        normalized = display_name.casefold().replace("ё", "е").strip()
        if normalized == wanted:
            return field_name
    raise KeyError(f"OLAP-поле «{russian_name}» не найдено")


def olap_sales_report(host: str, token: str, day: str, next_day: str) -> dict:
    """OLAP-отчёт по продажам за день: по точкам и по часам открытия."""
    body = {
        "reportType": "SALES",
        "groupByRowFields": ["Department", "HourOpen", "OrderType"],
        "groupByColFields": [],
        "aggregateFields": [
            "DishSumInt",
            "DishDiscountSumInt",
            "UniqOrderId.OrdersCount",
        ],
        "filters": {
            "OpenDate.Typed": {
                "filterType": "DateRange",
                "periodType": "CUSTOM",
                "from": day,
                "to": next_day,
            }
        },
    }
    return _olap(host, token, body)


def olap_mtd_report(host: str, token: str, month_start: str, next_day: str) -> dict:
    """OLAP-отчёт по продажам с начала месяца по сегодня: по точкам."""
    body = {
        "reportType": "SALES",
        "groupByRowFields": ["Department"],
        "groupByColFields": [],
        "aggregateFields": [
            "DishSumInt",
            "DishDiscountSumInt",
            "UniqOrderId.OrdersCount",
        ],
        "filters": {
            "OpenDate.Typed": {
                "filterType": "DateRange",
                "periodType": "CUSTOM",
                "from": month_start,
                "to": next_day,
            }
        },
    }
    return _olap(host, token, body)


def olap_month_daily_report(host: str, token: str, month_start: str, next_day: str) -> dict:
    """OLAP-отчёт по продажам с начала месяца (сгруппировано по дате)."""
    body = {
        "reportType": "SALES",
        "groupByRowFields": ["OpenDate.Typed"],
        "groupByColFields": [],
        "aggregateFields": ["DishSumInt", "DishDiscountSumInt"],
        "filters": {
            "OpenDate.Typed": {
                "filterType": "DateRange",
                "periodType": "CUSTOM",
                "from": month_start,
                "to": next_day,
            }
        },
    }
    return _olap(host, token, body)


def olap_discounts_report(host: str, token: str, day: str, next_day: str) -> dict:
    """OLAP-отчёт, повторяющий итог стандартного отчёта «Продажи по типам скидок»."""
    columns = _olap_columns(host, token)
    discount_type = _find_olap_column(columns, "Тип скидки")
    gross_sum = _find_olap_column(columns, "Сумма без скидки")
    net_sum = _find_olap_column(columns, "Сумма со скидкой")
    discount_sum = _find_olap_column(columns, "Сумма скидки")
    body = {
        "reportType": "SALES",
        "groupByRowFields": [discount_type],
        "groupByColFields": [],
        "aggregateFields": [gross_sum, net_sum, discount_sum],
        "filters": {
            "OpenDate.Typed": {
                "filterType": "DateRange",
                "periodType": "CUSTOM",
                "from": day,
                "to": next_day,
            }
        },
    }
    report = _olap(host, token, body)
    report["data"] = [
        {
            "discount_type": row.get(discount_type),
            "gross_sum": row.get(gross_sum, 0),
            "net_sum": row.get(net_sum, 0),
            "discount_sum": row.get(discount_sum, 0),
        }
        for row in report.get("data", [])
    ]
    return report


def olap_top_items(host: str, token: str, month_start: str, next_day: str) -> dict:
    """OLAP-отчёт: топ позиций с начала месяца (с группой блюда для фильтра летних напитков)."""
    body = {
        "reportType": "SALES",
        "groupByRowFields": ["DishName", "DishGroup"],
        "groupByColFields": [],
        "aggregateFields": ["DishSumInt", "DishAmountInt"],
        "filters": {
            "OpenDate.Typed": {
                "filterType": "DateRange",
                "periodType": "CUSTOM",
                "from": month_start,
                "to": next_day,
            }
        },
    }
    return _olap(host, token, body)


def main():
    today = date.today()
    today_str = today.isoformat()
    yesterday_str = (today - timedelta(days=1)).isoformat()
    tomorrow = (today + timedelta(days=1)).isoformat()
    month_start = today.replace(day=1).isoformat()

    username = os.environ.get("IIKO_LOGIN")
    password = os.environ.get("IIKO_PASSWORD")
    if not username:
        username = input("Логин iikoOffice: ").strip()
    if not password:
        password = getpass.getpass("Пароль: ")

    print("1. Логинимся в iikoServer...")
    token = login(HOST, username, password)
    print(f"   OK, токен получен (длина {len(token)})")

    try:
        print(f"2. Строим отчёт по продажам за {today_str}...")
        sales = olap_sales_report(HOST, token, today_str, tomorrow)

        print(f"2а. Строим почасовой отчёт за {yesterday_str}...")
        sales_yesterday = olap_sales_report(HOST, token, yesterday_str, today_str)

        print(f"3. Строим отчёт с начала месяца ({month_start} -> {today_str})...")
        sales_mtd = olap_mtd_report(HOST, token, month_start, tomorrow)

        print(f"4. Строим топ позиций с начала месяца ({month_start} -> {today_str})...")
        top_items = olap_top_items(HOST, token, month_start, tomorrow)

        print(f"5. Строим выручку по дням месяца ({month_start} -> {today_str})...")
        weekly = olap_month_daily_report(HOST, token, month_start, tomorrow)

        print(f"6. Строим отчёт о скидках за {today_str}...")
        try:
            discounts = olap_discounts_report(HOST, token, today_str, tomorrow)
        except Exception as e:
            print(f"   Предупреждение: отчёт о скидках недоступен ({e}), пропускаем")
            discounts = {"data": []}

        output = {
            "date": today_str,
            "month_start": month_start,
            "sales_raw": sales,
            "sales_yesterday_raw": sales_yesterday,
            "sales_mtd_raw": sales_mtd,
            "top_items_raw": top_items,
            "sales_weekly_raw": weekly,
            "discounts_raw": discounts,
        }

        out_path = os.path.join(os.path.dirname(__file__), "dashboard_data.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        print(f"Готово. Сырые данные сохранены в {out_path}")
    finally:
        print("6. Разлогиниваемся (освобождаем слот лицензии)...")
        logout(HOST, token)
        print("   OK")


if __name__ == "__main__":
    main()
