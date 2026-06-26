"""
build_pnl_data.py  —  Агрегатор P&L для Garden Coffee (v3 — OLAP формат)

Читает:   pnl_data_raw.json   (из pnl_extract.js / iikoWeb OLAP)
Пишет:    pnl_data.json       (для coffee_pnl.html)
          pnl_data_embed.js   (для открытия coffee_pnl.html через file://)

Структура входных данных (pnl_data_raw.json):
    months[YYYY-MM]:
        summary   — KPI метрики: PL_SALES_TOTAL, PL_COS_TOTAL, PL_PROFIT_NET и др.
        by_store  — KPI по каждой точке
        olap      — {storeId: [{Account.Type, Account.AccountHierarchyTop,
                                Account.AccountHierarchySecond, sum_signed}]}

Структура P&L из iiko OLAP:
    INCOME                    → Выручка (sum_signed > 0) и скидки (< 0)
    COST_OF_GOODS_SOLD        → Себестоимость + прямая ЗП
    EXPENSES                  → Операционные расходы (аренда, коммуналка, персонал, маркетинг...)
    OTHER_EXPENSES            → Амортизация, налоги, прочее ниже EBITDA
    OTHER_INCOME              → Прочие доходы ниже EBITDA
"""

import json
import os
import re
import csv
from collections import Counter
from datetime import date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ─── Маппинг статей OLAP → группы дашборда ───────────────────────────────────
# Ключ — точное название Account.AccountHierarchySecond из iiko OLAP
# group:  cogs | staff | rent | infra | marketing | admin | amort | taxes | other
# alias:  короткое название для отображения

