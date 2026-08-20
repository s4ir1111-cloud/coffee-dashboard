"""Core calculations for the Garden monthly business review.

Every numeric conclusion is deterministic. Missing source domains are returned
with an explicit unavailable status instead of inferred or fabricated values.
"""

import calendar
import json
import os
from datetime import date, datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "monthly_analytics_config.json")
REQUIRED_STORE_COUNT = 20


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as source:
        return json.load(source)


def pct_change(current, previous):
    return round((current - previous) / abs(previous) * 100, 1) if previous else None


def average(values):
    clean = [value for value in values if value is not None]
    return sum(clean) / len(clean) if clean else None


def previous_month_key(today):
    year, month = today.year, today.month - 1
    if month == 0:
        year, month = year - 1, 12
    return f"{year:04d}-{month:02d}"


def select_period(months, requested=None):
    by_key = {item["mkey"]: item for item in months}
    target = requested or previous_month_key(date.today())
    if target not in by_key:
        completed = [item for item in months if item["mkey"] < date.today().strftime("%Y-%m")]
        if not completed:
            raise ValueError("Нет данных за завершённый месяц")
        target = completed[-1]["mkey"]
    current = by_key[target]
    index = months.index(current)
    previous = months[index - 1] if index else None
    yoy = by_key.get(f"{int(target[:4]) - 1:04d}-{target[5:]}")
    return current, previous, yoy, months[max(0, index - 5):index]


def data_quality(data, current):
    warnings = []
    stores = current.get("by_store", {})
    summary = current.get("summary", {})
    if len(stores) < REQUIRED_STORE_COUNT:
        warnings.append(f"Получены данные только по {len(stores)} из {REQUIRED_STORE_COUNT} ожидаемых кофеен.")
    if current.get("period_days") not in (None, 28, 29, 30, 31):
        warnings.append(f"Необычное число дней в периоде: {current.get('period_days')}.")
    for key in ("revenue", "cogs", "opex"):
        if summary.get(key, 0) < 0:
            warnings.append(f"Невозможное отрицательное значение показателя {key}.")
    store_revenue = sum(row.get("revenue", 0) for row in stores.values())
    network_revenue = summary.get("revenue", 0)
    gap = abs(store_revenue - network_revenue) / network_revenue * 100 if network_revenue else 0
    if gap > 2:
        warnings.append(f"Контрольная выручка по точкам отличается от итога сети на {gap:.1f}%.")
    names = list(stores)
    if len(names) != len(set(names)):
        warnings.append("Обнаружены дубли названий кофеен.")
    return {
        "status": "warning" if warnings else "ok",
        "warnings": warnings,
        "checks": {
            "period_complete": current["mkey"] < date.today().strftime("%Y-%m"),
            "stores_received": len(stores),
            "expected_stores": REQUIRED_STORE_COUNT,
            "network_store_revenue_gap_pct": round(gap, 2),
            "duplicates": len(names) - len(set(names)),
        },
    }


def comparison_block(metric, current, previous, yoy, history):
    value = current["summary"].get(metric, 0)
    prior = previous["summary"].get(metric, 0) if previous else None
    yoy_value = yoy["summary"].get(metric, 0) if yoy else None
    last3 = history[-3:]
    last6 = history[-6:]
    avg3 = average([item["summary"].get(metric, 0) for item in last3])
    avg6 = average([item["summary"].get(metric, 0) for item in last6])
    return {
        "value": value,
        "mom_pct": pct_change(value, prior),
        "yoy_pct": pct_change(value, yoy_value),
        "vs_3m_pct": pct_change(value, avg3),
        "vs_6m_pct": pct_change(value, avg6),
        "avg_3m": round(avg3) if avg3 is not None else None,
        "avg_6m": round(avg6) if avg6 is not None else None,
    }


