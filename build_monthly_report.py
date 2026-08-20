#!/usr/bin/env python3
"""Build a deterministic monthly Garden management report from pnl_data.json."""

import argparse
import html
import json
import os
from datetime import date, datetime, timezone

from monthly_analytics import build_full_report

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PNL_PATH = os.path.join(BASE_DIR, "pnl_data.json")
REPORT_JSON = os.path.join(BASE_DIR, "monthly_report.json")
REPORT_HTML = os.path.join(BASE_DIR, "monthly_report.html")
TELEGRAM_TEXT = os.path.join(BASE_DIR, "telegram_monthly_report.txt")
PUBLIC_URL = "https://s4ir1111-cloud.github.io/coffee-dashboard/monthly_report.html"


def pct_change(current, previous):
    return round((current - previous) / abs(previous) * 100, 1) if previous else None


def money(value):
    sign = "−" if value < 0 else ""
    return f"{sign}{abs(value) / 1_000_000:.1f} млн ₽"


def percent(value):
    return "н/д" if value is None else f"{value:+.1f}%"


def previous_month_key(today):
    year, month = today.year, today.month - 1
    if month == 0:
        year, month = year - 1, 12
    return f"{year:04d}-{month:02d}"


def select_period(months, requested=None):
    by_key = {item["mkey"]: item for item in months}
    target_key = requested or previous_month_key(date.today())
    current = by_key.get(target_key)
    if current is None:
        completed = [m for m in months if m["mkey"] < date.today().strftime("%Y-%m")]
        if not completed:
            raise ValueError("Нет данных за завершённый месяц")
        current = completed[-1]
        target_key = current["mkey"]
    index = months.index(current)
    previous = months[index - 1] if index > 0 else None
    yoy = by_key.get(f"{int(target_key[:4]) - 1:04d}-{target_key[5:]}")
    return current, previous, yoy


def metric_card(name, current, previous, suffix=""):
    delta = pct_change(current, previous) if previous is not None else None
    return {"name": name, "value": current, "display": money(current) + suffix, "delta_pct": delta}


def store_rows(current, previous):
    prev_stores = (previous or {}).get("by_store", {})
    result = []
    for name, values in current.get("by_store", {}).items():
        old = prev_stores.get(name, {})
        revenue = values.get("revenue", 0)
        old_revenue = old.get("revenue", 0)
        profit = values.get("net_profit", 0)
        delta_money = revenue - old_revenue if old_revenue else 0
        result.append({
            "name": name,
            "revenue": revenue,
            "revenue_delta_pct": pct_change(revenue, old_revenue),
            "revenue_delta_money": round(delta_money),
            "net_profit": profit,
            "net_margin_pct": round(profit / revenue * 100, 1) if revenue else 0,
            "ebitda": values.get("ebitda", 0),
        })
    return sorted(result, key=lambda row: row["revenue_delta_money"])


def expense_findings(data, month_key):
    findings = []
    for anomaly in data.get("anomalies", []):
        if anomaly.get("mkey") != month_key or anomaly.get("dev_pct", 0) <= 0:
            continue
        effect = anomaly.get("value", 0) - anomaly.get("prev_value", 0)
        if effect <= 0:
            continue
        findings.append({
            "item": anomaly.get("alias") or anomaly.get("item"),
            "value": anomaly.get("value", 0),
            "delta_pct": anomaly.get("dev_pct"),
            "money_effect": round(effect),
            "reason": "Причина требует проверки",
        })
    findings.sort(key=lambda item: item["money_effect"], reverse=True)
    return findings[:8]


def build_actions(stores, expenses):
    actions = []
    for row in stores[:3]:
        if row["revenue_delta_money"] >= 0:
            continue
        actions.append({
            "priority": len(actions) + 1,
            "problem": f"Снижение выручки: {row['name']}",
            "effect": abs(row["revenue_delta_money"]),
            "action": "Разложить изменение на трафик и средний чек; проверить график работы, стоп-лист и локальные отзывы.",
            "owner": "операционный директор / управляющий",
            "deadline": "7 рабочих дней",
            "kpi": "остановить падение выручки и вернуть показатель к уровню прошлого месяца",
        })
    for item in expenses[:3]:
        actions.append({
            "priority": len(actions) + 1,
            "problem": f"Рост расходов: {item['item']}",
            "effect": item["money_effect"],
            "action": "Проверить первичные документы, разовые начисления, поставщика и корректность распределения по точкам.",
            "owner": "финансовый директор / закупки",
            "deadline": "5 рабочих дней",
            "kpi": "подтвердить причину и устранить необоснованный перерасход",
        })
    return sorted(actions, key=lambda item: item["effect"], reverse=True)[:6]


