"""
build_pnl_data.py  —  Собирает pnl_data.json из pnl_data_raw.json

Входной формат (новый, от pnl_connector.py / pnl_extract_new.js):
  {
    "generated_at": "...",
    "store_ids": [...],
    "months": {
      "2025-01": {
        "PL_SALES_TOTAL": 49306212,
        "PL_COS_TOTAL":   28495270,
        "PL_EXP_TOTAL":   5967943,
        "PL_OTH_EXP_TOTAL":     1863591,
        "PL_OTH_INCOME_TOTAL":  115247,
        "PL_PROFIT_GROSS":      20810941,
        "PL_PROFIT_GROSS_PROC": 0.422,
        "PL_PROFIT_MAIN":       14842997,
        "PL_PROFIT_MAIN_PROC":  0.301,
        "PL_PROFIT_NET":        13094653,
        "PL_PROFIT_NET_PROC":   0.266
      },
      ...
    }
  }

Выходной формат (pnl_data.json):
  {
    "has_data": true,
    "generated_at": "...",
    "months_available": ["2025-01", ...],
    "monthly": {
      "2025-01": {
        "revenue":          49306212,
        "cogs":             28495270,
        "gross_profit":     20810941,
        "gross_margin":     0.422,
        "expenses":         5967943,
        "other_expenses":   1863591,
        "other_income":     115247,
        "operating_profit": 14842997,
        "operating_margin": 0.301,
        "net_profit":       13094653,
        "net_margin":       0.266
      }
    },
    "current": { ... },   // последний доступный месяц
    "prev_year": { ... }  // тот же месяц прошлого года
  }
"""

import json
import sys

RAW_FILE = "pnl_data_raw.json"
OUT_FILE = "pnl_data.json"

# ─── Маппинг PL_* → понятные имена ──────────────────────────────────────────
def map_month(raw_month):
    if not raw_month:
        return None
    return {
        "revenue":          raw_month.get("PL_SALES_TOTAL",       0),
        "cogs":             raw_month.get("PL_COS_TOTAL",         0),
        "gross_profit":     raw_month.get("PL_PROFIT_GROSS",      0),
        "gross_margin":     raw_month.get("PL_PROFIT_GROSS_PROC", 0),
        "expenses":         raw_month.get("PL_EXP_TOTAL",         0),
        "other_expenses":   raw_month.get("PL_OTH_EXP_TOTAL",    0),
        "other_income":     raw_month.get("PL_OTH_INCOME_TOTAL",  0),
        "operating_profit": raw_month.get("PL_PROFIT_MAIN",       0),
        "operating_margin": raw_month.get("PL_PROFIT_MAIN_PROC",  0),
        "net_profit":       raw_month.get("PL_PROFIT_NET",        0),
        "net_margin":       raw_month.get("PL_PROFIT_NET_PROC",   0),
    }

# ─── Основная обработка ───────────────────────────────────────────────────────
def build():
    try:
        with open(RAW_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        print(f"❌ Файл не найден: {RAW_FILE}")
        sys.exit(1)

    months_raw = raw.get("months", {})
    if not months_raw:
        print(f"❌ В {RAW_FILE} нет данных (ключ 'months' пуст)")
        sys.exit(1)

    # Берём только месяцы с данными, сортируем
    monthly = {}
    for key, data in sorted(months_raw.items()):
        mapped = map_month(data)
        if mapped and mapped["revenue"] > 0:
            monthly[key] = mapped

    months_list = sorted(monthly.keys())

    if not months_list:
        print("❌ Нет месяцев с ненулевой выручкой")
        sys.exit(1)

    # Текущий и год назад
    last_key = months_list[-1]
    last_year, last_month = map(int, last_key.split("-"))
    prev_year_key = f"{last_year - 1}-{last_month:02d}"

    current   = monthly.get(last_key)
    prev_year = monthly.get(prev_year_key)

    # Квартальная агрегация
    quarterly = {}
    for key, md in monthly.items():
        y, m = map(int, key.split("-"))
        q_key = f"{y}-Q{(m - 1) // 3 + 1}"
        if q_key not in quarterly:
            quarterly[q_key] = {k: 0.0 for k in md}
        for metric, val in md.items():
            if "margin" not in metric:
                quarterly[q_key][metric] = quarterly[q_key].get(metric, 0) + val

    # Пересчитываем маржи для кварталов
    for q_data in quarterly.values():
        rev = q_data.get("revenue", 0) or 1
        q_data["gross_margin"]     = q_data["gross_profit"]     / rev
        q_data["operating_margin"] = q_data["operating_profit"] / rev
        q_data["net_margin"]       = q_data["net_profit"]       / rev

    output = {
        "has_data":         True,
        "generated_at":     raw.get("generated_at", ""),
        "months_available": months_list,
        "monthly":          monthly,
        "quarterly":        quarterly,
        "current":          current,
        "prev_year":        prev_year,
    }

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"✓ Сохранено: {OUT_FILE}")
    print(f"  Месяцев: {len(months_list)}  ({months_list[0]} → {months_list[-1]})")
    if current:
        print(f"  Последний месяц ({last_key}):")
        print(f"    Выручка:          {current['revenue']/1e6:.1f} M₽")
        print(f"    Себестоимость:    {current['cogs']/1e6:.1f} M₽")
        print(f"    Валовая прибыль:  {current['gross_profit']/1e6:.1f} M₽  ({current['gross_margin']*100:.1f}%)")
        print(f"    Расходы:          {current['expenses']/1e6:.1f} M₽")
        print(f"    Опер. прибыль:    {current['operating_profit']/1e6:.1f} M₽  ({current['operating_margin']*100:.1f}%)")
        print(f"    Чистая прибыль:   {current['net_profit']/1e6:.1f} M₽  ({current['net_margin']*100:.1f}%)")
    if prev_year and current:
        rev_growth = (current["revenue"] / prev_year["revenue"] - 1) * 100 if prev_year["revenue"] else 0
        print(f"  Рост выручки YoY: {rev_growth:+.1f}%")

if __name__ == "__main__":
    build()
