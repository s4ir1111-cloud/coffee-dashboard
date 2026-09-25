"""
Guest CDP Data Builder
Группирует продажи по гостю из iiko OLAP, используя поля
Delivery.CustomerCardNumber / Delivery.CustomerPhone / Delivery.CustomerName
(а не "Card", который на этом сервере всегда пустой).
Считает RFM-сегменты и сохраняет guest_cdp_data.json.

Использование:
    python3 build_guest_cdp_data.py          # читает auth из env-переменных
    python3 build_guest_cdp_data.py --days-back 100   # порог давности визита

Требуемые переменные окружения:
    IIKO_LOGIN, IIKO_PASSWORD
"""

import json, os, sys, getpass, importlib.util, argparse
from datetime import date, timedelta
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

GUEST_FIELDS = ["Delivery.CustomerCardNumber", "Delivery.CustomerPhone", "Delivery.CustomerName"]
JUNK_VALUES = {"", "null", "none", "-", "(без карты)", "(без имени)", "(не указано)"}


# ─────────────── Загрузка коннектора ──────────────────────────────────────────
def _load_connector():
    for candidate in [
        Path(BASE_DIR) / "iiko_resto_connector.py",
        Path(BASE_DIR).parent / "iiko_resto_connector.py",
    ]:
        if candidate.exists():
            spec = importlib.util.spec_from_file_location("iiko_connector", str(candidate))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            print(f"  Коннектор найден: {candidate}")
            return mod
    return None


# ─────────────── Нормализация ──────────────────────────────────────────────────
def _clean(val):
    s = (val or "").strip()
    return "" if s.lower() in JUNK_VALUES else s


def _clean_phone(val):
    """Поле телефона иногда содержит несколько номеров через запятую — берём первый."""
    s = _clean(val)
    if not s:
        return ""
    first = s.split(",")[0].strip()
    return first


def guest_key(card, phone):
    """Идентификатор гостя: приоритет — номер карты, затем телефон."""
    card  = _clean(card)
    phone = _clean_phone(phone)
    if card:
        return f"card:{card}"
    if phone:
        return f"phone:{phone}"
    return None  # анонимный гость без идентификации — пропускаем


# ─────────────── RFM-сегментация ──────────────────────────────────────────────
def rfm_scores(recency_days, frequency, monetary):
    if recency_days <= 14:
        r = 4
    elif recency_days <= 45:
        r = 3
    elif recency_days <= 90:
        r = 2
    else:
        r = 1

    if frequency >= 20:
        f = 4
    elif frequency >= 8:
        f = 3
    elif frequency >= 3:
        f = 2
    else:
        f = 1

    if monetary >= 15000:
        m = 4
    elif monetary >= 5000:
        m = 3
    elif monetary >= 1500:
        m = 2
    else:
        m = 1

    return r, f, m


def rfm_segment(r, f, m):
    if r == 1:
        return "lost"
    if f == 1 and r >= 3:
        return "newcomers"
    if r <= 2 and f >= 3:
        return "at_risk"
    if r == 2:
        return "sleeping"
    score = r + f + m
    if score >= 10:
        return "champions"
    return "loyal"


# ─────────────── OLAP-запросы ─────────────────────────────────────────────────
def _olap_one_year(connector, host, token, year_from, year_to):
    """Один OLAP-запрос за конкретный отрезок (избегаем таймаута)."""
    return connector._olap(host, token, {
        "reportType": "SALES",
        "groupByRowFields": GUEST_FIELDS,
        "aggregateFields": [
            "DishDiscountSumInt",
            "DishSumInt",
            "UniqOrderId.OrdersCount",
        ],
        "filters": {
            "OpenDate.Typed": {
                "filterType": "DateRange",
                "periodType": "CUSTOM",
                "from": year_from,
                "to": year_to,
                "includeLow": True,
                "includeHigh": True,
            }
        },
    })


