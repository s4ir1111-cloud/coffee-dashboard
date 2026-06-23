"""
build_finance_data.py  —  Агрегатор данных для Finance BI дашборда

Читает:
    finance_data_raw.json   — сырые помесячные данные (из finance_connector.py)
    plans.json              — планы по выручке и среднему чеку
    dashboard_data_view.json — текущие MTD-данные (из основного коннектора)

Пишет:
    finance_data.json       — финальные данные для coffee_finance.html
"""

import json, os, calendar
from datetime import date, datetime

# ─── Переименования точек (как в build_dashboard_data.py) ─────────────────────
DEPT_ALIASES = {
    "Преображенский": "Прео",
    # добавьте другие, если нужно
}

def normalize(name):
    name = name.strip()
    return DEPT_ALIASES.get(name, name)

# ─── Города для каждой точки ───────────────────────────────────────────────────
CITIES = {
    "Тюмень": [
        "Океан","Свердлова","Паруса","Видный","Панорама","Калинка","Новин",
        "Драмтеатр","Газпром","Осипенко","Европа","Гагарина","Домашний",
        "Ворлд Класс","Советская","Мельникайте","Арсиб","Прео","Преображенский"
    ],
    "Сургут":  ["Гарден Кофе Сургут"],
    "Тобольск":["Гарден Кофе Тобольск"],
}

def get_city(name):
    for city, points in CITIES.items():
        if name in points:
            return city
    return "Другие"

MONTH_LABELS_RU = ["Янв","Фев","Мар","Апр","Май","Июн",
                   "Июл","Авг","Сен","Окт","Ноя","Дек"]

# ─── Загрузка файлов ──────────────────────────────────────────────────────────
def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)

