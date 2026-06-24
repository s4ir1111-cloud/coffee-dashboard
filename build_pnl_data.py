"""
build_pnl_data.py  —  Агрегатор P&L данных для Finance BI дашборда

Читает:
    pnl_data_raw.json       — сырые данные (из pnl_connector.py)
    finance_data.json       — выручка по точкам (из build_finance_data.py)
    plans.json              — планы по выручке

Пишет:
    pnl_data.json           — P&L данные для coffee_finance.html

P&L структура:
  Выручка
  - Себестоимость продаж (COGS)
  = Валовая прибыль
  - ФОТ
  - Операционные расходы
  - Маркетинг и гости
  - Прочие расходы
  = EBITDA
  - Амортизация
  = Чистая прибыль
"""

import json, os, calendar
from datetime import date, datetime

# ─── P&L Группировка статей расходов ──────────────────────────────────────────
# Названия счетов из iikoWeb (dictionary/accounts, type=EXPENSES)
PNL_GROUPS = {
    "COGS": {
        "label": "Себестоимость продаж",
        "color": "#ef4444",
        "items": [
            # Счета 5.xx (товары/продукты) и прямые расходы
            "Расход продуктов/Себестоимость",
            "Безлимитный фильтр",
            "Бракераж",
            "Настройка помола зерна",
            "Недостача инвентаризации",
            "Потери/брак/порча",
            "Расходы на упаковку",
            "Упаковка",
            "Расходы на хоз.товары",
            "Излишки инвентаризации",
            # Поставки/себестоимость товара
            "Оплата поставщикам за товар",
            "Оплата накладных за сырье и материалы",
            "Транспортные услуги по доставке сырья,товаров",
        ],
        "cogs_system": True,
    },
    "FOT": {
        "label": "ФОТ (оплата труда)",
        "color": "#a855f7",
        "code_prefixes": ["6.01"],   # коды счетов начинающиеся с 6.01
        "items": [
            # iikoWeb account names (code 6.01.x)
            "ЗАРПЛАТА",
            "Заработная плата административный персонал",
            "Премия",
            # Соц. отчисления
            "Социальные отчисления от ЗП и НДФЛ",
            "Оплата труда сотрудников",
            # Устаревшие названия
            "ФОТ", "Зарплата", "Выплата зарплаты", "Оплата труда",
        ],
        "cogs_system": False,
    },
    "Operations": {
        "label": "Операционные расходы",
        "color": "#f97316",
        "code_prefixes": ["6.03", "6.07", "6.08", "6.09", "6.10", "6.11", "6.12"],
        "items": [
            # Аренда
            "Арендная плата",
            # Банк / налоги
            "Банковские услуги/РКО",
            "НАЛОГИ",
            "Госпошлины/Штрафы",
            "Налоги Земля/Имущество",
            "Налоги",
            # Помещения и коммуналка
            "Ремонт и обслуживание помещений",
            "Коммунальные услуги",
            "Клининговые услуги и материалы",
            "Дератизация и дезинфекция",
            "Вывоз мусора",
            "Вывоз мусор",
            # Оборудование
            "ТО и ремонт оборудования, инвентаря",
            "Ремонт и обслуживание оборудования",
            "Орг.техника",
            # Прочие операционные
            "ГСМ",
            "Оформление торгового зала(дизайн и озеленение)",
            "Транспортные расходы",
            "Транспортные расходы на доставку оборудования",
            "Прочие ТМЦ списанные",
            "Инвентарь списанный",
            "Бой посуды",
            "Канцтовары",
            "Охрана объектов",
            "Охрана труда, ТБ и ПБ",
            "Лицензии/ПО/сертификация",
            "Страхование, поручительство",
            "Юридические, консультационные услуги",
            "Услуги управляющей компании",
            "РАО и ВОИС",
            "Проценты овердрафт",
            "Расходы Учредителей",
            "Разменный фонд",
            "Скидка бонусная система",
            "Прочие выплаты",
        ],
        "cogs_system": False,
    },
    "Personnel": {
        "label": "Расходы на персонал",
        "color": "#3b82f6",
        "items": [
            "Поиск персонала",
            "Подбор и обучение персонала",
            "Обучение персонала",
            "Медосмотр/ Медикаменты",
            "Развозка персонала",
            "Командировочные расходы",
            "Бесплатная еда для сотрудников",
            "Бесплатные напитки для сотрудников",
        ],
        "cogs_system": False,
    },
    "Marketing": {
        "label": "Маркетинг и гости",
        "color": "#ec4899",
        "items": [
            "Гостю",
            "Первый Гость и Подарок в День Рождения",
            "Расходы на рекламу, дизайн и маркетинг",
            "Рекламные расходы",
            "Представительские расходы",
            "Строительно монтажные и отделочные работы",
            "Проектирование и стройматериалы",
        ],
        "cogs_system": False,
    },
    "Depreciation": {
        "label": "Амортизация",
        "color": "#64748b",
        "items": [
            "Расходы из Амортизационного фонда",
        ],
        "cogs_system": False,
    },
}