EXPENSE_ITEMS = {
    # ── COST_OF_GOODS_SOLD → cogs ─────────────────────────────────────────────
    "СЕБЕСТОИМОСТЬ":                          {"group": "cogs",      "alias": "Себестоимость продуктов"},
    "ЗАРАБОТНАЯ ПЛАТА":                       {"group": "staff",     "alias": "ЗП (прямая)"},
    "НАЛОГИ И ВЗНОСЫ":                        {"group": "staff",     "alias": "Налоги с прямой ЗП"},

    # ── EXPENSES → Аренда ─────────────────────────────────────────────────────
    "Арендная плата":                         {"group": "rent",      "alias": "Аренда"},

    # ── EXPENSES → Коммуналка и инфраструктура ────────────────────────────────
    "Коммунальные услуги":                    {"group": "infra",     "alias": "Коммуналка"},
    "Телефония/Интернет":                     {"group": "infra",     "alias": "Телефония/Интернет"},
    "ТО и ремонт оборудования, инвентаря":   {"group": "infra",     "alias": "ТО оборудования"},
    "Ремонт и обслуживание помещений":        {"group": "infra",     "alias": "Ремонт помещений"},
    "Транспортные расходы":                   {"group": "infra",     "alias": "Транспорт"},
    "Чистка жироуловителя":                   {"group": "infra",     "alias": "Чистка жироуловителя"},
    "Вывоз мусора":                           {"group": "infra",     "alias": "Вывоз мусора"},
    "Замена и чистка ковриков":               {"group": "infra",     "alias": "Ковры/уборка"},
    "Клининговые услуги и материалы":         {"group": "infra",     "alias": "Клининг"},
    "Оформление торгового зала(дизайн и озеленение)": {"group": "infra", "alias": "Оформление зала"},
    "Расходы на охранно- пожарные мероприятия": {"group": "infra",  "alias": "Охрана/пожар"},
    "Содержание оргтехники и компьютерных сетей": {"group": "infra","alias": "Оргтехника/сети"},
    "Дератизация и дезинфекция":              {"group": "infra",     "alias": "Дератизация"},
    "Изготовление мебели":                    {"group": "infra",     "alias": "Мебель"},
    "ГСМ ":                                   {"group": "infra",     "alias": "ГСМ"},
    "ГСМ":                                    {"group": "infra",     "alias": "ГСМ"},
    "Химчистка":                              {"group": "infra",     "alias": "Химчистка"},

    # ── EXPENSES → Персонал (административный) ───────────────────────────────
    "РАСХОДЫ НА ПЕРСОНАЛ":                    {"group": "staff",     "alias": "ЗП (административная)"},
    "Заработная плата административный персонал": {"group": "staff", "alias": "ЗП адм. персонала"},
    "Налог НДФЛ административный  персонал":  {"group": "staff",     "alias": "НДФЛ адм."},
    "Страховые взносы ПФ/ФСС/административный персонал": {"group": "staff", "alias": "Взносы ПФ/ФСС адм."},
    "Депозит iikocard5(питание за счет бонусной системы)": {"group": "staff", "alias": "Питание сотрудников (бонусы)"},

    # ── EXPENSES → Маркетинг ─────────────────────────────────────────────────
    "Расходы на рекламу, дизайн и маркетинг": {"group": "marketing", "alias": "Реклама и маркетинг"},
    "Гостю":                                  {"group": "marketing", "alias": "Гостю"},
    "Первый Гость и Подарок в День Рождения ": {"group": "marketing","alias": "Первый гость/ДР"},
    "Первый Гость и Подарок в День Рождения": {"group": "marketing", "alias": "Первый гость/ДР"},
    "Представительские расходы":              {"group": "marketing", "alias": "Представительские"},
    "Выездные мероприятия":                   {"group": "marketing", "alias": "Выездные мероприятия"},
    "Маркетинговые услуги":                   {"group": "marketing", "alias": "Маркетинговые услуги"},

    # ── EXPENSES → Административные расходы ─────────────────────────────────
    "Лицензии/ПО/сертификация":               {"group": "admin",     "alias": "Лицензии/ПО"},
    "Банковские услуги/РКО":                  {"group": "admin",     "alias": "Банковские услуги"},
    "Консультационные услуги":                {"group": "admin",     "alias": "Консультации"},
    "РАО и ВОИС":                             {"group": "admin",     "alias": "РАО/ВОИС"},
    "Расходы на охранно-пожарные мероприятия": {"group": "admin",    "alias": "Охрана/пожар"},
    "Почта/ Курьерские расходы":              {"group": "admin",     "alias": "Курьерские"},
    "Почта/Курьерские расходы":               {"group": "admin",     "alias": "Курьерские"},
    "Госпошлины/Штрафы":                      {"group": "admin",     "alias": "Госпошлины/Штрафы"},
    "Канцтовары":                             {"group": "admin",     "alias": "Канцтовары"},
    "Услуги Управляющей компании":            {"group": "admin",     "alias": "Услуги УК"},

    # ── EXPENSES → Списания ТМЦ ───────────────────────────────────────────────
    "СПИСАННЫЕ  ОС/ИНВЕНТАРЬ/ПОСУДА ":        {"group": "tmc",       "alias": "Списания ОС/инвентарь"},
    "СПИСАННЫЕ  ОС/ИНВЕНТАРЬ/ПОСУДА":         {"group": "tmc",       "alias": "Списания ОС/инвентарь"},

    # ── OTHER_EXPENSES → Амортизация и налоги ────────────────────────────────
    "Амортизация":                            {"group": "amort",     "alias": "Амортизация"},
    "Налоги с доходов/Прибыль/УСН":           {"group": "taxes",     "alias": "Налог (УСН/прибыль)"},
    "Прочие расходы*":                        {"group": "other",     "alias": "Прочие расходы"},
    "Списание ОС и Прочих ОС":                {"group": "other",     "alias": "Списание ОС"},
}

# Группы для отображения (порядок важен)
GROUPS = {
    "cogs":      "Себестоимость",
    "staff":     "Персонал",
    "rent":      "Аренда",
    "infra":     "Инфраструктура",
    "marketing": "Маркетинг",
    "admin":     "Административные",
    "tmc":       "ТМЦ/Списания",
    "amort":     "Амортизация",
    "taxes":     "Налоги",
    "other":     "Прочее",
}

# Группы, входящие в OPEX (всё ниже валовой прибыли, выше EBITDA)
OPEX_GROUPS = {"staff", "rent", "infra", "marketing", "admin", "tmc"}
# Группы ниже EBITDA (амортизация, налоги)
BELOW_EBITDA_GROUPS = {"amort", "taxes", "other"}

ANOMALY_THRESHOLD = 30.0
MONITOR_ANOMALY_THRESHOLD = 30.0
MONITOR_MIN_AMOUNT = 10_000

MONITOR_SOURCE_OVERRIDES = {
    "Расход продуктов/Себестоимость": ["СЕБЕСТОИМОСТЬ"],
    "Прочие ТМЦ списанные": ["СПИСАННЫЕ  ОС/ИНВЕНТАРЬ/ПОСУДА", "СПИСАННЫЕ  ОС/ИНВЕНТАРЬ/ПОСУДА "],
    "Инвентарь списанный": ["СПИСАННЫЕ  ОС/ИНВЕНТАРЬ/ПОСУДА", "СПИСАННЫЕ  ОС/ИНВЕНТАРЬ/ПОСУДА "],
    "Бой посуды": ["СПИСАННЫЕ  ОС/ИНВЕНТАРЬ/ПОСУДА", "СПИСАННЫЕ  ОС/ИНВЕНТАРЬ/ПОСУДА "],
    "Бесплатная еда для сотрудников": ["Депозит iikocard5(питание за счет бонусной системы)"],
    "Бесплатные напитки для сотрудников": ["Депозит iikocard5(питание за счет бонусной системы)"],
    "Расходы из Амортизационного фонда": ["Амортизация"],
}