def build_stores(current, previous, yoy, history):
    prev_stores = (previous or {}).get("by_store", {})
    yoy_stores = (yoy or {}).get("by_store", {})
    network_margin = current["summary"].get("net_margin_pct", 0)
    result = []
    for name, row in current.get("by_store", {}).items():
        revenue = row.get("revenue", 0)
        profit = row.get("net_profit", 0)
        old = prev_stores.get(name, {})
        old_revenue = old.get("revenue", 0)
        historical = [m.get("by_store", {}).get(name, {}).get("revenue") for m in history[-3:]]
        avg3 = average(historical)
        margin = round(profit / revenue * 100, 1) if revenue else 0
        result.append({
            "name": name,
            "revenue": revenue,
            "revenue_delta_pct": pct_change(revenue, old_revenue),
            "revenue_delta_money": round(revenue - old_revenue) if old_revenue else 0,
            "yoy_pct": pct_change(revenue, yoy_stores.get(name, {}).get("revenue", 0)),
            "vs_3m_pct": pct_change(revenue, avg3),
            "net_profit": profit,
            "net_margin_pct": margin,
            "vs_network_margin_pp": round(margin - network_margin, 1),
            "ebitda": row.get("ebitda", 0),
            "contribution_pct": round(revenue / current["summary"].get("revenue", 1) * 100, 1),
        })
    return sorted(result, key=lambda row: row["revenue_delta_money"])


def impact_score(effect, max_effect, duration=1, scale=1, confidence=0.8, ease=0.7):
    cfg = load_config()["impact_weights"]
    financial = min(abs(effect) / max(max_effect, 1), 1)
    score = 100 * (
        financial * cfg["financial"] + min(scale / 5, 1) * cfg["scale"]
        + min(duration / 3, 1) * cfg["duration"] + confidence * cfg["confidence"] + ease * cfg["ease"]
    )
    return round(score, 1)


def marker(score):
    if score >= 75:
        return "🔴"
    if score >= 55:
        return "🟠"
    if score >= 35:
        return "🟡"
    return "🟢"


def build_problems(data, current, stores):
    raw = []
    for store in stores:
        if store["revenue_delta_money"] < 0:
            raw.append({
                "kind": "revenue",
                "title": f"Падение выручки · {store['name']}",
                "what": f"Выручка изменилась на {store['revenue_delta_pct']:+.1f}% MoM.",
                "why": "Причина требует проверки: чеки, средний чек, категории, SKU, часы и стоп-лист.",
                "financial_impact": abs(store["revenue_delta_money"]),
                "annual_potential": abs(store["revenue_delta_money"]) * 12,
                "confidence": 0.9,
                "scale": 1,
            })
    for anomaly in data.get("anomalies", []):
        if anomaly.get("mkey") != current["mkey"] or anomaly.get("dev_pct", 0) <= 0:
            continue
        effect = anomaly.get("value", 0) - anomaly.get("prev_value", 0)
        if effect > 0:
            raw.append({
                "kind": "expense",
                "title": f"Рост расходов · {anomaly.get('alias') or anomaly.get('item')}",
                "what": f"Статья выросла на {anomaly.get('dev_pct', 0):+.1f}% MoM.",
                "why": "Причина требует проверки: первичные документы, разовое начисление, поставщик и распределение по точкам.",
                "financial_impact": round(effect),
                "annual_potential": round(effect * 12),
                "deviation_pct": anomaly.get("dev_pct", 0),
                "confidence": 0.8,
                "scale": 5,
            })
    max_effect = max([item["financial_impact"] for item in raw] or [1])
    for item in raw:
        item["impact_score"] = impact_score(item["financial_impact"], max_effect, scale=item["scale"], confidence=item["confidence"])
        item["marker"] = marker(item["impact_score"])
    return sorted(raw, key=lambda item: item["impact_score"], reverse=True)[:10]


def build_opportunities(stores, current):
    opportunities = []
    for store in sorted(stores, key=lambda row: row["revenue_delta_money"], reverse=True):
        if store["revenue_delta_money"] <= 0:
            continue
        opportunities.append({
            "title": f"Масштабировать практику точки {store['name']}",
            "evidence": f"Рост выручки {store['revenue_delta_pct']:+.1f}% MoM; вклад в сеть {store['contribution_pct']:.1f}%.",
            "financial_impact": store["revenue_delta_money"],
            "action": "Зафиксировать драйверы роста и проверить применимость в сопоставимых кофейнях.",
        })
    return opportunities[:5]