# Построим обратный маппинг: article_lower → group_key
ARTICLE_TO_GROUP = {}
for gkey, gdata in PNL_GROUPS.items():
    for art in gdata.get("items", []):
        ARTICLE_TO_GROUP[art.strip().lower()] = gkey

# Маппинг по префиксу кода счёта (fallback)
CODE_PREFIX_TO_GROUP = {}
for gkey, gdata in PNL_GROUPS.items():
    for prefix in gdata.get("code_prefixes", []):
        CODE_PREFIX_TO_GROUP[prefix] = gkey

# ─── Нормализация имён ─────────────────────────────────────────────────────────
DEPT_ALIASES = {
    "Преображенский": "Прео",
}

def normalize(name):
    name = str(name).strip()
    return DEPT_ALIASES.get(name, name)

MONTH_LABELS_RU = ["Янв","Фев","Мар","Апр","Май","Июн",
                   "Июл","Авг","Сен","Окт","Ноя","Дек"]

# ─── Утилиты ──────────────────────────────────────────────────────────────────
def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def quarter(month_key):
    m = int(month_key.split("-")[1])
    return f"Q{(m-1)//3+1}"

# ─── Агрегация P&L за один месяц ──────────────────────────────────────────────
def aggregate_month(month_data):
    """
    month_data = {"sales": {dept: {revenue,cogs,orders}}, "finance": {dept: {article: amount}}}
    Возвращает:
    {
      "revenue":  total revenue
      "cogs_system": COGS из SALES OLAP (DishCostSumInt)
      "by_group": {group_key: total_amount}
      "by_article": {article: total_amount}
      "orders": total orders
      "by_point": {dept: {revenue, cogs_system, orders, by_group: {...}}}
    }
    """
    result = {
        "revenue": 0,
        "cogs_system": 0,
        "orders": 0,
        "by_group": {gk: 0 for gk in PNL_GROUPS},
        "by_article": {},
        "by_point": {},
    }

    # Обрабатываем Sales данные
    for dept_raw, vals in month_data.get("sales", {}).items():
        dept = normalize(dept_raw)
        rev  = float(vals.get("revenue", 0))
        cogs = float(vals.get("cogs", 0))
        ord_ = int(vals.get("orders", 0))
        result["revenue"]     += rev
        result["cogs_system"] += cogs
        result["orders"]      += ord_
        if dept not in result["by_point"]:
            result["by_point"][dept] = {"revenue": 0, "cogs_system": 0, "orders": 0, "by_group": {gk: 0 for gk in PNL_GROUPS}}
        result["by_point"][dept]["revenue"]     += rev
        result["by_point"][dept]["cogs_system"] += cogs
        result["by_point"][dept]["orders"]      += ord_

    # Обрабатываем Finance данные
    # Новая структура (iikoWeb): {account_name: {store_name: sum}}
    # Старая структура (TRANSACTIONS OLAP): {dept: {article: sum}} — тоже поддерживаем
    finance = month_data.get("finance", {})
    first_val = next(iter(finance.values()), None) if finance else None
    new_structure = isinstance(first_val, dict) and not any(
        isinstance(v, (int, float)) for v in (first_val.values() if first_val else [])
    )
    # Определяем формат: если значение вложенного словаря — число, это старый формат
    # Если значение вложенного словаря — словарь, это новый формат
    if first_val and isinstance(first_val, dict):
        inner_val = next(iter(first_val.values()), None)
        new_structure = isinstance(inner_val, (int, float))

    if new_structure:
        # Новый формат: {account_name: {store_name: sum}}
        for account_name, store_amounts in finance.items():
            art_clean = account_name.strip()
            art_lower = art_clean.lower()
            gkey = ARTICLE_TO_GROUP.get(art_lower)

            if isinstance(store_amounts, dict):
                for store_raw, amount in store_amounts.items():
                    dept = normalize(store_raw)
                    amount = float(amount or 0)
                    result["by_article"][art_clean] = result["by_article"].get(art_clean, 0) + amount
                    if gkey:
                        result["by_group"][gkey] = result["by_group"].get(gkey, 0) + amount
                        if dept not in result["by_point"]:
                            result["by_point"][dept] = {"revenue": 0, "cogs_system": 0, "orders": 0, "by_group": {gk: 0 for gk in PNL_GROUPS}}
                        result["by_point"][dept]["by_group"][gkey] = \
                            result["by_point"][dept]["by_group"].get(gkey, 0) + amount
    else:
        # Старый формат: {dept: {article: sum}}
        for dept_raw, articles in finance.items():
            dept = normalize(dept_raw)
            if not isinstance(articles, dict):
                continue
            for article, amount in articles.items():
                art_clean = article.strip()
                art_lower = art_clean.lower()
                gkey = ARTICLE_TO_GROUP.get(art_lower)
                amount = float(amount or 0)
                result["by_article"][art_clean] = result["by_article"].get(art_clean, 0) + amount
                if gkey:
                    result["by_group"][gkey] = result["by_group"].get(gkey, 0) + amount
                    if dept not in result["by_point"]:
                        result["by_point"][dept] = {"revenue": 0, "cogs_system": 0, "orders": 0, "by_group": {gk: 0 for gk in PNL_GROUPS}}
                    result["by_point"][dept]["by_group"][gkey] = \
                        result["by_point"][dept]["by_group"].get(gkey, 0) + amount

    # Добавляем cogs_system в группу COGS (если нет данных из финансового журнала)
    # Логика: если в финансовом журнале есть статья "Расход продуктов" — используем её.
    # Иначе — используем DishCostSumInt из SALES как fallback.
    cogs_from_journal = result["by_group"].get("COGS", 0)
    if cogs_from_journal == 0 and result["cogs_system"] > 0:
        result["by_group"]["COGS"] = result["cogs_system"]
        for dept, pt in result["by_point"].items():
            if pt["by_group"].get("COGS", 0) == 0:
                pt["by_group"]["COGS"] = pt.get("cogs_system", 0)

    return result