def fetch_ltv_per_guest(connector, host, token, date_from, date_to):
    """Всё-время: LTV, кол-во заказов по каждому гостю.
    Разбивает на годовые чанки, чтобы не словить ReadTimeout."""
    from_year = int(date_from[:4])
    to_year   = int(date_to[:4])

    all_rows = []
    for yr in range(from_year, to_year + 1):
        y_from = f"{yr}-01-01"
        y_to   = f"{yr}-12-31"
        if y_from < date_from: y_from = date_from
        if y_to   > date_to:   y_to   = date_to
        print(f"  OLAP: продажи по гостям {yr} ({y_from} — {y_to})…")
        try:
            result = _olap_one_year(connector, host, token, y_from, y_to)
            rows   = result.get("data", [])
            print(f"    → {len(rows):,} строк")
            all_rows.extend(rows)
        except Exception as e:
            print(f"    ⚠ {yr}: ошибка — {e}. Пропускаем.")

    return {"data": all_rows}


def fetch_recent_by_date(connector, host, token, date_from, date_to):
    """Последние N дней: гость + дата → последний визит и частота за период."""
    print(f"  OLAP: визиты по датам ({date_from} — {date_to})…")
    return connector._olap(host, token, {
        "reportType": "SALES",
        "groupByRowFields": GUEST_FIELDS + ["OpenDate.Typed"],
        "aggregateFields": ["UniqOrderId.OrdersCount"],
        "filters": {
            "OpenDate.Typed": {
                "filterType": "DateRange",
                "periodType": "CUSTOM",
                "from": date_from,
                "to": date_to,
                "includeLow": True,
                "includeHigh": True,
            }
        },
    })


def fetch_location_per_guest(connector, host, token, date_from, date_to):
    """Последние N дней: любимая точка каждого гостя."""
    print(f"  OLAP: предпочтительная точка по гостям…")
    return connector._olap(host, token, {
        "reportType": "SALES",
        "groupByRowFields": GUEST_FIELDS + ["Department"],
        "aggregateFields": ["UniqOrderId.OrdersCount"],
        "filters": {
            "OpenDate.Typed": {
                "filterType": "DateRange",
                "periodType": "CUSTOM",
                "from": date_from,
                "to": date_to,
                "includeLow": True,
                "includeHigh": True,
            }
        },
    })