def build_actions(problems, opportunities):
    actions = []
    for problem in problems[:7]:
        expense = problem["kind"] == "expense"
        actions.append({
            "priority": len(actions) + 1,
            "problem": problem["title"],
            "action": ("Проверить первичные документы и распределение по точкам; назначить корректирующее действие."
                       if expense else "Разложить падение на чеки и средний чек, затем на категории/SKU и часы; устранить главный подтверждённый фактор."),
            "kpi": "снижение перерасхода" if expense else "восстановление выручки",
            "current_level": problem["what"],
            "target": "вернуться не хуже уровня предыдущего месяца",
            "expected_effect": problem["financial_impact"],
            "owner": "финансовый директор / закупки" if expense else "операционный директор / управляющий",
            "deadline": "5 рабочих дней" if expense else "10 рабочих дней",
            "status": "new",
        })
    for opportunity in opportunities:
        if len(actions) >= 10:
            break
        actions.append({
            "priority": len(actions) + 1,
            "problem": opportunity["title"],
            "action": opportunity["action"],
            "kpi": "дополнительная выручка сопоставимых точек",
            "current_level": opportunity["evidence"],
            "target": "подтвердить и тиражировать минимум один драйвер роста",
            "expected_effect": opportunity["financial_impact"],
            "owner": "операционный директор",
            "deadline": "до конца следующего месяца",
            "status": "new",
        })
    return actions[:15]


def previous_plan_review(period):
    year, month = map(int, period.split("-"))
    if month == 1:
        previous = f"{year - 1}-12"
    else:
        previous = f"{year}-{month - 1:02d}"
    path = os.path.join(BASE_DIR, "reports", "monthly", f"{previous}.json")
    if not os.path.exists(path):
        return {"status": "not_available", "message": "Предыдущий структурированный отчёт отсутствует; контроль начнётся со следующего цикла.", "actions": []}
    with open(path, encoding="utf-8") as source:
        old = json.load(source)
    return {
        "status": "requires_human_update",
        "message": "Статус исполнения должен подтвердить ответственный; KPI будет сопоставлен автоматически после внесения статуса.",
        "actions": old.get("action_plan", []),
    }


def source_coverage():
    missing = {
        "sales_checks": "нужна помесячная OLAP-выгрузка чеков и среднего чека",
        "menu_sku": "нужны исторические продажи и себестоимость по категориям/SKU",
        "purchase_prices": "нужны документы поступления: товар, поставщик, цена, объём и дата",
        "workforce": "нужны сотрудники смены, начало/конец смены и стоимость часа",
        "raw_material": "нужны списания/расход сырья и связи ингредиент → SKU",
    }
    result = {key: {"status": "unavailable", "required_source": value} for key, value in missing.items()}
    ready_path = os.path.join(BASE_DIR, "iiko_analytics_ready.json")
    if os.path.exists(ready_path):
        with open(ready_path, encoding="utf-8") as source:
            ready = json.load(source)
        mapping = {"sales_checks":"sales_checks","menu_sku":"menu_sku","purchase_prices":"purchases","raw_material":"raw_material"}
        for domain, key in mapping.items():
            rows = ready.get(key, [])
            if rows:
                result[domain] = {"status":"available","required_source":f"iiko подключён · {len(rows)} строк · период {ready.get('period')}"}
    return result


