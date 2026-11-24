#!/usr/bin/env python3
"""Verified analytics access layer for Garden Coffee.

The module is intentionally deterministic: it never invents missing values and
every returned metric contains its formula, operands, source file and source
timestamp.  It is suitable both for direct CLI use and for importing from an
assistant session when an ad-hoc calculation is requested.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent
HISTORY_DIR = ROOT / "analytics" / "history" / "daily"

STORE_ALIASES = {
    "парус": "Паруса",
    "преображенский": "Прео",
    "софи лэнд": "Софилэнд",
    "софи-лэнд": "Софилэнд",
    "сургут": "Гарден Кофе Сургут",
    "тобольск": "Гарден Кофе Тобольск",
    "world class": "Ворлд Класс",
}

CURRENT_METRICS = {
    "revenue": ("Выручка", "₽"),
    "orders": ("Заказы", "заказов"),
    "avg_check": ("Средний чек", "₽"),
    "discount_pct": ("Скидки", "%"),
}

MONTHLY_METRICS = {
    "revenue": ("Выручка", "₽"),
    "orders": ("Заказы", "заказов"),
    "avg_check": ("Средний чек", "₽"),
}

PNL_METRICS = {
    "revenue": ("Выручка", "₽"),
    "cogs": ("Себестоимость", "₽"),
    "gross_profit": ("Валовая прибыль", "₽"),
    "opex": ("Операционные расходы", "₽"),
    "ebitda": ("EBITDA", "₽"),
    "net_profit": ("Чистая прибыль", "₽"),
    "cogs_pct": ("Себестоимость к выручке", "%"),
    "gross_margin_pct": ("Валовая маржа", "%"),
    "opex_pct": ("OPEX к выручке", "%"),
    "ebitda_pct": ("Маржа EBITDA", "%"),
    "net_margin_pct": ("Чистая маржа", "%"),
}

PLAN_METRICS = {
    "mtd_revenue": ("Факт с начала месяца", "₽"),
    "plan_month": ("План месяца", "₽"),
    "expected_to_date": ("План на текущую дату", "₽"),
    "pct_of_plan": ("Выполнение плана месяца", "%"),
    "pct_vs_expected": ("Выполнение плана на текущую дату", "%"),
    "avg_check_plan": ("План среднего чека", "₽"),
    "mtd_avg_check": ("Средний чек с начала месяца", "₽"),
}


class AnalyticsError(RuntimeError):
    pass


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise AnalyticsError(f"Источник не найден: {path.name}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalyticsError(f"Источник повреждён: {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise AnalyticsError(f"Источник имеет неверный формат: {path.name}")
    return data


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text + "T00:00:00+00:00")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _round(value: float, digits: int = 1) -> int | float:
    rounded = round(value, digits)
    return int(rounded) if rounded == int(rounded) else rounded


def canonical_store(value: str | None) -> str | None:
    if value is None:
        return None
    original = re.sub(r"^\d+\s*[.)-]\s*", "", str(value).strip())
    key = re.sub(r"\s+", " ", original.lower().replace("ё", "е"))
    return STORE_ALIASES.get(key, original)


class AnalyticsService:
    def __init__(self, root: Path | str = ROOT):
        self.root = Path(root)
        self.dashboard_path = self.root / "dashboard_data_view.json"
        self.finance_path = self.root / "finance_data.json"
        self.pnl_path = self.root / "pnl_data.json"
        self.dashboard = _load(self.dashboard_path)
        self.finance = _load(self.finance_path)
        self.pnl = _load(self.pnl_path)

    def _source(self, path: Path, data: dict[str, Any]) -> dict[str, Any]:
        generated_at = data.get("generated_at") or data.get("date")
        parsed = _parse_time(generated_at)
        age_hours = None
        if parsed:
            age_hours = max(0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 3600)
        return {
            "file": path.name,
            "generated_at": generated_at,
            "sha256": _sha256(path),
            "age_hours": _round(age_hours, 1) if age_hours is not None else None,
        }

    @staticmethod
    def _result(
        *, metric: str, label: str, value: Any, unit: str, period: str,
        scope: str, formula: str, operands: dict[str, Any], source: dict[str, Any],
        checks: Iterable[dict[str, Any]] = (), warnings: Iterable[str] = (),
    ) -> dict[str, Any]:
        check_list = list(checks)
        warning_list = list(warnings)
        verified = all(item.get("ok", False) for item in check_list) and value is not None
        return {
            "metric": metric,
            "label": label,
            "value": value,
            "unit": unit,
            "period": period,
            "scope": scope,
            "calculation": {"formula": formula, "operands": operands},
            "verification": {
                "status": "verified" if verified and not warning_list else "warning",
                "checks": check_list,
                "warnings": warning_list,
            },
            "source": source,
        }

    def stores(self) -> list[str]:
        names = {str(p.get("name")) for p in self.dashboard.get("points", []) if p.get("name")}
        names.update(self.finance.get("by_point", {}).keys())
        return sorted(names)

    def _find_store(self, store: str, candidates: Iterable[dict[str, Any]]) -> dict[str, Any]:
        wanted = canonical_store(store)
        for row in candidates:
            if canonical_store(row.get("name")) == wanted:
                return row
        raise AnalyticsError(f"Кофейня не найдена: {store}")

    def current(self, metric: str, store: str | None = None) -> dict[str, Any]:
        if metric not in CURRENT_METRICS:
            raise AnalyticsError(f"Недоступная текущая метрика: {metric}")
        label, unit = CURRENT_METRICS[metric]
        scope = canonical_store(store) or "Все точки"
        source = self._source(self.dashboard_path, self.dashboard)
        period = str(self.dashboard.get("date"))
        summary = self.dashboard.get("summary", {})
        if store:
            row = self._find_store(store, self.dashboard.get("points", []))
            revenue = float(row.get("revenue") or 0)
            orders = float(row.get("orders") or 0)
            if metric == "avg_check":
                value = round(revenue / orders) if orders else None
                formula = "revenue / orders"
                operands = {"revenue": revenue, "orders": orders}
                checks = [{"name": "orders_nonzero", "ok": orders > 0}]
            elif metric in ("revenue", "orders"):
                value = row.get(metric)
                formula = f"SUM({metric}) for store and date"
                operands = {metric: value}
                checks = [{"name": "store_present", "ok": True}]
            else:
                raise AnalyticsError("Скидка по отдельной кофейне отсутствует в текущем источнике")
        else:
            revenue = float(summary.get("revenue") or 0)
            orders = float(summary.get("orders") or 0)
            if metric == "avg_check":
                value = round(revenue / orders) if orders else None
                formula = "revenue / orders"
                operands = {"revenue": revenue, "orders": orders}
                checks = [{"name": "orders_nonzero", "ok": orders > 0}]
            else:
                value = summary.get(metric)
                formula = f"SUM({metric}) across stores for date" if metric != "discount_pct" else "discount_sum / gross_sales * 100"
                operands = {metric: value}
                checks = [{"name": "nonempty_snapshot", "ok": bool(self.dashboard.get("points"))}]
        return self._result(metric=metric, label=label, value=value, unit=unit, period=period,
                            scope=scope, formula=formula, operands=operands, source=source,
                            checks=checks)

    def daily(self, day: str, metric: str, store: str | None = None) -> dict[str, Any]:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise AnalyticsError("Дата должна быть в формате YYYY-MM-DD")
        if day == self.dashboard.get("date"):
            return self.current(metric, store)
        path = self.root / "analytics" / "history" / "daily" / f"{day}.json"
        snapshot = _load(path)
        if metric not in ("revenue", "orders", "avg_check", "discount_pct"):
            raise AnalyticsError(f"Недоступная дневная метрика: {metric}")
        label, unit = CURRENT_METRICS[metric]
        scope = canonical_store(store) or "Все точки"
        summary = snapshot.get("summary", {})
        if store:
            row = self._find_store(store, snapshot.get("points", []))
            if metric == "discount_pct":
                raise AnalyticsError("Скидка по отдельной кофейне отсутствует в дневном снимке")
            revenue, orders = float(row.get("revenue") or 0), float(row.get("orders") or 0)
            value = round(revenue / orders) if metric == "avg_check" and orders else row.get(metric)
        else:
            revenue, orders = float(summary.get("revenue") or 0), float(summary.get("orders") or 0)
            value = round(revenue / orders) if metric == "avg_check" and orders else summary.get(metric)
        formula = "revenue / orders" if metric == "avg_check" else f"SUM({metric}) for date and scope"
        operands = {"revenue": revenue, "orders": orders} if metric == "avg_check" else {metric: value}
        source = snapshot.get("source", {}) | {"archive_file": str(path.relative_to(self.root))}
        return self._result(metric=metric, label=label, value=value, unit=unit, period=day,
                            scope=scope, formula=formula, operands=operands, source=source,
                            checks=[{"name": "archived_snapshot_present", "ok": True},
                                    {"name": "orders_nonzero", "ok": orders > 0}])

    def plan(self, metric: str, store: str | None = None) -> dict[str, Any]:
        if metric not in PLAN_METRICS:
            raise AnalyticsError(f"Недоступная плановая метрика: {metric}")
        label, unit = PLAN_METRICS[metric]
        scope = canonical_store(store) or "Все точки"
        plan = self.dashboard.get("plan", {})
        row = self._find_store(store, plan.get("points", [])) if store else plan.get("summary", {})
        value = row.get(metric)
        if value is None or (metric in ("plan_month", "avg_check_plan") and not value):
            raise AnalyticsError(f"Плановая метрика {metric} отсутствует для {scope}")
        formula = f"published plan metric: {metric}"
        operands = {metric: value}
        checks = [{"name": "plan_present", "ok": bool(row.get("plan_month"))}]
        if metric == "pct_of_plan":
            formula = "mtd_revenue / plan_month * 100"
            operands = {"mtd_revenue": row.get("mtd_revenue"), "plan_month": row.get("plan_month")}
            expected = _round(row.get("mtd_revenue", 0) / row.get("plan_month", 1) * 100, 1)
            checks.append({"name": "percentage_recalculated", "ok": math.isclose(value, expected, abs_tol=0.1),
                           "recalculated": expected})
        elif metric == "pct_vs_expected":
            formula = "mtd_revenue / expected_to_date * 100"
            operands = {"mtd_revenue": row.get("mtd_revenue"), "expected_to_date": row.get("expected_to_date")}
            expected = _round(row.get("mtd_revenue", 0) / row.get("expected_to_date", 1) * 100, 1)
            checks.append({"name": "percentage_recalculated", "ok": math.isclose(value, expected, abs_tol=0.1),
                           "recalculated": expected})
        return self._result(metric=metric, label=label, value=value, unit=unit,
                            period=str(self.dashboard.get("date"))[:7], scope=scope,
                            formula=formula, operands=operands,
                            source=self._source(self.dashboard_path, self.dashboard),
                            checks=checks)

    def monthly(self, month: str, metric: str, store: str | None = None) -> dict[str, Any]:
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            raise AnalyticsError("Месяц должен быть в формате YYYY-MM")
        if metric not in MONTHLY_METRICS:
            raise AnalyticsError(f"Недоступная метрика продаж: {metric}")
        label, unit = MONTHLY_METRICS[metric]
        scope = canonical_store(store) or "Все точки"
        source = self._source(self.finance_path, self.finance)
        warnings: list[str] = []
        current_month = self.finance.get("current_month")
        if store:
            point = self.finance.get("by_point", {}).get(scope)
            if not point:
                raise AnalyticsError(f"Кофейня не найдена в истории продаж: {scope}")
            revenue = point.get("monthly", {}).get(month)
            orders = point.get("monthly_orders", {}).get(month)
        else:
            row = next((x for x in self.finance.get("monthly_totals", []) if x.get("month") == month), None)
            if not row:
                raise AnalyticsError(f"Нет данных продаж за {month}")
            revenue, orders = row.get("revenue"), row.get("orders")
        if revenue is None or orders is None:
            raise AnalyticsError(f"Нет данных за {month} для {scope}")
        if metric == "revenue":
            value, formula, operands = revenue, "SUM(daily revenue)", {"revenue": revenue}
        elif metric == "orders":
            value, formula, operands = orders, "SUM(unique orders)", {"orders": orders}
        else:
            value = round(revenue / orders) if orders else None
            formula, operands = "revenue / orders", {"revenue": revenue, "orders": orders}
        if month == current_month:
            warnings.append(f"{month} ещё не завершён; значение накопительное на дату источника")
        checks = [
            {"name": "period_present", "ok": revenue is not None and orders is not None},
            {"name": "orders_nonzero", "ok": orders > 0},
        ]
        return self._result(metric=metric, label=label, value=value, unit=unit, period=month,
                            scope=scope, formula=formula, operands=operands, source=source,
                            checks=checks, warnings=warnings)

    def pnl_metric(self, month: str, metric: str, store: str | None = None) -> dict[str, Any]:
        if metric not in PNL_METRICS:
            raise AnalyticsError(f"Недоступная P&L-метрика: {metric}")
        label, unit = PNL_METRICS[metric]
        period = next((x for x in self.pnl.get("months", []) if x.get("mkey") == month), None)
        if not period:
            raise AnalyticsError(f"Нет P&L за {month}")
        scope = canonical_store(store) or "Все точки"
        row = period.get("by_store", {}).get(scope) if store else period.get("summary", {})
        if not row:
            raise AnalyticsError(f"Нет P&L за {month} для {scope}")
        value = row.get(metric)
        if value is None and metric.endswith("_pct"):
            numerator_key = {
                "cogs_pct": "cogs", "gross_margin_pct": "gross_profit",
                "opex_pct": "opex", "ebitda_pct": "ebitda", "net_margin_pct": "net_profit",
            }[metric]
            revenue = row.get("revenue") or 0
            numerator = row.get(numerator_key)
            value = _round(numerator / revenue * 100, 1) if revenue and numerator is not None else None
        revenue = row.get("revenue")
        checks = [{"name": "period_present", "ok": True}]
        if metric.endswith("_pct"):
            numerator_key = {
                "cogs_pct": "cogs", "gross_margin_pct": "gross_profit",
                "opex_pct": "opex", "ebitda_pct": "ebitda", "net_margin_pct": "net_profit",
            }[metric]
            numerator = row.get(numerator_key)
            formula = f"{numerator_key} / revenue * 100"
            operands = {numerator_key: numerator, "revenue": revenue}
            checks.append({"name": "revenue_nonzero", "ok": bool(revenue)})
        else:
            formula = f"P&L {metric} for period"
            operands = {metric: value}
        if all(row.get(k) is not None for k in ("revenue", "cogs", "gross_profit")):
            checks.append({
                "name": "gross_profit_identity",
                "ok": math.isclose(row["gross_profit"], row["revenue"] - row["cogs"], abs_tol=2),
                "difference": row["gross_profit"] - (row["revenue"] - row["cogs"]),
            })
        return self._result(metric=metric, label=label, value=value, unit=unit, period=month,
                            scope=scope, formula=formula, operands=operands,
                            source=self._source(self.pnl_path, self.pnl), checks=checks)

    def compare(self, metric: str, month_a: str, month_b: str, store: str | None = None,
                dataset: str = "sales") -> dict[str, Any]:
        getter = self.monthly if dataset == "sales" else self.pnl_metric
        a = getter(month_a, metric, store)
        b = getter(month_b, metric, store)
        av, bv = a["value"], b["value"]
        if av is None or bv is None:
            raise AnalyticsError("Сравнение невозможно: одно из значений отсутствует")
        delta = av - bv
        delta_pct = _round(delta / abs(bv) * 100, 1) if bv else None
        return {
            "metric": metric,
            "scope": a["scope"],
            "period_a": month_a,
            "period_b": month_b,
            "value_a": av,
            "value_b": bv,
            "delta": delta,
            "delta_pct": delta_pct,
            "calculation": {
                "formula": "delta = A - B; delta_pct = (A - B) / abs(B) * 100",
                "operands": {"A": av, "B": bv},
            },
            "verification": {
                "status": "verified" if a["verification"]["status"] == b["verification"]["status"] == "verified" else "warning",
                "inputs": [a["verification"], b["verification"]],
            },
            "sources": [a["source"], b["source"]],
        }

    def top(self, kind: str = "all", limit: int = 5) -> dict[str, Any]:
        key = {"all": "top_items", "food": "top_food", "drinks": "top_drinks"}.get(kind)
        if not key:
            raise AnalyticsError("kind должен быть all, food или drinks")
        rows = list(self.dashboard.get(key, []))[: max(1, limit)]
        return {
            "kind": kind,
            "period": f"{self.dashboard.get('date', '')[:7]} MTD",
            "items": rows,
            "calculation": {"formula": "GROUP BY item; SUM(qty), SUM(revenue); ORDER BY revenue DESC"},
            "verification": {"status": "verified" if rows else "warning", "checks": [{"name": "nonempty", "ok": bool(rows)}]},
            "source": self._source(self.dashboard_path, self.dashboard),
        }

    def catalog(self) -> dict[str, Any]:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "stores": self.stores(),
            "current_date": self.dashboard.get("date"),
            "sales_months": [x.get("month") for x in self.finance.get("monthly_totals", [])],
            "pnl_months": [x.get("mkey") for x in self.pnl.get("months", [])],
            "metrics": {
                "current": sorted(CURRENT_METRICS),
                "monthly_sales": sorted(MONTHLY_METRICS),
                "pnl": sorted(PNL_METRICS),
                "plan": sorted(PLAN_METRICS),
            },
            "history_snapshots": sorted(p.stem for p in (self.root / "analytics" / "history" / "daily").glob("*.json")),
            "sources": [
                self._source(self.dashboard_path, self.dashboard),
                self._source(self.finance_path, self.finance),
                self._source(self.pnl_path, self.pnl),
            ],
            "privacy": "Guest-level PII is excluded from this analytics layer.",
        }

    def health(self) -> dict[str, Any]:
        current = self.current("revenue")
        store_revenue = sum(float(row.get("revenue") or 0) for row in self.dashboard.get("points", []))
        network_revenue = float(self.dashboard.get("summary", {}).get("revenue") or 0)
        plan = self.dashboard.get("plan", {}).get("summary", {})
        plan_points = self.dashboard.get("plan", {}).get("points", [])
        store_plan = sum(float(row.get("plan_month") or 0) for row in plan_points)
        checks = [
            {"name": "current_points_present", "ok": bool(self.dashboard.get("points"))},
            {"name": "store_revenue_reconciles", "ok": math.isclose(store_revenue, network_revenue, abs_tol=2),
             "difference": store_revenue - network_revenue},
            {"name": "store_plans_reconcile", "ok": math.isclose(store_plan, float(plan.get("plan_month") or 0), abs_tol=2),
             "difference": store_plan - float(plan.get("plan_month") or 0)},
            {"name": "finance_months_present", "ok": bool(self.finance.get("monthly_totals"))},
            {"name": "pnl_months_present", "ok": bool(self.pnl.get("months"))},
            {"name": "current_source_fresh", "ok": (current["source"].get("age_hours") or 0) <= 2,
             "age_hours": current["source"].get("age_hours")},
        ]
        return {
            "status": "healthy" if all(x["ok"] for x in checks) else "warning",
            "checks": checks,
            "current_source": current["source"],
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verified Garden Coffee analytics")
    parser.add_argument("--root", default=str(ROOT), help="Repository/data directory")
    parser.add_argument("--compact", action="store_true", help="Compact JSON output")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("current")
    p.add_argument("--metric", choices=sorted(CURRENT_METRICS), required=True)
    p.add_argument("--store")

    p = sub.add_parser("daily")
    p.add_argument("--date", required=True)
    p.add_argument("--metric", choices=sorted(CURRENT_METRICS), required=True)
    p.add_argument("--store")

    p = sub.add_parser("plan")
    p.add_argument("--metric", choices=sorted(PLAN_METRICS), required=True)
    p.add_argument("--store")

    p = sub.add_parser("monthly")
    p.add_argument("--month", required=True)
    p.add_argument("--metric", choices=sorted(MONTHLY_METRICS), required=True)
    p.add_argument("--store")

    p = sub.add_parser("pnl")
    p.add_argument("--month", required=True)
    p.add_argument("--metric", choices=sorted(PNL_METRICS), required=True)
    p.add_argument("--store")

    p = sub.add_parser("compare")
    p.add_argument("--dataset", choices=("sales", "pnl"), default="sales")
    p.add_argument("--metric", required=True)
    p.add_argument("--month-a", required=True)
    p.add_argument("--month-b", required=True)
    p.add_argument("--store")

    p = sub.add_parser("top")
    p.add_argument("--kind", choices=("all", "food", "drinks"), default="all")
    p.add_argument("--limit", type=int, default=5)

    sub.add_parser("catalog")
    sub.add_parser("health")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        service = AnalyticsService(args.root)
        if args.command == "current":
            result = service.current(args.metric, args.store)
        elif args.command == "daily":
            result = service.daily(args.date, args.metric, args.store)
        elif args.command == "plan":
            result = service.plan(args.metric, args.store)
        elif args.command == "monthly":
            result = service.monthly(args.month, args.metric, args.store)
        elif args.command == "pnl":
            result = service.pnl_metric(args.month, args.metric, args.store)
        elif args.command == "compare":
            result = service.compare(args.metric, args.month_a, args.month_b, args.store, args.dataset)
        elif args.command == "top":
            result = service.top(args.kind, args.limit)
        elif args.command == "catalog":
            result = service.catalog()
        else:
            result = service.health()
    except AnalyticsError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=None if args.compact else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