# ─── Расчёт P&L метрик ────────────────────────────────────────────────────────
def calc_pnl(agg):
    """Считает P&L из агрегированных данных."""
    rev  = agg["revenue"]
    cogs = agg["by_group"].get("COGS", 0)
    fot  = agg["by_group"].get("FOT", 0)
    ops  = agg["by_group"].get("Operations", 0)
    pers = agg["by_group"].get("Personnel", 0)
    mkt  = agg["by_group"].get("Marketing", 0)
    dep  = agg["by_group"].get("Depreciation", 0)

    gross_profit = rev - cogs
    total_opex   = fot + ops + pers + mkt
    ebitda       = gross_profit - total_opex
    net_profit   = ebitda - dep

    def pct(val, base):
        return round(val / base * 100, 1) if base else 0

    return {
        "revenue":       round(rev),
        "cogs":          round(cogs),
        "gross_profit":  round(gross_profit),
        "gross_margin":  pct(gross_profit, rev),
        "fot":           round(fot),
        "fot_pct":       pct(fot, rev),
        "operations":    round(ops),
        "operations_pct":pct(ops, rev),
        "personnel":     round(pers),
        "personnel_pct": pct(pers, rev),
        "marketing":     round(mkt),
        "marketing_pct": pct(mkt, rev),
        "ebitda":        round(ebitda),
        "ebitda_margin": pct(ebitda, rev),
        "depreciation":  round(dep),
        "net_profit":    round(net_profit),
        "net_margin":    pct(net_profit, rev),
        "orders":        agg["orders"],
        "avg_check":     round(rev / agg["orders"]) if agg["orders"] else 0,
        "by_group":      {k: round(v) for k, v in agg["by_group"].items()},
        "by_article":    {k: round(v) for k, v in agg["by_article"].items()},
        "by_point":      {
            dept: {
                "revenue":      round(pt["revenue"]),
                "cogs":         round(pt["by_group"].get("COGS", pt.get("cogs_system", 0))),
                "gross_profit": round(pt["revenue"] - pt["by_group"].get("COGS", pt.get("cogs_system", 0))),
                "fot":          round(pt["by_group"].get("FOT", 0)),
                "operations":   round(pt["by_group"].get("Operations", 0)),
                "net_est":      round(
                    pt["revenue"]
                    - pt["by_group"].get("COGS", pt.get("cogs_system", 0))
                    - sum(pt["by_group"].get(g, 0) for g in ["FOT","Operations","Personnel","Marketing","Depreciation"])
                ),
            }
            for dept, pt in agg["by_point"].items()
        },
    }