def _shift_month(value, offset):
    """Сдвиг первого дня месяца без внешних зависимостей."""
    absolute = value.year * 12 + value.month - 1 + offset
    return date(absolute // 12, absolute % 12 + 1, 1)


def fetch_monthly_guest_history(connector, host, token, today, months):
    """Загружает агрегаты по гостям отдельно за каждый календарный месяц."""
    first_month = _shift_month(today.replace(day=1), -(months - 1))
    result = []
    for offset in range(months):
        month_start = _shift_month(first_month, offset)
        next_month = _shift_month(month_start, 1)
        month_end = min(today, next_month - timedelta(days=1))
        print(f"  OLAP: помесячная аналитика {month_start:%Y-%m}…")
        try:
            response = connector._olap(host, token, {
                "reportType": "SALES",
                "groupByRowFields": GUEST_FIELDS,
                "aggregateFields": [
                    "DishDiscountSumInt", "DishSumInt", "UniqOrderId.OrdersCount",
                ],
                "filters": {
                    "OpenDate.Typed": {
                        "filterType": "DateRange", "periodType": "CUSTOM",
                        "from": month_start.isoformat(), "to": month_end.isoformat(),
                        "includeLow": True, "includeHigh": True,
                    }
                },
            })
            rows = response.get("data", [])
            print(f"    → {len(rows):,} строк")
            result.append({"period": month_start.strftime("%Y-%m"), "rows": rows})
        except Exception as exc:
            print(f"    ⚠ {month_start:%Y-%m}: {exc}. Месяц пропущен.")
    return result


def build_monthly_history(raw_months, lifetime_guests, today):
    """Строит обезличенные управленческие KPI по месяцам."""
    lifetime_orders = {g["id"]: g["orders"] for g in lifetime_guests}
    window_orders = {}
    active_by_month = []
    for item in raw_months:
        active = {}
        for row in item["rows"]:
            key = _row_key(row)
            if not key:
                continue
            orders = _safe_int(row.get("UniqOrderId.OrdersCount"))
            if orders <= 0:
                continue
            target = active.setdefault(key, {"orders": 0, "revenue": 0.0, "gross": 0.0})
            target["orders"] += orders
            target["revenue"] += _safe_float(row.get("DishDiscountSumInt"))
            target["gross"] += _safe_float(row.get("DishSumInt"))
            window_orders[key] = window_orders.get(key, 0) + orders
        active_by_month.append((item, active))

    known_before_window = {
        key for key, orders in lifetime_orders.items() if orders > window_orders.get(key, 0)
    }
    seen = set(known_before_window)
    previous_active = set()
    output = []
    for item, active in active_by_month:
        period = item["period"]
        active_keys = set(active)
        all_revenue = round(sum(_safe_float(r.get("DishDiscountSumInt")) for r in item["rows"]))
        identified_revenue = round(sum(v["revenue"] for v in active.values()))
        identified_gross = round(sum(v["gross"] for v in active.values()))
        visits = sum(v["orders"] for v in active.values())
        new_guests = len(active_keys - seen)
        returned_guests = len(active_keys & previous_active) if previous_active else None
        retention_pct = (
            round(returned_guests / len(previous_active) * 100, 1)
            if previous_active and returned_guests is not None else None
        )
        repeat_guests = sum(1 for v in active.values() if v["orders"] >= 2)
        active_count = len(active_keys)
        discount_amount = max(0, identified_gross - identified_revenue)
        output.append({
            "period": period,
            "is_complete": period < today.strftime("%Y-%m"),
            "identified_revenue": identified_revenue,
            "total_revenue": all_revenue,
            "loyalty_revenue_share_pct": round(identified_revenue / all_revenue * 100, 1) if all_revenue else 0,
            "active_guests": active_count,
            "new_guests": new_guests,
            "returned_guests": returned_guests,
            "retention_pct": retention_pct,
            "repeat_guest_pct": round(repeat_guests / active_count * 100, 1) if active_count else 0,
            "visits": visits,
            "visits_per_guest": round(visits / active_count, 2) if active_count else 0,
            "avg_check": round(identified_revenue / visits) if visits else 0,
            "revenue_per_guest": round(identified_revenue / active_count) if active_count else 0,
            "discount_amount": round(discount_amount),
            "discount_pct": round(discount_amount / identified_gross * 100, 1) if identified_gross else 0,
        })
        seen.update(active_keys)
        previous_active = active_keys

    by_period = {row["period"]: row for row in output}
    delta_metrics = ("identified_revenue", "active_guests", "visits", "avg_check", "retention_pct")
    for index, row in enumerate(output):
        previous = output[index - 1] if index else None
        year, month = map(int, row["period"].split("-"))
        yoy = by_period.get(f"{year - 1:04d}-{month:02d}")
        row["mom_pct"], row["yoy_pct"] = {}, {}
        for metric in delta_metrics:
            current_value = row.get(metric)
            previous_value = previous.get(metric) if previous else None
            yoy_value = yoy.get(metric) if yoy else None
            row["mom_pct"][metric] = (
                round((current_value - previous_value) / abs(previous_value) * 100, 1)
                if row["is_complete"] and current_value is not None and previous_value else None
            )
            row["yoy_pct"][metric] = (
                round((current_value - yoy_value) / abs(yoy_value) * 100, 1)
                if row["is_complete"] and current_value is not None and yoy_value else None
            )
    return output


# ─────────────── Обработка ────────────────────────────────────────────────────
def _safe_float(val, default=0.0):
    try:
        return float(val or default)
    except (TypeError, ValueError):
        return default


def _safe_int(val, default=0):
    try:
        return int(float(val or default))
    except (TypeError, ValueError):
        return default


def _row_key(row):
    card  = row.get("Delivery.CustomerCardNumber")
    phone = row.get("Delivery.CustomerPhone")
    return guest_key(card, phone)


def process(ltv_rows, recent_rows, location_rows, today):
    # 1. Агрегируем LTV по гостю за всё время + считаем варианты имени
    agg = {}        # key → {"ltv","gross","orders","card","phone","names": {name: orders}}
    for row in ltv_rows:
        key = _row_key(row)
        if not key:
            continue
        ltv    = _safe_float(row.get("DishDiscountSumInt"))
        gross  = _safe_float(row.get("DishSumInt"))
        orders = _safe_int(row.get("UniqOrderId.OrdersCount"))
        name   = _clean(row.get("Delivery.CustomerName"))

        if key not in agg:
            agg[key] = {
                "ltv": 0.0, "gross": 0.0, "orders": 0,
                "card":  _clean(row.get("Delivery.CustomerCardNumber")),
                "phone": _clean_phone(row.get("Delivery.CustomerPhone")),
                "names": {},
            }
        a = agg[key]
        a["ltv"]    += ltv
        a["gross"]  += gross
        a["orders"] += orders
        if name:
            a["names"][name] = a["names"].get(name, 0) + orders

    # 2. Последний визит + число заказов за последние N дней
    last_visit = {}   # key → "YYYY-MM-DD"
    recent_cnt = {}   # key → int
    for row in recent_rows:
        key = _row_key(row)
        if not key:
            continue
        date_str = (row.get("OpenDate.Typed") or "")[:10]
        orders   = _safe_int(row.get("UniqOrderId.OrdersCount"))
        if date_str:
            if key not in last_visit or date_str > last_visit[key]:
                last_visit[key] = date_str
        recent_cnt[key] = recent_cnt.get(key, 0) + orders

    # 3. Любимая точка
    locs_by_key = {}
    for row in location_rows:
        key  = _row_key(row)
        dept = _clean(row.get("Department"))
        orders = _safe_int(row.get("UniqOrderId.OrdersCount"))
        if not key or not dept:
            continue
        locs_by_key.setdefault(key, {})
        locs_by_key[key][dept] = locs_by_key[key].get(dept, 0) + orders

    # 4. Строим список гостей
    guests = []
    for key, a in agg.items():
        if a["orders"] == 0:
            continue

        lv = last_visit.get(key)
        if lv:
            try:
                days_since = (today - date.fromisoformat(lv)).days
            except ValueError:
                days_since = 9999
        else:
            # Гость не встречался в окне recency-запроса → точную дату не знаем,
            # но она точно дальше окна (>90 дней для RFM не важна точность).
            days_since = 9999

        ltv    = round(a["ltv"])
        orders = a["orders"]
        avg_check = round(ltv / orders) if orders else 0
        gross  = round(a["gross"])
        discount_pct = round((gross - ltv) / gross * 100, 1) if gross > 0 else 0.0

        name = max(a["names"], key=lambda n: a["names"][n]) if a["names"] else ""

        locs    = locs_by_key.get(key, {})
        fav_loc = max(locs, key=lambda k: locs[k]) if locs else "—"

        r, f, m = rfm_scores(days_since, orders, ltv)
        seg     = rfm_segment(r, f, m)

        guests.append({
            "id":            key,
            "name":          name,
            "card":          a["card"],
            "phone":         a["phone"],
            "segment":       seg,
            "rfm_r":         r,
            "rfm_f":         f,
            "rfm_m":         m,
            "orders":        orders,
            "ltv":           ltv,
            "avg_check":     avg_check,
            "discount_pct":  discount_pct,
            "last_visit":    lv or "",
            "days_since":    days_since,
            "recent_orders": recent_cnt.get(key, 0),
            "fav_location":  fav_loc,
        })

    guests.sort(key=lambda x: -x["ltv"])
    return guests


# ─────────────── Статистика ───────────────────────────────────────────────────
def build_output(guests, today, date_from, recency_window):
    SEGMENTS = ["champions", "loyal", "at_risk", "sleeping", "newcomers", "lost"]
    seg_counts = {s: 0 for s in SEGMENTS}
    seg_ltv    = {s: 0 for s in SEGMENTS}
    for g in guests:
        s = g["segment"]
        seg_counts[s] = seg_counts.get(s, 0) + 1
        seg_ltv[s]    = seg_ltv.get(s, 0)    + g["ltv"]

    total = len(guests)
    total_rev = sum(g["ltv"] for g in guests)

    buckets = [0, 500, 1500, 3000, 5000, 10000, 20000, 50000]
    ltv_dist = []
    for i in range(len(buckets) - 1):
        lo, hi = buckets[i], buckets[i + 1]
        cnt = sum(1 for g in guests if lo <= g["ltv"] < hi)
        ltv_dist.append({"range": f"{lo//1000}k–{hi//1000}k", "count": cnt})
    ltv_dist.append({"range": "50k+", "count": sum(1 for g in guests if g["ltv"] >= 50000)})

    freq_dist = []
    bands = [(1,1,"1 визит"), (2,3,"2–3"), (4,7,"4–7"), (8,14,"8–14"), (15,29,"15–29"), (30,999,"30+")]
    for lo, hi, label in bands:
        cnt = sum(1 for g in guests if lo <= g["orders"] <= hi)
        freq_dist.append({"label": label, "count": cnt})

    segment_meta = {
        "champions": {"label": "Чемпионы",   "color": "#FFD700", "icon": "🏆",
                      "desc": "Часто, недавно, много тратят"},
        "loyal":     {"label": "Лояльные",   "color": "#4CAF50", "icon": "❤️",
                      "desc": "Регулярные, активные гости"},
        "at_risk":   {"label": "Под угрозой","color": "#FF9800", "icon": "⚠️",
                      "desc": "Раньше были активны, теперь пропали"},
        "sleeping":  {"label": "Засыпающие", "color": "#9C27B0", "icon": "💤",
                      "desc": "Давно не приходили и нечасто"},
        "newcomers": {"label": "Новички",    "color": "#2196F3", "icon": "🌱",
                      "desc": "Первый–второй визит, свежий"},
        "lost":      {"label": "Потерянные", "color": "#F44336", "icon": "😴",
                      "desc": "Не приходят более 90 дней"},
    }
    for s in SEGMENTS:
        segment_meta[s]["count"]   = seg_counts[s]
        segment_meta[s]["ltv"]     = seg_ltv[s]
        segment_meta[s]["share"]   = round(seg_counts[s] / total * 100, 1) if total else 0
        segment_meta[s]["avg_ltv"] = round(seg_ltv[s] / seg_counts[s]) if seg_counts[s] else 0

    return {
        "generated_at":   today.isoformat(),
        "recency_window": recency_window,
        "period":         {"from": date_from, "to": today.isoformat()},
        "summary": {
            "total_guests":  total,
            "active_30d":    sum(1 for g in guests if g["days_since"] <= 30),
            "active_90d":    sum(1 for g in guests if g["days_since"] <= 90),
            "avg_ltv":       round(total_rev / total) if total else 0,
            "avg_check":     round(sum(g["avg_check"] for g in guests) / total) if total else 0,
            "total_revenue": round(total_rev),
            "median_orders": sorted(g["orders"] for g in guests)[total // 2] if total else 0,
        },
        "segment_counts": seg_counts,
        "segment_meta":   segment_meta,
        "ltv_distribution":  ltv_dist,
        "freq_distribution": freq_dist,
        "guests": guests,
        "top20": guests[:20],
    }


# ─────────────── main ─────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Guest CDP Data Builder для iiko")
    parser.add_argument("--days-back", type=int, default=100,
                        help="Окно для поиска последнего визита (дефолт: 100 дней — достаточно для RFM recency)")
    parser.add_argument("--all-time-from", default="2019-01-01",
                        help="Начало периода для LTV (дефолт: 2019-01-01)")
    parser.add_argument("--monthly-months", type=int, default=24,
                        help="Глубина помесячной аналитики (дефолт: 24 месяца)")
    parser.add_argument("--out", default="guest_cdp_data.json",
                        help="Путь выходного файла")
    args = parser.parse_args()

    connector = _load_connector()
    if not connector:
        print("❌ iiko_resto_connector.py не найден ни в текущей директории, ни в родительской.")
        sys.exit(1)

    host     = connector.HOST
    username = os.environ.get("IIKO_LOGIN")    or input("Логин iiko: ").strip()
    password = os.environ.get("IIKO_PASSWORD") or getpass.getpass("Пароль iiko: ")

    today        = date.today()
    date_to      = (today + timedelta(days=1)).isoformat()
    recent_from  = (today - timedelta(days=args.days_back)).isoformat()

    print(f"\n{'─'*60}")
    print(f"  Host:          {host}")
    print(f"  Логин:         {username}")
    print(f"  LTV-период:    {args.all_time_from} → {today}")
    print(f"  Давность:      последние {args.days_back} дней")
    print(f"{'─'*60}\n")

    print("🔐 Авторизация…")
    token = connector.login(host, username, password)
    print(f"   Токен: {token[:10]}…\n")

    ltv_rows      = []
    recent_rows   = []
    location_rows = []
    monthly_rows  = []

    try:
        ltv_data      = fetch_ltv_per_guest(connector, host, token, args.all_time_from, date_to)
        ltv_rows      = ltv_data.get("data", [])
        print(f"   → всего {len(ltv_rows):,} строк за всё время\n")

        recent_data   = fetch_recent_by_date(connector, host, token, recent_from, date_to)
        recent_rows   = recent_data.get("data", [])
        print(f"   → {len(recent_rows):,} строк\n")

        location_data = fetch_location_per_guest(connector, host, token, recent_from, date_to)
        location_rows = location_data.get("data", [])
        print(f"   → {len(location_rows):,} строк\n")

        if args.monthly_months > 0:
            monthly_rows = fetch_monthly_guest_history(
                connector, host, token, today, max(1, args.monthly_months)
            )

        # Снимок на конец прошлого календарного месяца для сравнения KPI.
        current_month_start = today.replace(day=1)
        previous_as_of = current_month_start - timedelta(days=1)
        previous_date_to = previous_as_of.isoformat()
        previous_recent_from = (previous_as_of - timedelta(days=args.days_back)).isoformat()
        print(f"  Сравнение: состояние на {previous_as_of.isoformat()}…")
        previous_ltv_data = fetch_ltv_per_guest(
            connector, host, token, args.all_time_from, previous_date_to
        )
        previous_ltv_rows = previous_ltv_data.get("data", [])
        previous_recent_data = fetch_recent_by_date(
            connector, host, token, previous_recent_from, previous_date_to
        )
        previous_recent_rows = previous_recent_data.get("data", [])

    finally:
        print("🔓 Разлогиниваемся… ", end="")
        connector.logout(host, token)
        print("OK, лицензия освобождена.\n")

    print("⚙️  Обработка данных…")
    guests = process(ltv_rows, recent_rows, location_rows, today)
    output = build_output(guests, today, args.all_time_from, args.days_back)
    output["monthly_history"] = build_monthly_history(monthly_rows, guests, today)
    previous_guests = process(previous_ltv_rows, previous_recent_rows, [], previous_as_of)
    previous_output = build_output(
        previous_guests, previous_as_of, args.all_time_from, args.days_back
    )
    output["comparison"] = {
        "label": previous_as_of.strftime("%m.%Y"),
        "as_of": previous_as_of.isoformat(),
        "summary": previous_output["summary"],
        "segment_counts": previous_output["segment_counts"],
        "segment_meta": previous_output["segment_meta"],
    }

    out_path = os.path.join(BASE_DIR, args.out)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    s = output["summary"]
    sc = output["segment_counts"]
    print(f"\n{'='*60}")
    print(f"✅ Сохранено: {out_path}")
    print(f"{'='*60}")
    print(f"  Всего идентифицированных гостей: {s['total_guests']:>8,}")
    print(f"  Активных за 30 дней:    {s['active_30d']:>8,}  ({round(s['active_30d']/s['total_guests']*100) if s['total_guests'] else 0}%)")
    print(f"  Активных за 90 дней:    {s['active_90d']:>8,}  ({round(s['active_90d']/s['total_guests']*100) if s['total_guests'] else 0}%)")
    print(f"  Общий LTV:              {s['total_revenue']:>10,} ₽")
    print(f"  Средний LTV:            {s['avg_ltv']:>8,} ₽")
    print(f"  Средний чек:            {s['avg_check']:>8,} ₽")
    print(f"\n  Сегменты:")
    print(f"    🏆 Чемпионы:          {sc['champions']:>6,}")
    print(f"    ❤️  Лояльные:          {sc['loyal']:>6,}")
    print(f"    ⚠️  Под угрозой:       {sc['at_risk']:>6,}")
    print(f"    💤 Засыпающие:        {sc['sleeping']:>6,}")
    print(f"    🌱 Новички:           {sc['newcomers']:>6,}")
    print(f"    😴 Потерянные:        {sc['lost']:>6,}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
