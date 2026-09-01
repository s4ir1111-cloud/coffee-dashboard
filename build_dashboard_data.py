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
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import calendar

DAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS_RU_SHORT = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
DASHBOARD_TIMEZONE = ZoneInfo(os.environ.get("DASHBOARD_TIMEZONE", "Asia/Yekaterinburg"))

# Переименования точек (IIKO-название → отображаемое)
DEPT_ALIASES = {
    "Преображенский": "Прео",
}

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
        raw_file = json.load(f)
    raw_plans = raw_file.get("monthly_plans", {})
    raw_avg_check_plans = raw_file.get("avg_check_plans", {})
    plans = {DEPT_ALIASES.get(k, k): v for k, v in raw_plans.items()}
    avg_check_plans = {DEPT_ALIASES.get(k, k): v for k, v in raw_avg_check_plans.items()}

    today = date.fromisoformat(raw.get("date", date.today().isoformat()))

    rows = raw["sales_raw"]["data"]
    yesterday_rows = raw.get("sales_yesterday_raw", {}).get("data", [])
    mtd_rows = raw["sales_mtd_raw"]["data"]
    top_rows = raw["top_items_raw"]["data"]

    # --- По точкам (сегодня) ---
    by_dept = {}
    for r in rows:
        dept = DEPT_ALIASES.get(r["Department"], r["Department"])
        d = by_dept.setdefault(dept, {"revenue": 0, "orders": 0})
        d["revenue"] += r.get("DishDiscountSumInt", 0)
        d["orders"] += r.get("UniqOrderId.OrdersCount", 0)

    # Вчера до последнего полностью завершённого часа по местному времени.
    # Нельзя брать максимальный HourOpen сегодняшних строк: интернет-предзаказ
    # может иметь будущий час (например, 21:00 при текущем времени 16:58).
    current_hours = [int(r["HourOpen"]) for r in rows if str(r.get("HourOpen", "")).isdigit()]
    local_now = datetime.now(DASHBOARD_TIMEZONE)
    if today == local_now.date():
        comparison_hour = local_now.hour - 1 if local_now.hour > 0 else None
    else:
        comparison_hour = max(current_hours) if current_hours else None
    yesterday_by_dept = {}
    yesterday_orders_by_dept = {}
    if comparison_hour is not None:
        for r in yesterday_rows:
            hour = str(r.get("HourOpen", ""))
            if not hour.isdigit() or int(hour) > comparison_hour:
                continue
            dept = DEPT_ALIASES.get(r["Department"], r["Department"])
            yesterday_by_dept[dept] = (
                yesterday_by_dept.get(dept, 0) + r.get("DishDiscountSumInt", 0)
            )
            yesterday_orders_by_dept[dept] = (
                yesterday_orders_by_dept.get(dept, 0)
                + r.get("UniqOrderId.OrdersCount", 0)
            )

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
            "yesterday_same_hour_revenue": yesterday_by_dept.get(dept, 0),
            "yesterday_same_hour_orders": yesterday_orders_by_dept.get(dept, 0),
        })

    # --- По часам текущего дня ---
    by_hour = {}
    by_hour_by_dept = {}
    hour_cap = local_now.hour if today == local_now.date() else 23
    for r in rows:
        hour = str(r["HourOpen"])
        if not hour.isdigit() or int(hour) > hour_cap:
            continue
        revenue = r.get("DishDiscountSumInt", 0)
        dept = DEPT_ALIASES.get(r["Department"], r["Department"])
        by_hour[hour] = by_hour.get(hour, 0) + revenue
        dept_hours = by_hour_by_dept.setdefault(dept, {})
        dept_hours[hour] = dept_hours.get(hour, 0) + revenue

    # --- Периоды графика: 7 дней, текущий месяц и текущий год ---
    period_rows = raw.get("sales_period_raw", raw.get("sales_weekly_raw", {})).get("data", [])
    daily_by_date = {}
    daily_by_dept = {}
    for r in period_rows:
        d = r.get("OpenDate.Typed", "")
        revenue = r.get("DishDiscountSumInt", 0)
        daily_by_date[d] = daily_by_date.get(d, 0) + revenue
        raw_dept = r.get("Department")
        if raw_dept:
            dept = DEPT_ALIASES.get(raw_dept, raw_dept)
            dept_days = daily_by_dept.setdefault(dept, {})
            dept_days[d] = dept_days.get(d, 0) + revenue

    def make_revenue_periods(daily_values, hourly_values):
        hours_sorted = sorted(hourly_values.keys(), key=int)
        hourly = [{"hour": h, "label": f"{int(h):02d}:00", "date": today.isoformat(), "revenue": hourly_values[h]} for h in hours_sorted]
        month_days = []
        for day_number in range(1, today.day + 1):
            d = today.replace(day=day_number)
            ds = d.isoformat()
            month_days.append({"date": ds, "label": d.strftime("%d.%m"), "revenue": daily_values.get(ds, 0)})
        week_days = []
        for days_ago in range(6, -1, -1):
            d = today - timedelta(days=days_ago)
            ds = d.isoformat()
            week_days.append({"date": ds, "label": d.strftime("%d.%m"), "revenue": daily_values.get(ds, 0)})
        year_months = []
        for month_number in range(1, today.month + 1):
            prefix = f"{today.year}-{month_number:02d}-"
            revenue = sum(value for ds, value in daily_values.items() if ds.startswith(prefix))
            year_months.append({"date": prefix[:7], "label": MONTHS_RU_SHORT[month_number - 1], "revenue": revenue})
        return {"day": hourly, "week": week_days, "month": month_days, "year": year_months}

    revenue_periods = make_revenue_periods(daily_by_date, by_hour)
    all_period_depts = set(by_dept) | set(daily_by_dept)
    revenue_periods_points = {
        dept: make_revenue_periods(daily_by_dept.get(dept, {}), by_hour_by_dept.get(dept, {}))
        for dept in sorted(all_period_depts)
    }
    weekly = revenue_periods["month"]

    # --- Топ позиций с начала месяца (исключаем модификаторы с нулевой суммой) ---
    items = [
        {
            "name": r["DishName"].strip(),
            "group": (r.get("DishGroup") or "").strip(),
            "qty": r["DishAmountInt"],
            "revenue": r["DishSumInt"],
        }
        for r in top_rows
        if r.get("DishSumInt", 0) > 0
    ]
    items.sort(key=lambda x: -x["revenue"])
    top_items = items[:8]

    # --- Топ летних напитков с начала месяца ---
    summer_drinks = [it for it in items if is_summer_drink(it["name"], it["group"])]
    summer_drinks = summer_drinks[:8]

    # --- Итоги (сегодня) ---
    total_revenue = sum(p["revenue"] for p in points)
    total_orders = sum(p["orders"] for p in points)
    total_avg_check = round(total_revenue / total_orders) if total_orders else 0
    total_yesterday_same_hour = sum(yesterday_by_dept.values())
    total_yesterday_same_hour_orders = sum(yesterday_orders_by_dept.values())

    # --- % скидки по стандартному отчёту IIKO «Продажи по типам скидок» ---
    # Нельзя считать скидку как DishSumInt - DishDiscountSumInt: эта разница
    # включает не только скидки. Используем отдельные OLAP-поля отчёта.
    discount_rows = raw.get("discounts_raw", {}).get("data", [])
    discount_gross = sum(r.get("gross_sum", 0) or 0 for r in discount_rows)
    discount_amount = sum(r.get("discount_sum", 0) or 0 for r in discount_rows)
    discount_pct = (
        round((discount_amount / discount_gross) * 1000) / 10
        if discount_gross else None
    )

    # --- План/факт с начала месяца ---
    day_of_month = today.day
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    pace = day_of_month / days_in_month  # доля месяца, прошедшая к сегодняшнему дню

    mtd_by_dept = {}
    mtd_orders_by_dept = {}
    for r in mtd_rows:
        dept = DEPT_ALIASES.get(r["Department"], r["Department"])
        mtd_by_dept[dept] = mtd_by_dept.get(dept, 0) + r.get("DishDiscountSumInt", 0)
        mtd_orders_by_dept[dept] = mtd_orders_by_dept.get(dept, 0) + r.get("UniqOrderId.OrdersCount", 0)

    plan_rows = []
    total_mtd = 0
    total_plan = 0
    total_mtd_orders = 0
    for dept, plan_month in plans.items():
        mtd_revenue = mtd_by_dept.get(dept, 0)
        mtd_orders = mtd_orders_by_dept.get(dept, 0)
        mtd_avg_check = round(mtd_revenue / mtd_orders) if mtd_orders else 0
        expected_to_date = round(plan_month * pace)
        pct_of_plan = round((mtd_revenue / plan_month) * 1000) / 10 if plan_month else 0
        pct_vs_expected = round((mtd_revenue / expected_to_date) * 1000) / 10 if expected_to_date else 0
        avg_check_plan = avg_check_plans.get(dept)
        pct_avg_check = round((mtd_avg_check / avg_check_plan) * 1000) / 10 if (avg_check_plan and mtd_avg_check) else None
        plan_rows.append({
            "name": dept,
            "mtd_revenue": mtd_revenue,
            "mtd_orders": mtd_orders,
            "mtd_avg_check": mtd_avg_check,
            "plan_month": plan_month,
            "expected_to_date": expected_to_date,
            "pct_of_plan": pct_of_plan,
            "pct_vs_expected": pct_vs_expected,
            "avg_check_plan": avg_check_plan,
            "pct_avg_check": pct_avg_check,
        })
        total_mtd_orders += mtd_orders
        total_mtd += mtd_revenue
        total_plan += plan_month

    plan_rows.sort(key=lambda x: -x["mtd_revenue"])

    total_expected_to_date = round(total_plan * pace)
    total_mtd_avg_check = round(total_mtd / total_mtd_orders) if total_mtd_orders else 0
    plan_summary = {
        "mtd_revenue": total_mtd,
        "mtd_orders": total_mtd_orders,
        "mtd_avg_check": total_mtd_avg_check,
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
            "yesterday_same_hour_revenue": total_yesterday_same_hour,
            "yesterday_same_hour_orders": total_yesterday_same_hour_orders,
            "comparison_hour": f"{comparison_hour:02d}" if comparison_hour is not None else None,
            "discount_pct": discount_pct,
        },
        "points": points,
        "yesterday_same_hour_points": [
            {
                "name": dept,
                "revenue": revenue,
                "orders": yesterday_orders_by_dept.get(dept, 0),
            }
            for dept, revenue in sorted(
                yesterday_by_dept.items(), key=lambda item: -item[1]
            )
        ],
        "hourly": hourly,
        "weekly": weekly,
        "revenue_periods": revenue_periods,
        "revenue_periods_points": revenue_periods_points,
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