# ─── Обнаружение аномалий ─────────────────────────────────────────────────────
def detect_anomalies(monthly_pnl):
    """Ищет статьи с отклонением >30% м/м или г/г."""
    anomalies = []
    months = sorted(monthly_pnl.keys())
    if len(months) < 2:
        return anomalies

    cur_key  = months[-1]
    prev_key = months[-2]
    cur  = monthly_pnl[cur_key]
    prev = monthly_pnl[prev_key]

    # Проверяем группы расходов
    for gkey, gdata in PNL_GROUPS.items():
        cur_val  = cur.get("by_group", {}).get(gkey, 0)
        prev_val = prev.get("by_group", {}).get(gkey, 0)
        if prev_val <= 0 or cur_val <= 0:
            continue
        delta_pct = (cur_val - prev_val) / prev_val * 100

        if abs(delta_pct) >= 30:
            anomalies.append({
                "type":     "expense",
                "severity": "high" if abs(delta_pct) >= 50 else "warn",
                "group":    gkey,
                "label":    gdata["label"],
                "cur":      round(cur_val),
                "prev":     round(prev_val),
                "delta_pct": round(delta_pct, 1),
                "period":   f"{cur_key} vs {prev_key}",
                "desc":     f"{gdata['label']}: {'+' if delta_pct>0 else ''}{delta_pct:.0f}% м/м",
            })

    # Рентабельность
    cur_margin  = cur.get("net_margin", 0)
    prev_margin = prev.get("net_margin", 0)
    if prev_margin != 0 and abs(cur_margin - prev_margin) >= 5:
        anomalies.append({
            "type":     "margin",
            "severity": "high" if cur_margin < 0 else "warn",
            "label":    "Чистая прибыль %",
            "cur":      cur_margin,
            "prev":     prev_margin,
            "delta_pct": round(cur_margin - prev_margin, 1),
            "period":   f"{cur_key} vs {prev_key}",
            "desc":     f"Маржинальность: {cur_margin:.1f}% (было {prev_margin:.1f}%)",
        })

    return sorted(anomalies, key=lambda x: abs(x["delta_pct"]), reverse=True)

# ─── Квартальная агрегация ────────────────────────────────────────────────────
def build_quarterly(monthly_pnl, year):
    quarters = {}
    for mkey, pnl in monthly_pnl.items():
        if not mkey.startswith(str(year)):
            continue
        q = quarter(mkey)
        if q not in quarters:
            quarters[q] = {
                "revenue": 0, "cogs": 0, "gross_profit": 0,
                "fot": 0, "operations": 0, "ebitda": 0, "net_profit": 0,
            }
        for k in quarters[q]:
            quarters[q][k] += pnl.get(k, 0)
    # Пересчитаем проценты
    for q, d in quarters.items():
        rev = d["revenue"]
        d["gross_margin"] = round(d["gross_profit"] / rev * 100, 1) if rev else 0
        d["net_margin"]   = round(d["net_profit"] / rev * 100, 1) if rev else 0
    return quarters