# Имена точек (из decoration.restaurant iikoWeb KPI API, актуально на 2026-06)
STORE_NAMES = {
    "56178": "Паруса",       "56188": "Океан",         "56190": "Калинка",
    "56193": "Новин",        "56197": "Европа",         "56203": "Garden КЦ",
    "56458": "Советская",    "59619": "Панорама",       "80477": "Обжарка",
    "80486": "Кондитерская", "86753": "Газпром",        "87392": "Мельникайте",
    "94945": "Преображенский","100421": "Арсиб",        "108119": "Свердлова",
    "115697": "Драмтеатр",   "120401": "Гарден Сургут", "145308": "Видный",
    "156443": "Осипенко",    "170714": "Гарден Тобольск","172412": "Гагарина",
    "176065": "Ворлд Класс", "178149": "Домашний",
}

# ВАЖНО: by_store в pnl_data_raw.json хранится как {метрика: {GUID: значение}}
# (не {storeId: {метрика: значение}}).
# Маппинг intId → GUID (из decoration.restaurant iikoWeb KPI API, 2026-06)
ID_TO_GUID = {
    "56178": "bf2729c7-d215-41b5-89b2-8089fc36edb8",
    "56188": "0e04edaf-25fa-4afc-8343-a6a1cda6bc8a",
    "56190": "f23dcc5d-37f5-44d1-a0c8-8ae2121420fc",
    "56193": "31831cf5-f23b-4622-bf75-e532f7cbdb37",
    "56197": "2c27e7c0-af93-460f-bdc7-543edffbbb33",
    "56203": "b4d9fbbf-c90d-4a11-8613-14b13f33b700",
    "56458": "86db8cfa-e42e-4701-a9b6-f716876bf635",
    "59619": "9d02590c-0e27-490a-9cf9-3dc289b455dc",
    "80477": "ac8ce19e-c6b7-4f37-8af1-d7e2d8b1be51",
    "80486": "e540ffd3-b50e-4e90-88db-4923b0d4a0a1",
    "86753": "1f7ef850-98dd-4f8f-b284-13607a5729ea",
    "87392": "cf12ead5-65b4-43b4-a963-e0a2bc2cf933",
    "94945": "fa4159b1-e1c2-4e4b-87fb-7bcc7ed4fde5",
    "100421": "26c4cb2a-fce0-412c-ab52-fc3cecd5e561",
    "108119": "3b6aa85a-304c-4cf4-8738-5e5b309bf5e2",
    "115697": "6f1bfbf0-ce0f-456d-9cf2-6678dce1c996",
    "120401": "046dd863-3c66-47ac-9482-226293dbad0a",
    "145308": "a097ccea-e629-4011-a1c1-faa4036e492a",
    "156443": "72147db6-894a-4715-92f3-e37bd2b5bf3d",
    "170714": "75df7ed6-62bb-4bd3-8262-d200941145a3",
    "172412": "be65c662-82d8-4541-b143-da6ea73c2485",
    "176065": "d0b9b947-38f0-4a9a-86f9-cf81c1396a87",
    "178149": "eac753be-4c5c-4a95-a8ac-7ba0647ab7ee",
}

STORE_IDS = list(STORE_NAMES.keys())


def store_kpi(by_store_metric_keyed, sid, metric):
    """by_store = {metricCode: {GUID: value}} — читает метрику для точки по int ID."""
    guid = ID_TO_GUID.get(str(sid))
    if not guid:
        return 0
    return (by_store_metric_keyed.get(metric) or {}).get(guid, 0) or 0

MONTH_LABELS_RU = ["Янв","Фев","Мар","Апр","Май","Июн",
                   "Июл","Авг","Сен","Окт","Ноя","Дек"]