def build_report(data, requested_month=None):
    months = data.get("months", [])
    current, previous, yoy = select_period(months, requested_month)
    cur = current["summary"]
    prev = previous["summary"] if previous else {}
    stores = store_rows(current, previous)
    expenses = expense_findings(data, current["mkey"])
    actions = build_actions(stores, expenses)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": current["mkey"],
        "period_label": current["label"],
        "comparison_label": previous["label"] if previous else None,
        "yoy_available": yoy is not None,
        "data_limits": [
            "Исторические чеки и средний чек отсутствуют в текущей P&L-выгрузке.",
            "Причины отклонений отмечаются как гипотезы до проверки первичных документов.",
        ],
        "kpis": [
            metric_card("Выручка", cur.get("revenue", 0), prev.get("revenue")),
            metric_card("Валовая прибыль", cur.get("gross_profit", 0), prev.get("gross_profit")),
            metric_card("EBITDA", cur.get("ebitda", 0), prev.get("ebitda")),
            metric_card("Чистая прибыль", cur.get("net_profit", 0), prev.get("net_profit")),
        ],
        "margins": {
            "cogs_pct": cur.get("cogs_pct", 0),
            "gross_margin_pct": cur.get("gross_margin_pct", 0),
            "opex_pct": cur.get("opex_pct", 0),
            "ebitda_pct": cur.get("ebitda_pct", 0),
            "net_margin_pct": cur.get("net_margin_pct", 0),
        },
        "stores": stores,
        "expense_findings": expenses,
        "actions": actions,
    }


def esc(value):
    return html.escape(str(value))