# ─── Основная функция ─────────────────────────────────────────────────────────
def build():
    raw    = load_json("finance_data_raw.json")
    plans  = load_json("plans.json") or {}
    view   = load_json("dashboard_data_view.json") or {}

    monthly_plans = plans.get("monthly_plans", {})
    avgcheck_plans = plans.get("avg_check_plans", {})

    today = date.today()
    current_year  = today.year
    previous_year = today.year - 1
    current_month = today.month

    cur_key  = f"{current_year}-{str(current_month).zfill(2)}"
    dom      = today.day
    dim      = calendar.monthrange(today.year, today.month)[1]
    pace     = dom / dim

    # ── Все известные точки ────────────────────────────────────────────────────
    all_points = set()
    if raw:
        for year_data in raw.get("years", {}).values():
            for month_data in year_data.values():
                all_points.update(normalize(k) for k in month_data.keys())
    all_points.update(monthly_plans.keys())

    # ── Собираем by_point ─────────────────────────────────────────────────────
    by_point = {}
    for point in sorted(all_points):
        entry = {
            "city": get_city(point),
            "plan_month": monthly_plans.get(point, 0),
            "avg_check_plan": avgcheck_plans.get(point),
            "monthly": {},          # key: "YYYY-MM" → revenue
            "monthly_orders": {},   # key: "YYYY-MM" → orders
            "monthly_prev": {},     # прошлый год "YYYY-MM" → revenue
        }
        if raw:
            # Текущий год
            cur_year_data = raw["years"].get(str(current_year), {})
            for month_key, month_data in cur_year_data.items():
                for dept, vals in month_data.items():
                    if normalize(dept) == point:
                        entry["monthly"][month_key] = round(vals.get("revenue", 0))
                        entry["monthly_orders"][month_key] = int(vals.get("orders", 0))

            # Прошлый год
            prev_year_data = raw["years"].get(str(previous_year), {})
            for month_key, month_data in prev_year_data.items():
                for dept, vals in month_data.items():
                    if normalize(dept) == point:
                        entry["monthly_prev"][month_key] = round(vals.get("revenue", 0))

        # MTD из dashboard_data_view.json (более свежий чем raw за текущий месяц)
        if view:
            for p in (view.get("plan", {}).get("points") or []):
                if normalize(p.get("name", "")) == point:
                    entry["monthly"][cur_key]        = p.get("mtd_revenue") or entry["monthly"].get(cur_key, 0)
                    entry["monthly_orders"][cur_key] = p.get("mtd_orders")  or entry["monthly_orders"].get(cur_key, 0)
                    entry["avg_check"] = p.get("mtd_avg_check") or 0
                    entry["pct_of_plan"]     = p.get("pct_of_plan") or 0
                    entry["pct_vs_expected"] = p.get("pct_vs_expected") or 0
                    break

        by_point[point] = entry

    # ── Месячные итоги сети ────────────────────────────────────────────────────
    months_available = []
    monthly_totals   = []
    monthly_totals_prev = []

    for m in range(1, 13):
        mkey = f"{current_year}-{str(m).zfill(2)}"
        if m > current_month:
            break
        months_available.append(mkey)
        total_rev    = sum(entry["monthly"].get(mkey, 0) for entry in by_point.values())
        total_orders = sum(entry["monthly_orders"].get(mkey, 0) for entry in by_point.values())
        avg_chk = round(total_rev / total_orders) if total_orders else 0
        monthly_totals.append({
            "month": mkey,
            "label": MONTH_LABELS_RU[m - 1],
            "revenue": total_rev,
            "orders": total_orders,
            "avg_check": avg_chk,
        })

        # Прошлый год (тот же месяц)
        prev_mkey = f"{previous_year}-{str(m).zfill(2)}"
        prev_rev = sum(
            entry["monthly_prev"].get(prev_mkey, 0) for entry in by_point.values()
        )
        monthly_totals_prev.append({
            "month": prev_mkey,
            "label": MONTH_LABELS_RU[m - 1],
            "revenue": prev_rev,
        })

    # ── Сводка по текущему месяцу (plan_summary) ──────────────────────────────
    if view and view.get("plan", {}).get("summary"):
        plan_summary = view["plan"]["summary"]
    else:
        # Считаем из by_point если view недоступен
        mtd_rev    = sum(e["monthly"].get(cur_key, 0) for e in by_point.values())
        mtd_orders = sum(e["monthly_orders"].get(cur_key, 0) for e in by_point.values())
        plan_month = sum(monthly_plans.values())
        pct_plan   = round(mtd_rev / plan_month * 100, 1) if plan_month else 0
        expected   = plan_month * pace
        pct_pace   = round(mtd_rev / expected * 100, 1) if expected else 0
        avg_chk    = round(mtd_rev / mtd_orders) if mtd_orders else 0
        plan_summary = {
            "mtd_revenue": mtd_rev,
            "mtd_orders": mtd_orders,
            "plan_month": plan_month,
            "pct_of_plan": pct_plan,
            "pct_vs_expected": pct_pace,
            "mtd_avg_check": avg_chk,
            "day_of_month": dom,
            "days_in_month": dim,
        }

    # ── Аномалии ──────────────────────────────────────────────────────────────
    anomalies = []
    for point, entry in by_point.items():
        rev  = entry["monthly"].get(cur_key, 0)
        plan = entry["plan_month"] or 0
        if not plan or not rev:
            continue
        expected = plan * pace
        pct_pace_val = rev / expected * 100 if expected else 0
        if pct_pace_val < 80:
            anomalies.append({
                "type": "high",
                "point": point,
                "metric": "pace",
                "value": round(pct_pace_val, 1),
                "desc": f"Темп {pct_pace_val:.1f}% — отставание от плана на дату"
            })
        elif pct_pace_val > 125:
            anomalies.append({
                "type": "info",
                "point": point,
                "metric": "pace",
                "value": round(pct_pace_val, 1),
                "desc": f"Темп {pct_pace_val:.1f}% — значительное опережение плана"
            })

    # ── Итоговая структура ────────────────────────────────────────────────────
    output = {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "current_year": current_year,
        "current_month": cur_key,
        "current_month_label": f"{MONTH_LABELS_RU[current_month - 1]} {current_year}",
        "day_of_month": dom,
        "days_in_month": dim,
        "months_available": months_available,
        "monthly_totals": monthly_totals,
        "monthly_totals_prev": monthly_totals_prev,
        "plan_summary": plan_summary,
        "by_point": by_point,
        "anomalies": anomalies,
    }

    with open("finance_data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    total_pts = len([p for p in by_point.values() if p["monthly"].get(cur_key)])
    print(f"✓ finance_data.json сформирован")
    print(f"  Точек с данными: {total_pts}")
    print(f"  Месяцев: {len(months_available)} ({months_available[0] if months_available else '—'} … {months_available[-1] if months_available else '—'})")

if __name__ == "__main__":
    build()