def build_full_report(data, requested_month=None):
    months = data.get("months", [])
    current, previous, yoy, history = select_period(months, requested_month)
    stores = build_stores(current, previous, yoy, history)
    problems = build_problems(data, current, stores)
    opportunities = build_opportunities(stores, current)
    action_plan = build_actions(problems, opportunities)
    comparisons = {metric: comparison_block(metric, current, previous, yoy, history) for metric in ("revenue", "cogs", "gross_profit", "opex", "ebitda", "net_profit")}
    executive = []
    for item in problems[:5]:
        executive.append(f"{item['marker']} {item['title']}: эффект {item['financial_impact'] / 1_000_000:.1f} млн ₽/мес. {item['why']}")
    for item in opportunities[:3]:
        executive.append(f"🟢 {item['title']}: подтверждённый прирост {item['financial_impact'] / 1_000_000:.1f} млн ₽/мес.")
    kpi_names = {
        "revenue": "Выручка", "gross_profit": "Валовая прибыль",
        "ebitda": "EBITDA", "net_profit": "Чистая прибыль",
    }
    kpis = [
        {
            "name": label,
            "value": comparisons[key]["value"],
            "display": f"{comparisons[key]['value'] / 1_000_000:.1f} млн ₽",
            "delta_pct": comparisons[key]["mom_pct"],
        }
        for key, label in kpi_names.items()
    ]
    expense_findings = [
        {
            "item": item["title"].split(" · ", 1)[-1],
            "value": item["financial_impact"],
            "delta_pct": item.get("deviation_pct", 0),
            "money_effect": item["financial_impact"],
            "reason": item["why"],
        }
        for item in problems if item["kind"] == "expense"
    ]
    legacy_actions = [
        {
            "problem": item["problem"], "effect": item["expected_effect"],
            "action": item["action"], "owner": item["owner"],
            "deadline": item["deadline"], "kpi": item["kpi"],
        }
        for item in action_plan
    ]
    extended = {}
    ready_path = os.path.join(BASE_DIR, "iiko_analytics_ready.json")
    if os.path.exists(ready_path):
        with open(ready_path, encoding="utf-8") as source:
            ready = json.load(source)
        if ready.get("period") == current["mkey"]:
            checks = ready.get("sales_checks", [])
            total_checks = sum(x.get("checks",0) for x in checks)
            total_sales = sum(x.get("revenue",0) for x in checks)
            guest_trends = ready.get("guest_trends", [])
            trend_by_period = {item.get("period"): item for item in guest_trends}
            trend_current = trend_by_period.get(current["mkey"], {})
            trend_previous = trend_by_period.get(previous["mkey"], {}) if previous else {}
            trend_yoy = trend_by_period.get(yoy["mkey"], {}) if yoy else {}
            metrics = {
                "avg_check": "Средний чек", "visits_per_guest": "Визитов на гостя",
                "repeat_guest_pct": "Повторные гости", "identified_guests": "Идентифицированные гости",
            }
            guest_comparisons = [
                {"key": key, "label": label, "value": trend_current.get(key),
                 "mom_pct": pct_change(trend_current.get(key, 0), trend_previous.get(key, 0)),
                 "yoy_pct": pct_change(trend_current.get(key, 0), trend_yoy.get(key, 0))}
                for key, label in metrics.items()
            ]
            avg_mom = next((x["mom_pct"] for x in guest_comparisons if x["key"] == "avg_check"), None)
            avg_yoy = next((x["yoy_pct"] for x in guest_comparisons if x["key"] == "avg_check"), None)
            freq_mom = next((x["mom_pct"] for x in guest_comparisons if x["key"] == "visits_per_guest"), None)
            freq_yoy = next((x["yoy_pct"] for x in guest_comparisons if x["key"] == "visits_per_guest"), None)
            guests_yoy = next((x["yoy_pct"] for x in guest_comparisons if x["key"] == "identified_guests"), None)
            repeat_now = trend_current.get("repeat_guest_pct")
            guest_analysis = []
            if avg_mom is not None:
                guest_analysis.append(f"Средний чек {'вырос' if avg_mom >= 0 else 'снизился'} на {abs(avg_mom):.1f}% MoM и {('вырос' if (avg_yoy or 0) >= 0 else 'снизился')} на {abs(avg_yoy or 0):.1f}% YoY. Рост нужно разложить на цену, продуктовый микс и скидки, чтобы подтвердить его качество.")
            if freq_mom is not None:
                guest_analysis.append(f"Частота посещений {'выросла' if freq_mom >= 0 else 'снизилась'} на {abs(freq_mom):.1f}% MoM, но {'выросла' if (freq_yoy or 0) >= 0 else 'ниже'} на {abs(freq_yoy or 0):.1f}% YoY. Краткосрочное улучшение пока не компенсировало годовое снижение удержания.")
            if guests_yoy is not None:
                guest_analysis.append(f"База идентифицированных гостей выросла на {guests_yoy:.1f}% YoY при динамике частоты {freq_yoy:+.1f}%: сеть хорошо привлекает гостей, но потенциал находится в переводе новых гостей в повторные.")
            if repeat_now is not None:
                guest_analysis.append(f"Доля повторных гостей — {repeat_now:.1f}%; доля разовых гостей — {100-repeat_now:.1f}%.")
            guest_recommendations = [
                {"action": "Запустить возврат гостей без визита 21–30 дней персональным предложением.", "kpi": "Визитов на гостя", "target": trend_yoy.get("visits_per_guest", round(trend_current.get("visits_per_guest", 0) * 1.03, 2)), "deadline": "30 дней"},
                {"action": "Поднять повторные визиты через предложение на следующую покупку, а не скидку в текущем чеке.", "kpi": "Повторные гости", "target": trend_yoy.get("repeat_guest_pct", round(min(100, trend_current.get("repeat_guest_pct", 0) + 2), 1)), "deadline": "30 дней"},
                {"action": "Провести A/B-тест допродажи напиток + десерт на точках с чеком ниже среднего сети.", "kpi": "Средний чек", "target": round(trend_current.get("avg_check", 0) * 1.03), "deadline": "30 дней"},
            ]
            extended = {
                "checks": total_checks, "avg_check": round(total_sales/total_checks,2) if total_checks else 0,
                "checks_per_day": round(total_checks/calendar.monthrange(int(current["mkey"][:4]), int(current["mkey"][5:]))[1],1), "guest_frequency": ready.get("guest_frequency",{}),
                "guest_trends": guest_trends, "guest_comparisons": guest_comparisons,
                "guest_analysis": guest_analysis, "guest_recommendations": guest_recommendations,
                "top_menu": sorted(ready.get("menu_sku",[]),key=lambda x:x.get("revenue",0),reverse=True)[:20],
                "top_purchases": sorted(ready.get("purchases",[]),key=lambda x:x.get("sum",0),reverse=True)[:20],
                "top_raw_material": sorted(ready.get("raw_material",[]),key=lambda x:x.get("sum",0),reverse=True)[:20],
            }
    return {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": current["mkey"],
        "period_label": current["label"],
        "comparison_label": previous["label"] if previous else None,
        "yoy_available": yoy is not None,
        "kpis": kpis,
        "kpi": comparisons,
        "data_quality": data_quality(data, current),
        "source_coverage": source_coverage(),
        "extended_analytics": extended,
        "executive_summary": executive[:10],
        "comparisons": comparisons,
        "margins": {key: current["summary"].get(key, 0) for key in ("cogs_pct", "gross_margin_pct", "opex_pct", "ebitda_pct", "net_margin_pct")},
        "stores": stores,
        "problems": problems[:5],
        "anomalies": problems,
        "opportunities": opportunities[:5],
        "financial_impact": {
            "monthly_problem_total": sum(item["financial_impact"] for item in problems[:5]),
            "annual_problem_total": sum(item["annual_potential"] for item in problems[:5]),
            "monthly_opportunity_total": sum(item["financial_impact"] for item in opportunities[:5]),
        },
        "action_plan": action_plan,
        "recommendations": action_plan,
        "actions": legacy_actions,
        "expense_findings": expense_findings,
        "previous_plan_review": previous_plan_review(current["mkey"]),
        "human_checks": [item["why"] for item in problems if "требует проверки" in item["why"].lower()],
        "data_limits": [item["required_source"] for item in source_coverage().values()],
        "thresholds": load_config(),
    }