def render_html(report):
    cards = "".join(
        f'<div class="card"><span>{esc(k["name"])}</span><strong>{esc(k["display"])}</strong>'
        f'<em class="{("good" if (k["delta_pct"] or 0) >= 0 else "bad")}">{percent(k["delta_pct"])} к прошлому месяцу</em></div>'
        for k in report["kpis"]
    )
    stores = "".join(
        f'<tr><td>{esc(s["name"])}</td><td>{money(s["revenue"])}</td><td class="{("good" if (s["revenue_delta_pct"] or 0) >= 0 else "bad")}">{percent(s["revenue_delta_pct"])}</td><td>{money(s["net_profit"])}</td><td>{s["net_margin_pct"]:.1f}%</td></tr>'
        for s in report["stores"]
    )
    expenses = "".join(
        f'<tr><td>{esc(x["item"])}</td><td>{money(x["value"])}</td><td class="bad">+{x["delta_pct"]:.1f}%</td><td class="bad">{money(x["money_effect"])}</td><td>{esc(x["reason"])}</td></tr>'
        for x in report["expense_findings"]
    ) or '<tr><td colspan="5">Значимых положительных отклонений расходов не обнаружено.</td></tr>'
    actions = "".join(
        f'<tr><td>{a["priority"]}</td><td>{esc(a["problem"])}</td><td>{esc(a["action"])}</td><td>{esc(a["kpi"])}</td><td>{esc(a["current_level"])}</td><td>{esc(a["target"])}</td><td>{money(a["expected_effect"])}</td><td>{esc(a["owner"])}</td><td>{esc(a["deadline"])}</td></tr>'
        for a in report.get("action_plan", [])
    )
    limits = "".join(f"<li>{esc(x)}</li>" for x in report["data_limits"])
    margins = report["margins"]
    executive = "".join(f"<li>{esc(item)}</li>" for item in report.get("executive_summary", []))
    comparisons = "".join(
        f'<tr><td>{esc(name)}</td><td>{money(row["value"])}</td><td>{percent(row["mom_pct"])}</td><td>{percent(row["yoy_pct"])}</td><td>{percent(row["vs_3m_pct"])}</td><td>{percent(row["vs_6m_pct"])}</td></tr>'
        for name, row in report.get("comparisons", {}).items()
    )
    problems = "".join(
        f'<article class="finding"><h3>{esc(item["marker"])} {esc(item["title"])}</h3><p>{esc(item["what"])}</p><p>{esc(item["why"])}</p><b>Эффект: {money(item["financial_impact"])} / мес · Impact Score {item["impact_score"]}</b></article>'
        for item in report.get("problems", [])
    )
    opportunities = "".join(
        f'<article class="finding"><h3>{esc(item["title"])}</h3><p>{esc(item["evidence"])}</p><p>{esc(item["action"])}</p><b>Потенциал: {money(item["financial_impact"])} / мес</b></article>'
        for item in report.get("opportunities", [])
    )
    coverage = "".join(
        f'<tr><td>{esc(name)}</td><td>{esc(item["status"])}</td><td>{esc(item["required_source"])}</td></tr>'
        for name, item in report.get("source_coverage", {}).items()
    )
    unavailable_sections = "".join(
        f'<article class="finding"><h3>{esc(name)}</h3><p class="{("good" if item["status"]=="available" else "bad")}">{esc(item["status"])}</p><p>{esc(item["required_source"])}</p></article>'
        for name, item in report.get("source_coverage", {}).items()
    )
    ext = report.get("extended_analytics", {})
    ext_summary = f'<div class="margins"><div class="pill">Чеки <b>{ext.get("checks",0):,.0f}</b></div><div class="pill">Средний чек <b>{ext.get("avg_check",0):,.0f} ₽</b></div></div>' if ext else ''
    dq = report.get("data_quality", {})
    dq_items = "".join(f"<li>{esc(item)}</li>" for item in dq.get("warnings", [])) or "<li>Контрольные проверки пройдены.</li>"
    previous_review = report.get("previous_plan_review", {})
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Garden · Управленческий отчёт</title>
<style>:root{{--bg:#09110f;--panel:#111d19;--line:#263a32;--text:#f3f7f5;--muted:#9fb0a8;--accent:#d9b36c;--good:#62d69b;--bad:#ff827a}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 Inter,Arial,sans-serif}}main{{max-width:1320px;margin:auto;padding:34px 22px 60px}}header{{display:flex;justify-content:space-between;gap:20px;align-items:end;border-bottom:1px solid var(--line);padding-bottom:20px}}h1{{margin:0;font-size:31px}}h2{{margin:34px 0 12px;font-size:20px}}h3{{margin:0 0 8px}}.eyebrow,.note,span,em{{color:var(--muted)}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:22px}}.card,.finding{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:17px}}.card strong{{display:block;font-size:25px;margin:6px 0}}.findings{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}}em{{font-style:normal;font-size:13px}}.good{{color:var(--good)!important}}.bad{{color:var(--bad)!important}}table{{width:100%;border-collapse:collapse;background:var(--panel);border-radius:14px;overflow:hidden}}th,td{{padding:11px 12px;border-bottom:1px solid var(--line);text-align:right;vertical-align:top}}th:first-child,td:first-child{{text-align:left}}th{{color:var(--muted);font-size:12px;text-transform:uppercase}}.margins{{display:flex;gap:12px;flex-wrap:wrap}}.pill{{background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:9px 13px}}.warn{{border-left:3px solid var(--accent);background:var(--panel);padding:13px 18px;margin-top:28px}}footer{{color:var(--muted);margin-top:32px}}@media(max-width:800px){{.grid,.findings{{grid-template-columns:1fr 1fr}}header{{display:block}}.scroll{{overflow:auto}}table{{min-width:760px}}}}@media(max-width:480px){{.grid,.findings{{grid-template-columns:1fr}}}}</style></head>
<body><main><header><div><div class="eyebrow">GARDEN COFFEE · АВТОМАТИЧЕСКИЙ ОТЧЁТ</div><h1>{esc(report["period_label"])}</h1></div><div class="note">Сравнение: {esc(report["comparison_label"] or "нет данных")}</div></header>
<div class="warn"><b>Data Quality · {esc(dq.get('status','unknown'))}</b><ul>{dq_items}</ul></div><h2>Executive Summary</h2><ol>{executive}</ol>
<section class="grid">{cards}</section><h2>Сравнения MoM / YoY / 3 / 6 месяцев</h2><div class="scroll"><table><thead><tr><th>KPI</th><th>Значение</th><th>MoM</th><th>YoY</th><th>к среднему 3 мес.</th><th>к среднему 6 мес.</th></tr></thead><tbody>{comparisons}</tbody></table></div><h2>Маржинальность</h2><div class="margins"><div class="pill">Себестоимость <b>{margins['cogs_pct']:.1f}%</b></div><div class="pill">Валовая маржа <b>{margins['gross_margin_pct']:.1f}%</b></div><div class="pill">OPEX <b>{margins['opex_pct']:.1f}%</b></div><div class="pill">EBITDA <b>{margins['ebitda_pct']:.1f}%</b></div><div class="pill">Чистая маржа <b>{margins['net_margin_pct']:.1f}%</b></div></div>
<h2>TOP-5 проблем</h2><div class="findings">{problems}</div><h2>TOP-5 точек роста</h2><div class="findings">{opportunities}</div>
<h2>Кофейни · от максимального падения к росту</h2><div class="scroll"><table><thead><tr><th>Точка</th><th>Выручка</th><th>MoM</th><th>Чистая прибыль</th><th>Маржа</th></tr></thead><tbody>{stores}</tbody></table></div>
<h2>Главные отклонения расходов</h2><div class="scroll"><table><thead><tr><th>Статья</th><th>Сумма</th><th>Изменение</th><th>Денежный эффект</th><th>Статус причины</th></tr></thead><tbody>{expenses}</tbody></table></div>
<h2>План действий</h2><div class="scroll"><table><thead><tr><th>№</th><th>Проблема</th><th>Действие</th><th>KPI</th><th>Текущий уровень</th><th>Цель</th><th>Эффект</th><th>Ответственный</th><th>Срок</th></tr></thead><tbody>{actions}</tbody></table></div>
<div class="warn"><b>Контроль предыдущего плана · {esc(previous_review.get('status','unknown'))}</b><p>{esc(previous_review.get('message',''))}</p></div>
<h2>Покрытие источников по ТЗ</h2><div class="scroll"><table><thead><tr><th>Контур</th><th>Статус</th><th>Что требуется</th></tr></thead><tbody>{coverage}</tbody></table></div>
<h2>Продажи, меню, закупки, персонал и сырьё</h2>{ext_summary}<div class="findings">{unavailable_sections}</div>
<div class="warn"><b>Ограничения данных</b><ul>{limits}</ul></div><footer>Сформировано {esc(report['generated_at'][:10])} · источник: iiko P&L / OLAP</footer></main></body></html>'''


def telegram_message(report):
    kpis = {item["name"]: item for item in report["kpis"]}
    bad_stores = [s for s in report["stores"] if s["revenue_delta_money"] < 0][:3]
    lines = [
        f"☕ Garden Coffee · итог за {report['period_label']}",
        "",
        f"Выручка: {kpis['Выручка']['display']} ({percent(kpis['Выручка']['delta_pct'])} MoM)",
        f"EBITDA: {kpis['EBITDA']['display']} · маржа {report['margins']['ebitda_pct']:.1f}%",
        f"Чистая прибыль: {kpis['Чистая прибыль']['display']}",
    ]
    if bad_stores:
        lines += ["", "🔻 Наибольшее снижение выручки:"]
        lines += [f"• {s['name']}: {money(s['revenue_delta_money'])} ({percent(s['revenue_delta_pct'])})" for s in bad_stores]
    if report["expense_findings"]:
        lines += ["", "⚠️ Рост расходов:"]
        lines += [f"• {x['item']}: +{money(x['money_effect'])}" for x in report["expense_findings"][:3]]
    unavailable = [name for name, item in report.get("source_coverage", {}).items() if item.get("status") != "available"]
    if unavailable:
        lines += ["", "⚠️ Не подключены источники: " + ", ".join(unavailable)]
    lines += ["", f"План действий и полный отчёт: {PUBLIC_URL}"]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", help="Месяц YYYY-MM; по умолчанию предыдущий завершённый")
    args = parser.parse_args()
    with open(PNL_PATH, encoding="utf-8") as source:
        report = build_full_report(json.load(source), args.month)
    with open(REPORT_JSON, "w", encoding="utf-8") as target:
        json.dump(report, target, ensure_ascii=False, indent=2)
    with open(REPORT_HTML, "w", encoding="utf-8") as target:
        rendered = render_html(report)
        target.write(rendered)
    with open(TELEGRAM_TEXT, "w", encoding="utf-8") as target:
        target.write(telegram_message(report) + "\n")
    history_dir = os.path.join(BASE_DIR, "reports", "monthly")
    os.makedirs(history_dir, exist_ok=True)
    with open(os.path.join(history_dir, f"{report['period']}.json"), "w", encoding="utf-8") as target:
        json.dump(report, target, ensure_ascii=False, indent=2)
    with open(os.path.join(history_dir, f"{report['period']}.html"), "w", encoding="utf-8") as target:
        target.write(rendered)
    print(f"Built monthly report for {report['period']}: {REPORT_HTML}")


if __name__ == "__main__":
    main()
