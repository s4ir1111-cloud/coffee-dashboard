"""
Преобразует сырые данные IIKO (dashboard_data.json, из iiko_resto_connector.py)
в формат, который умеет показывать coffee_dashboard.html.

Использование:
    python3 build_dashboard_data.py

Вход:
  dashboard_data.json   (сырые OLAP-отчёты)
  plans.json            (месячные планы по точкам)
Выход:
  dashboard_data_view.json  (агрегированные данные для дашборда)
"""

import json
import os
from datetime import date, timedelta
import calendar

DAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

# Ключевые слова для определения летних/холодных напитков
# Проверяются в названии группы (DishGroup) и названии блюда (DishName)
SUMMER_GROUP_KW = ['лет', 'холод', 'смузи', 'лимонад', 'summer', 'cold', 'ice', 'fresh']
SUMMER_NAME_KW  = ['лимонад', 'смузи', 'фреш', 'айс', 'холодн', 'мохито', 'тоник',
                   'милкшейк', 'шейк', 'фраппе', 'гранита', 'матча', 'cold brew',
                   'ice', 'iced', 'lemonade', 'smoothie']

def is_summer_drink(name: str, group: str) -> bool:
    g = group.lower()
    n = name.lower()
    return (any(kw in g for kw in SUMMER_GROUP_KW) or
            any(kw in n for kw in SUMMER_NAME_KW))

BASE_DIR = os.path.dirname(__file__)
IN_PATH = os.path.join(BASE_DIR, "dashboard_data.json")
PLANS_PATH = os.path.join(BASE_DIR, "plans.json")
OUT_PATH = os.path.join(BASE_DIR, "dashboard_data_view.json")


def main():
    with open(IN_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    with open(PLANS_PATH, "r", encoding="utf-8") as f:
        plans = json.load(f).get("monthly_plans", {})

    today = date.today()

    rows = raw["sales_raw"]["data"]
    mtd_rows = raw["sales_mtd_raw"]["data"]
    top_rows = raw["top_items_raw"]["data"]

    # --- По точкам (сегодня) ---
    by_dept = {}
    for r in rows:
        dept = r["Department"]
        d = by_dept.setdefault(dept, {"revenue": 0, "orders": 0})
        d["revenue"] += r.get("DishDiscountSumInt", 0)
        d["orders"] += r.get("UniqOrderId.OrdersCount", 0)

    points = []
    for dept, d in sorted(by_dept.items(), key=lambda kv: -kv[1]["revenue"]):
        revenue = d["revenue"]
        orders = d["orders"]
        avg_check = round(revenue / orders) if orders else 0
        points.append({
            "name": dept,
            "revenue": revenue,
            "orders": orders,
            "avg_check": avg_check,
        })

    # --- По часам (для совместимости, не отображается) ---
    by_hour = {}
    for r in rows:
        hour = r["HourOpen"]
        by_hour[hour] = by_hour.get(hour, 0) + r.get("DishDiscountSumInt", 0)
    hours_sorted = sorted(by_hour.keys())
    hourly = [{"hour": h, "revenue": by_hour[h]} for h in hours_sorted]

    # --- Выручка по дням (последние 7 дней) ---
    weekly_rows = raw.get("sales_weekly_raw", {}).get("data", [])
    weekly_by_date = {}
    for r in weekly_rows:
        d = r.get("OpenDate.Typed", "")
        weekly_by_date[d] = weekly_by_date.get(d, 0) + r.get("DishDiscountSumInt", 0)
    weekly = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        ds = d.isoformat()
        weekly.append({"date": ds, "day_name": DAYS_RU[d.weekday()], "revenue": weekly_by_date.get(ds, 0)})

    # --- Топ позиций (исключаем модификаторы с нулевой суммой) ---
    items = [
        {
            "name": r["DishName"].strip(),
            "group": r.get("DishGroup", "").strip(),
            "qty": r["DishAmountInt"],
            "revenue": r["DishSumInt"],
        }
        for r in top_rows
        if r.get("DishSumInt", 0) > 0
    ]
    items.sort(key=lambda x: -x["revenue"])
    top_items = items[:8]

    # --- Топ летних напитков ---
    summer_drinks = [it for it in items if is_summer_drink(it["name"], it["group"])]
    summer_drinks = summer_drinks[:8]

    # --- Итоги (сегодня) ---
    total_revenue = sum(p["revenue"] for p in points)
    total_orders = sum(p["orders"] for p in points)
    total_avg_check = round(total_revenue / total_orders) if total_orders else 0

    # --- План/факт с начала месяца ---
    day_of_month = today.day
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    pace = day_of_month / days_in_month  # доля месяца, прошедшая к сегодняшнему дню

    mtd_by_dept = {}
    for r in mtd_rows:
        dept = r["Department"]
        mtd_by_dept[dept] = mtd_by_dept.get(dept, 0) + r.get("DishDiscountSumInt", 0)

    plan_rows = []
    total_mtd = 0
    total_plan = 0
    for dept, plan_month in plans.items():
        mtd_revenue = mtd_by_dept.get(dept, 0)
        expected_to_date = round(plan_month * pace)
        pct_of_plan = round((mtd_revenue / plan_month) * 1000) / 10 if plan_month else 0
        pct_vs_expected = round((mtd_revenue / expected_to_date) * 1000) / 10 if expected_to_date else 0
        plan_rows.append({
            "name": dept,
            "mtd_revenue": mtd_revenue,
            "plan_month": plan_month,
            "expected_to_date": expected_to_date,
            "pct_of_plan": pct_of_plan,
            "pct_vs_expected": pct_vs_expected,
        })
        total_mtd += mtd_revenue
        total_plan += plan_month

    plan_rows.sort(key=lambda x: -x["mtd_revenue"])

    total_expected_to_date = round(total_plan * pace)
    plan_summary = {
        "mtd_revenue": total_mtd,
        "plan_month": total_plan,
        "expected_to_date": total_expected_to_date,
        "pct_of_plan": round((total_mtd / total_plan) * 1000) / 10 if total_plan else 0,
        "pct_vs_expected": round((total_mtd / total_expected_to_date) * 1000) / 10 if total_expected_to_date else 0,
        "day_of_month": day_of_month,
        "days_in_month": days_in_month,
    }

    output = {
        "date": raw["date"],
        "summary": {
            "revenue": total_revenue,
            "orders": total_orders,
            "avg_check": total_avg_check,
        },
        "points": points,
        "hourly": hourly,
        "weekly": weekly,
        "top_items": top_items,
        "summer_drinks": summer_drinks,
        "plan": {
            "summary": plan_summary,
            "points": plan_rows,
        },
    }

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Готово: {OUT_PATH}")
    print(f"Точек: {len(points)}, выручка сегодня: {total_revenue} ₽, заказов: {total_orders}")
    print(f"С начала месяца (день {day_of_month}/{days_in_month}): {total_mtd} ₽ из плана {total_plan} ₽ "
          f"({plan_summary['pct_of_plan']}% плана, {plan_summary['pct_vs_expected']}% от темпа)")


if __name__ == "__main__":
    main()