# ─── Основная функция ─────────────────────────────────────────────────────────
def build():
    raw      = load_json("pnl_data_raw.json")
    fin_data = load_json("finance_data.json")    # выручка из основного дашборда
    plans    = load_json("plans.json") or {}

    today         = date.today()
    current_year  = today.year
    previous_year = today.year - 1
    cur_month_key = f"{current_year}-{str(today.month).zfill(2)}"

    if not raw:
        print("⚠ pnl_data_raw.json не найден. Запустите pnl_connector.py")
        # Создаём минимальную структуру для отображения
        output = {"generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "has_data": False, "error": "pnl_data_raw.json не найден"}
        with open("pnl_data.json", "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        return

    # ── Собираем помесячные P&L ───────────────────────────────────────────────
    monthly_pnl      = {}   # "YYYY-MM" → pnl metrics
    monthly_pnl_prev = {}   # прошлый год

    for year_str, year_data in raw.get("years", {}).items():
        year_int = int(year_str)
        for month_key, month_data in year_data.items():
            agg = aggregate_month(month_data)
            pnl = calc_pnl(agg)
            pnl["month_key"] = month_key
            pnl["month_label"] = MONTH_LABELS_RU[int(month_key.split("-")[1]) - 1]
            pnl["quarter"] = quarter(month_key)

            if year_int == current_year:
                monthly_pnl[month_key] = pnl
            elif year_int == previous_year:
                monthly_pnl_prev[month_key] = pnl

    # ── Если revenue пустая — берём из finance_data.json и пересчитываем P&L ──
    if fin_data and fin_data.get("monthly_totals"):
        for mt in fin_data["monthly_totals"]:
            mkey = mt["month"]
            if mkey in monthly_pnl and monthly_pnl[mkey]["revenue"] == 0:
                rev = mt.get("revenue", 0)
                if not rev:
                    continue
                p = monthly_pnl[mkey]
                p["revenue"]   = rev
                p["orders"]    = mt.get("orders", 0)
                p["avg_check"] = mt.get("avg_check", 0)
                # Пересчитываем производные метрики
                def pct(val, base): return round(val / base * 100, 1) if base else 0
                cogs            = p["cogs"]
                p["gross_profit"]  = round(rev - cogs)
                p["gross_margin"]  = pct(rev - cogs, rev)
                fot  = p["fot"];  ops = p["operations"]
                pers = p["personnel"]; mkt = p["marketing"]; dep = p["depreciation"]
                ebitda = rev - cogs - fot - ops - pers - mkt
                p["ebitda"]        = round(ebitda)
                p["ebitda_margin"] = pct(ebitda, rev)
                p["fot_pct"]       = pct(fot, rev)
                p["operations_pct"]= pct(ops, rev)
                p["personnel_pct"] = pct(pers, rev)
                p["marketing_pct"] = pct(mkt, rev)
                net = ebitda - dep
                p["net_profit"]    = round(net)
                p["net_margin"]    = pct(net, rev)

    # ── Квартальные итоги ─────────────────────────────────────────────────────
    quarterly = build_quarterly(monthly_pnl, current_year)
    quarterly_prev = build_quarterly(monthly_pnl_prev, previous_year)

    # ── Аномалии ─────────────────────────────────────────────────────────────
    anomalies = detect_anomalies(monthly_pnl)

    # ── Метаданные по статьям ─────────────────────────────────────────────────
    # Собираем все статьи что реально пришли из IIKO (для отладки)
    all_articles_found = set()
    for yd in raw.get("years", {}).values():
        for md in yd.values():
            for dept_arts in md.get("finance", {}).values():
                all_articles_found.update(dept_arts.keys())

    # ── Группы для дашборда ───────────────────────────────────────────────────
    groups_meta = [
        {
            "key":   gkey,
            "label": gdata["label"],
            "color": gdata["color"],
            "items": gdata["items"],
        }
        for gkey, gdata in PNL_GROUPS.items()
    ]

    # ── Итоговая структура ────────────────────────────────────────────────────
    months_list = sorted(monthly_pnl.keys())
    cur_pnl = monthly_pnl.get(cur_month_key, {})

    output = {
        "generated_at":     datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "has_data":         bool(monthly_pnl),
        "current_year":     current_year,
        "previous_year":    previous_year,
        "current_month":    cur_month_key,
        "months_available": months_list,
        "monthly":          monthly_pnl,
        "monthly_prev":     monthly_pnl_prev,
        "quarterly":        quarterly,
        "quarterly_prev":   quarterly_prev,
        "anomalies":        anomalies,
        "groups_meta":      groups_meta,
        "articles_found":   sorted(all_articles_found),
        # Сводка текущего месяца
        "current": {
            "revenue":      cur_pnl.get("revenue", 0),
            "cogs":         cur_pnl.get("cogs", 0),
            "gross_profit": cur_pnl.get("gross_profit", 0),
            "gross_margin": cur_pnl.get("gross_margin", 0),
            "fot":          cur_pnl.get("fot", 0),
            "fot_pct":      cur_pnl.get("fot_pct", 0),
            "ebitda":       cur_pnl.get("ebitda", 0),
            "ebitda_margin":cur_pnl.get("ebitda_margin", 0),
            "net_profit":   cur_pnl.get("net_profit", 0),
            "net_margin":   cur_pnl.get("net_margin", 0),
        },
    }

    with open("pnl_data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"✓ pnl_data.json сформирован")
    print(f"  Месяцев: {len(months_list)}")
    print(f"  Статей расходов найдено: {len(all_articles_found)}")
    print(f"  Аномалий: {len(anomalies)}")
    if cur_pnl:
        print(f"\n  Текущий месяц ({cur_month_key}):")
        print(f"    Выручка:        {cur_pnl.get('revenue',0)/1e6:.1f} млн")
        print(f"    Валовая прибыль:{cur_pnl.get('gross_margin',0):.1f}%")
        print(f"    EBITDA:         {cur_pnl.get('ebitda_margin',0):.1f}%")
        print(f"    Чистая прибыль: {cur_pnl.get('net_margin',0):.1f}%")

if __name__ == "__main__":
    build()
