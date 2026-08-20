#!/usr/bin/env python3
"""Daily purchase-price and raw-material anomaly monitor.

Inputs are optional adapters. Until iiko exports are connected the output stays
explicitly in DATA QUALITY WARNING state and never invents operational alerts.
"""

import json
import os
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PURCHASE_PATH = os.path.join(BASE_DIR, "purchase_price_data.json")
RAW_PATH = os.path.join(BASE_DIR, "raw_material_usage_data.json")
OUT_PATH = os.path.join(BASE_DIR, "operational_alerts.json")
MESSAGE_PATH = os.path.join(BASE_DIR, "telegram_operational_alerts.txt")
HISTORY_PATH = os.path.join(BASE_DIR, "reports", "operational", "alert_history.json")


def load(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as source:
        value = json.load(source)
    return value if isinstance(value, list) else value.get("rows", [])


def purchase_alerts(rows):
    alerts = []
    for row in rows:
        old = float(row.get("previous_price") or 0)
        new = float(row.get("current_price") or 0)
        volume = float(row.get("monthly_volume") or 0)
        if not old or not new:
            continue
        delta = new - old
        change_pct = delta / old * 100
        monthly_effect = delta * volume
        if change_pct <= 0:
            level = "green"
        elif change_pct < 3:
            level = "yellow"
        elif change_pct <= 7:
            level = "orange"
        else:
            level = "red"
        if monthly_effect >= 100000:
            level = "red"
        if level == "green":
            continue
        alerts.append({
            "type": "purchase_price", "severity": level,
            "item": row.get("item"), "supplier": row.get("supplier"),
            "previous_price": old, "current_price": new,
            "change_rub": round(delta, 2), "change_pct": round(change_pct, 1),
            "avg_30d": row.get("avg_30d"), "avg_90d": row.get("avg_90d"),
            "monthly_volume": volume, "monthly_effect": round(monthly_effect),
            "annual_effect": round(monthly_effect * 12),
            "affected_sku": row.get("affected_sku", []),
            "check": "Проверить поставщика, договор, единицу измерения и цены альтернативных поставщиков.",
            "action": "Запросить обоснование изменения и рассчитать замену/переговоры.",
        })
    return sorted(alerts, key=lambda item: item["monthly_effect"], reverse=True)[:10]


def raw_material_alerts(rows):
    alerts = []
    for row in rows:
        actual = float(row.get("actual_usage") or 0)
        expected = float(row.get("expected_usage") or 0)
        unit_cost = float(row.get("unit_cost") or 0)
        if expected <= 0:
            continue
        deviation_pct = (actual - expected) / expected * 100
        if deviation_pct <= 10:
            continue
        excess = actual - expected
        alerts.append({
            "type": "raw_material", "severity": "red" if deviation_pct > 20 else "orange",
            "item": row.get("item"), "store": row.get("store"),
            "actual_usage": actual, "expected_usage": expected,
            "deviation_pct": round(deviation_pct, 1),
            "monthly_effect": round(excess * unit_cost),
            "linked_sku": row.get("linked_sku", []),
            "check": "Проверить списания, порции, рецептуры, остатки, перемещения и ошибки учёта.",
            "action": "Провести инвентаризацию и сверку фактического расхода с продажами связанных SKU.",
        })
    return sorted(alerts, key=lambda item: item["monthly_effect"], reverse=True)[:10]


def main():
    purchases = load(PURCHASE_PATH)
    materials = load(RAW_PATH)
    alerts = purchase_alerts(purchases) + raw_material_alerts(materials)
    warnings = []
    if not purchases:
        warnings.append("Нет purchase_price_data.json: мониторинг закупочных цен не выполняется.")
    if not materials:
        warnings.append("Нет raw_material_usage_data.json: мониторинг списаний и сырья не выполняется.")
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "data_quality_warning" if warnings else "ok",
        "warnings": warnings,
        "alerts": sorted(alerts, key=lambda item: item.get("monthly_effect", 0), reverse=True),
    }
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    history = []
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH, encoding="utf-8") as source:
            history = json.load(source)
    history.append(output)
    history = history[-365:]
    for path, value in ((OUT_PATH, output), (HISTORY_PATH, history)):
        with open(path, "w", encoding="utf-8") as target:
            json.dump(value, target, ensure_ascii=False, indent=2)
    lines = ["🚨 Garden · оперативные сигналы iiko", ""]
    for alert in output["alerts"][:10]:
        lines.append(f"• {alert.get('item')}: эффект {alert.get('monthly_effect', 0):,.0f} ₽/мес · {alert.get('action')}")
    if not output["alerts"]:
        lines.append("NO_ALERTS")
    with open(MESSAGE_PATH, "w", encoding="utf-8") as target:
        target.write("\n".join(lines) + "\n")
    print(f"Operational monitor: {len(alerts)} alerts, {len(warnings)} warnings")


if __name__ == "__main__":
    main()