# ─── Утилиты ──────────────────────────────────────────────────────────────────
def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def norm_name(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def pct(a, b):
    return round(a / b * 100, 1) if b else 0.0


def month_label(mkey):
    parts = mkey.split("-")
    if len(parts) == 2:
        return f"{MONTH_LABELS_RU[int(parts[1])-1]} {parts[0]}"
    return mkey


def period_days_from_olap(olap_by_store):
    days = []
    for rows in (olap_by_store or {}).values():
        for row in rows:
            value = row.get("_BusinessDays")
            if isinstance(value, (int, float)) and value > 0:
                days.append(int(value))
    if not days:
        return None
    return Counter(days).most_common(1)[0][0]


def comparable_previous_value(cur_value, prev_value, cur_period_days=None, prev_period_days=None):
    if cur_period_days and prev_period_days and prev_period_days > 0:
        return prev_value * cur_period_days / prev_period_days
    return prev_value


def comparable_period_note(cur_period_days=None, prev_period_days=None):
    if cur_period_days and prev_period_days and cur_period_days != prev_period_days:
        return f"{cur_period_days} дн. против {prev_period_days} дн.; прошлый месяц приведён к текущему периоду"
    if cur_period_days:
        return f"{cur_period_days} дн. против прошлого месяца"
    return "текущий период против прошлого месяца"


# ─── Агрегация OLAP строк по месяцу ──────────────────────────────────────────
def aggregate_olap(olap_by_store):
    """
    olap_by_store: {storeId: [{Account.Type, Account.AccountHierarchyTop,
                                Account.AccountHierarchySecond, sum_signed}]}
    Возвращает:
        items: {item_name: sum_signed}  — агрегировано по всем точкам
        by_store: {storeId: {item_name: sum_signed}}
        revenue_olap: выручка из OLAP
    """
    items = {}       # network aggregate
    by_store = {}    # per store

    for store_id, rows in olap_by_store.items():
        store_items = {}
        for row in rows:
            item  = (row.get("Account.AccountHierarchySecond") or "").strip()
            atype = row.get("Account.Type", "")
            val   = row.get("sum_signed", 0) or 0

            if not item or item == "null":
                continue

            store_items[item] = store_items.get(item, 0) + val
            items[item]       = items.get(item, 0) + val

        by_store[str(store_id)] = store_items

    return items, by_store


def calc_pnl_from_olap(items, summary_kpi):
    """
    Рассчитывает P&L из OLAP items + KPI summary.
    Использует KPI summary как авторитетный источник для выручки и чистой прибыли.
    """
    # Выручка из KPI (надёжнее, включает все поправки)
    revenue   = abs(summary_kpi.get("PL_SALES_TOTAL", 0) or 0)
    cogs_kpi  = abs(summary_kpi.get("PL_COS_TOTAL",   0) or 0)
    exp_kpi   = abs(summary_kpi.get("PL_EXP_TOTAL",   0) or 0)
    gp_kpi    = summary_kpi.get("PL_PROFIT_GROSS", 0) or 0
    np_kpi    = summary_kpi.get("PL_PROFIT_NET",   0) or 0

    # Детализация по статьям из OLAP
    item_totals = {}
    for iiko_name in EXPENSE_ITEMS:
        item_totals[iiko_name] = abs(items.get(iiko_name, 0))

    # Итоги по группам
    groups = {}
    for gkey in GROUPS:
        groups[gkey] = sum(
            item_totals[k]
            for k, v in EXPENSE_ITEMS.items()
            if v["group"] == gkey
        )

    cogs_olap = groups.get("cogs", 0)
    opex_olap = sum(groups.get(g, 0) for g in OPEX_GROUPS)

    # Предпочитаем KPI для топ-уровневых показателей
    cogs        = cogs_kpi  if cogs_kpi  > 0 else cogs_olap
    gross_profit= gp_kpi    if gp_kpi    != 0 else (revenue - cogs)
    opex        = exp_kpi   if exp_kpi   > 0 else opex_olap
    ebitda      = gross_profit - opex
    net_profit  = np_kpi    if np_kpi    != 0 else (ebitda - groups.get("taxes", 0) - groups.get("amort", 0))

    return {
        "revenue":          round(revenue),
        "cogs":             round(cogs),
        "gross_profit":     round(gross_profit),
        "opex":             round(opex),
        "ebitda":           round(ebitda),
        "net_profit":       round(net_profit),
        "cogs_pct":         pct(cogs, revenue),
        "gross_margin_pct": pct(gross_profit, revenue),
        "opex_pct":         pct(opex, revenue),
        "ebitda_pct":       pct(ebitda, revenue),
        "net_margin_pct":   pct(net_profit, revenue),
        "groups":           {gk: round(gv) for gk, gv in groups.items()},
        "items":            {k: round(v) for k, v in item_totals.items()},
    }


def calc_pnl_from_flat_kpi(summary_kpi):
    """Рассчитывает P&L для старого raw-формата: months[YYYY-MM] = KPI dict."""
    revenue       = abs(summary_kpi.get("PL_SALES_TOTAL", 0) or 0)
    cogs          = abs(summary_kpi.get("PL_COS_TOTAL", 0) or 0)
    opex          = abs(summary_kpi.get("PL_EXP_TOTAL", 0) or 0)
    other_expense = abs(summary_kpi.get("PL_OTH_EXP_TOTAL", 0) or 0)
    other_income  = abs(summary_kpi.get("PL_OTH_INCOME_TOTAL", 0) or 0)
    gross_profit  = summary_kpi.get("PL_PROFIT_GROSS", 0) or (revenue - cogs)
    ebitda        = summary_kpi.get("PL_PROFIT_MAIN", 0) or (gross_profit - opex)
    net_profit    = summary_kpi.get("PL_PROFIT_NET", 0) or (ebitda - other_expense + other_income)

    groups = {gk: 0 for gk in GROUPS}
    groups["cogs"] = round(cogs)
    groups["admin"] = round(opex)
    groups["other"] = round(other_expense - other_income)

    return {
        "revenue":          round(revenue),
        "cogs":             round(cogs),
        "gross_profit":     round(gross_profit),
        "opex":             round(opex),
        "ebitda":           round(ebitda),
        "net_profit":       round(net_profit),
        "cogs_pct":         pct(cogs, revenue),
        "gross_margin_pct": pct(gross_profit, revenue),
        "opex_pct":         pct(opex, revenue),
        "ebitda_pct":       pct(ebitda, revenue),
        "net_margin_pct":   pct(net_profit, revenue),
        "groups":           groups,
        "items":            {k: 0 for k in EXPENSE_ITEMS},
    }


# ─── By-store KPI ─────────────────────────────────────────────────────────────
def calc_by_store(by_store_olap, by_store_kpi):
    """
    Возвращает {store_name: {store_id, revenue, net_profit, gross_profit, opex, ebitda, items, groups}}
    """
    result = {}

    # by_store_kpi структура: {metricCode: {GUID: value}} — используем store_kpi()
    # by_store_olap структура: {intId: {item_name: sum}}

    for sid in STORE_IDS:
        olap_items = by_store_olap.get(str(sid), {})

        # Читаем KPI через GUID-маппинг
        revenue = abs(store_kpi(by_store_kpi, sid, "PL_SALES_TOTAL"))
        np      = store_kpi(by_store_kpi, sid, "PL_PROFIT_NET")
        gp      = store_kpi(by_store_kpi, sid, "PL_PROFIT_GROSS")

        item_totals = {k: abs(olap_items.get(k, 0)) for k in EXPENSE_ITEMS}
        groups = {gkey: sum(item_totals[k] for k, v in EXPENSE_ITEMS.items() if v["group"] == gkey)
                  for gkey in GROUPS}

        opex   = sum(groups.get(g, 0) for g in OPEX_GROUPS)
        ebitda = gp - opex

        name = STORE_NAMES.get(str(sid), f"ID {sid}")
        result[name] = {
            "store_id":      str(sid),
            "revenue":       round(revenue),
            "net_profit":    round(np),
            "gross_profit":  round(gp),
            "opex":          round(opex),
            "ebitda":        round(ebitda),
            "items":         {k: round(v) for k, v in item_totals.items()},
            "groups":        {k: round(v) for k, v in groups.items()},
        }

    return result


# ─── Аномалии ─────────────────────────────────────────────────────────────────
def detect_anomalies(months_summary, threshold=ANOMALY_THRESHOLD):
    anomalies = []

    for i, cur in enumerate(months_summary):
        rev = cur["summary"]["revenue"]
        if not rev:
            continue

        prev_mom = months_summary[i - 1] if i > 0 else None
        if not prev_mom:
            continue

        cur_days = cur.get("period_days")
        prev_days = prev_mom.get("period_days")
        period_note = comparable_period_note(cur_days, prev_days)

        for iiko_name, cfg in EXPENSE_ITEMS.items():
            val = cur["summary"]["items"].get(iiko_name, 0)
            val_pct = pct(val, rev)

            prev_rev = comparable_previous_value(
                cur["summary"].get("revenue", 0),
                prev_mom["summary"].get("revenue", 0),
                cur_days,
                prev_days,
            )
            prev_val_raw = prev_mom["summary"]["items"].get(iiko_name, 0)
            prev_val = comparable_previous_value(val, prev_val_raw, cur_days, prev_days)
            prev_pct = pct(prev_val, prev_rev)

            if prev_val > 0:
                dev_pct = (val - prev_val) / prev_val * 100
            elif val > 0:
                dev_pct = 100.0
            else:
                continue

            if abs(dev_pct) >= threshold and (val > 50000 or prev_val > 50000):
                anomalies.append({
                    "type":       "PrevPeriod",
                    "mkey":       cur["mkey"],
                    "label":      cur["label"],
                    "item":       iiko_name,
                    "alias":      cfg["alias"],
                    "group":      cfg["group"],
                    "value":      round(val),
                    "value_pct":  val_pct,
                    "prev_value": round(prev_val),
                    "prev_value_raw": round(prev_val_raw),
                    "prev_pct":   prev_pct,
                    "prev_label": prev_mom["label"],
                    "dev_pct":    round(dev_pct, 1),
                    "severity":   "high" if abs(dev_pct) >= 60 else "warn",
                    "period_days": cur_days,
                    "prev_period_days": prev_days,
                    "period_note": period_note,
                })

    anomalies.sort(key=lambda a: -abs(a["dev_pct"]))
    return anomalies


def build_expense_monitoring(raw, months_summary):
    monitor_path = os.path.join(BASE_DIR, "expense_monitor_items.json")
    monitor_items = load_json(monitor_path) or []
    if not monitor_items:
        return {"items": [], "anomalies": [], "coverage": {}}

    item_totals_by_month = {}
    raw_names_by_norm = {}

    for mkey, mdata in raw.get("months", {}).items():
        olap_raw = mdata.get("olap") if isinstance(mdata, dict) else None
        totals = {}
        if olap_raw:
            items, _ = aggregate_olap(olap_raw)
            for raw_name, value in items.items():
                n = norm_name(raw_name)
                raw_names_by_norm[n] = raw_name
                totals[raw_name] = abs(value or 0)
        item_totals_by_month[mkey] = totals

    by_mkey = {m["mkey"]: m for m in months_summary}
    result_items = []
    anomalies = []

    for entry in monitor_items:
        order = entry.get("order")
        name = entry.get("name", "")
        sources = []
        status = "not_in_raw"

        exact = raw_names_by_norm.get(norm_name(name))
        if exact:
            sources = [exact]
            status = "exact_match"
        else:
            for candidate in MONITOR_SOURCE_OVERRIDES.get(name.strip(), []):
                actual = raw_names_by_norm.get(norm_name(candidate))
                if actual and actual not in sources:
                    sources.append(actual)
            if sources:
                status = "rollup_match"

        monthly = []
        for m in months_summary:
            mkey = m["mkey"]
            value = sum(item_totals_by_month.get(mkey, {}).get(src, 0) for src in sources)
            revenue = m["summary"].get("revenue", 0)
            monthly.append({
                "mkey": mkey,
                "label": m["label"],
                "year": m["year"],
                "value": round(value),
                "revenue_pct": pct(value, revenue),
            })

        current_year = date.today().year
        ytd_months = [m for m in monthly if m["year"] == current_year]
        ytd_value = sum(m["value"] for m in ytd_months)
        last = next((m for m in reversed(monthly) if m["value"] or m["year"] == current_year), monthly[-1] if monthly else None)

        for i, cur in enumerate(monthly):
            if status == "not_in_raw":
                continue
            prev_mom = monthly[i - 1] if i > 0 else None
            if not prev_mom:
                continue

            val = cur["value"]
            cur_source_month = by_mkey.get(cur["mkey"], {})
            prev_source_month = by_mkey.get(prev_mom["mkey"], {})
            cur_days = cur_source_month.get("period_days")
            prev_days = prev_source_month.get("period_days")
            prev_val_raw = prev_mom["value"]
            prev_val = comparable_previous_value(val, prev_val_raw, cur_days, prev_days)
            prev_rev = comparable_previous_value(
                cur_source_month.get("summary", {}).get("revenue", 0),
                prev_source_month.get("summary", {}).get("revenue", 0),
                cur_days,
                prev_days,
            )
            prev_pct = pct(prev_val, prev_rev)

            if prev_val > 0:
                dev_pct = (val - prev_val) / prev_val * 100
            elif val > 0:
                dev_pct = 100.0
            else:
                continue

            if abs(dev_pct) >= MONITOR_ANOMALY_THRESHOLD and max(abs(val), abs(prev_val)) >= MONITOR_MIN_AMOUNT:
                anomalies.append({
                    "type": "PrevPeriod",
                    "mkey": cur["mkey"],
                    "label": cur["label"],
                    "item": name,
                    "order": order,
                    "status": status,
                    "source_names": sources,
                    "value": round(val),
                    "value_pct": cur["revenue_pct"],
                    "prev_value": round(prev_val),
                    "prev_value_raw": round(prev_val_raw),
                    "prev_pct": prev_pct,
                    "prev_label": prev_mom["label"],
                    "dev_pct": round(dev_pct, 1),
                    "severity": "high" if abs(dev_pct) >= 60 else "warn",
                    "period_days": cur_days,
                    "prev_period_days": prev_days,
                    "period_note": comparable_period_note(cur_days, prev_days),
                })

        result_items.append({
            "order": order,
            "name": name,
            "status": status,
            "source_names": sources,
            "monthly": monthly,
            "ytd_value": round(ytd_value),
            "last_value": last["value"] if last else 0,
            "last_label": last["label"] if last else None,
        })

    anomalies.sort(key=lambda a: (a["mkey"], abs(a["dev_pct"])), reverse=True)
    coverage = {
        "total": len(result_items),
        "exact_match": sum(1 for i in result_items if i["status"] == "exact_match"),
        "rollup_match": sum(1 for i in result_items if i["status"] == "rollup_match"),
        "not_in_raw": sum(1 for i in result_items if i["status"] == "not_in_raw"),
    }
    return {"items": result_items, "anomalies": anomalies, "coverage": coverage}


def export_expense_monitoring_csv(output, base_dir):
    monitoring = output.get("expense_monitoring") or {}
    months = [m["mkey"] for m in output.get("months", [])]
    summary_path = os.path.join(base_dir, "expense_monitoring_summary.csv")
    anomalies_path = os.path.join(base_dir, "expense_monitoring_anomalies.csv")

    with open(summary_path, "w", encoding="utf-8-sig", newline="") as f:
        columns = ["order", "item", "status", "source_names", "ytd_value",
                   "last_label", "last_value"] + months
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for item in monitoring.get("items", []):
            values_by_month = {m["mkey"]: m["value"] for m in item.get("monthly", [])}
            row = {
                "order": item.get("order"),
                "item": item.get("name"),
                "status": item.get("status"),
                "source_names": " | ".join(item.get("source_names") or []),
                "ytd_value": item.get("ytd_value", 0),
                "last_label": item.get("last_label"),
                "last_value": item.get("last_value", 0),
            }
            row.update({mkey: values_by_month.get(mkey, 0) for mkey in months})
            writer.writerow(row)

    with open(anomalies_path, "w", encoding="utf-8-sig", newline="") as f:
        columns = ["mkey", "label", "type", "item", "status", "source_names",
                   "value", "value_pct", "prev_label", "prev_value", "prev_value_raw",
                   "prev_pct", "dev_pct", "severity", "period_days", "prev_period_days",
                   "period_note"]
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for anomaly in monitoring.get("anomalies", []):
            row = {key: anomaly.get(key) for key in columns}
            row["source_names"] = " | ".join(anomaly.get("source_names") or [])
            writer.writerow(row)


# ─── Главная функция ──────────────────────────────────────────────────────────
def build():
    raw = load_json(os.path.join(BASE_DIR, "pnl_data_raw.json"))

    if not raw:
        print("❌ pnl_data_raw.json не найден.")
        print("   Запусти pnl_extract.js в браузере iikoWeb, скачай файл в эту папку.")
        return

    today = date.today()
    months_summary = []

    for mkey in sorted(raw.get("months", {}).keys()):
        mdata      = raw["months"][mkey]
        period_days = None
        if any(k.startswith("PL_") for k in mdata.keys()):
            # Старый raw-формат: в месяце лежит сразу dict KPI без summary/by_store/olap.
            summary = calc_pnl_from_flat_kpi(mdata)
            by_store = {}
        else:
            summary_kpi = mdata.get("summary") or {}
            by_store_kpi = mdata.get("by_store") or {}
            olap_raw   = mdata.get("olap") or {}
            period_days = period_days_from_olap(olap_raw)

            # Агрегируем OLAP
            items, by_store_olap = aggregate_olap(olap_raw)

            # Считаем P&L
            summary = calc_pnl_from_olap(items, summary_kpi)

            # По точкам
            by_store = calc_by_store(by_store_olap, by_store_kpi)

        months_summary.append({
            "mkey":     mkey,
            "label":    month_label(mkey),
            "year":     int(mkey.split("-")[0]),
            "period_days": period_days,
            "summary":  summary,
            "by_store": by_store,
        })

    if not months_summary:
        print("❌ Нет данных в pnl_data_raw.json")
        return

    cur_year = today.year
    current_months = [m for m in months_summary if m["year"] == cur_year]
    last = current_months[-1] if current_months else months_summary[-1]

    # YTD
    ytd = {
        "label":            f"YTD {cur_year}",
        "months_count":     len(current_months),
        "revenue":          sum(m["summary"]["revenue"]      for m in current_months),
        "cogs":             sum(m["summary"]["cogs"]         for m in current_months),
        "gross_profit":     sum(m["summary"]["gross_profit"] for m in current_months),
        "opex":             sum(m["summary"]["opex"]         for m in current_months),
        "ebitda":           sum(m["summary"]["ebitda"]       for m in current_months),
        "net_profit":       sum(m["summary"]["net_profit"]   for m in current_months),
    }
    ytd_rev = ytd["revenue"]
    ytd.update({
        "cogs_pct":         pct(ytd["cogs"],         ytd_rev),
        "gross_margin_pct": pct(ytd["gross_profit"],  ytd_rev),
        "opex_pct":         pct(ytd["opex"],          ytd_rev),
        "ebitda_pct":       pct(ytd["ebitda"],        ytd_rev),
        "net_margin_pct":   pct(ytd["net_profit"],    ytd_rev),
        "items":  {k: sum(m["summary"]["items"].get(k,0) for m in current_months) for k in EXPENSE_ITEMS},
        "groups": {g: sum(m["summary"]["groups"].get(g,0) for m in current_months) for g in GROUPS},
    })

    anomalies = detect_anomalies(months_summary)
    expense_monitoring = build_expense_monitoring(raw, months_summary)

    items_meta  = {k: {"alias": v["alias"], "group": v["group"]} for k, v in EXPENSE_ITEMS.items()}
    groups_meta = [{"key": k, "label": v} for k, v in GROUPS.items()]

    output = {
        "generated_at":         today.isoformat(),
        "current_year":         cur_year,
        "current_month":        last["mkey"],
        "current_month_label":  last["label"],
        "has_expense_data":     True,
        "store_names":          STORE_NAMES,
        "items_meta":           items_meta,
        "groups_meta":          groups_meta,
        "summary_ytd":          ytd,
        "months":               months_summary,
        "anomalies":            anomalies,
        "expense_monitoring":    expense_monitoring,
        "by_store_current":     last["by_store"],
    }

    out_path = os.path.join(BASE_DIR, "pnl_data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    embed_path = os.path.join(BASE_DIR, "pnl_data_embed.js")
    with open(embed_path, "w", encoding="utf-8") as f:
        f.write("window.PNL_DATA = ")
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")

    export_expense_monitoring_csv(output, BASE_DIR)

    rev_m   = ytd_rev / 1e6
    gp_m    = ytd["gross_profit"] / 1e6
    ebitda_m= ytd["ebitda"] / 1e6
    np_m    = ytd["net_profit"] / 1e6

    print(f"✅ pnl_data.json готов")
    print(f"   Месяцев всего: {len(months_summary)}, текущий год: {len(current_months)}")
    print(f"   Статей расходов: {len(EXPENSE_ITEMS)}, аномалий: {len(anomalies)}")
    mon_cov = expense_monitoring.get("coverage", {})
    print(f"   Мониторинг статей: {mon_cov.get('exact_match',0)} exact, "
          f"{mon_cov.get('rollup_match',0)} rollup, {mon_cov.get('not_in_raw',0)} нет в raw; "
          f"аномалий: {len(expense_monitoring.get('anomalies', []))}")
    print(f"\n   YTD {cur_year}:")
    print(f"   Выручка:        {rev_m:>8.1f} M₽")
    print(f"   Вал. прибыль:   {gp_m:>8.1f} M₽  ({pct(ytd['gross_profit'], ytd_rev)}%)")
    print(f"   EBITDA:         {ebitda_m:>8.1f} M₽  ({pct(ytd['ebitda'], ytd_rev)}%)")
    print(f"   Чист. прибыль:  {np_m:>8.1f} M₽  ({pct(ytd['net_profit'], ytd_rev)}%)")

    print(f"\n   Топ статьи (YTD):")
    items_ytd = sorted(ytd["items"].items(), key=lambda x: -x[1])
    for name, val in items_ytd[:8]:
        if val > 100000:
            pct_of_rev = pct(val, ytd_rev)
            print(f"     {EXPENSE_ITEMS[name]['alias']:<35} {val/1e6:>6.1f} M₽  ({pct_of_rev}%)")


if __name__ == "__main__":
    build()
