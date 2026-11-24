import json
import tempfile
import unittest
from pathlib import Path

from analytics_query import AnalyticsError, AnalyticsService
from analytics_snapshot import build_snapshot


class AnalyticsServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self._write("dashboard_data_view.json", {
            "date": "2026-11-24", "generated_at": "2026-11-24T12:00:00+05:00",
            "summary": {"revenue": 3000, "orders": 6, "avg_check": 500, "discount_pct": 9.5},
            "points": [
                {"name": "Океан", "revenue": 2000, "orders": 4, "avg_check": 500},
                {"name": "Московская", "revenue": 1000, "orders": 2, "avg_check": 500},
            ],
            "weekly": [{"date": "2026-11-24", "revenue": 3000}],
            "top_items": [{"name": "Капучино", "qty": 3, "revenue": 900}],
            "top_food": [], "top_drinks": [{"name": "Капучино", "qty": 3, "revenue": 900}],
            "plan": {"summary": {"plan_month": 6000}, "points": [
                {"name": "Океан", "plan_month": 4000},
                {"name": "Московская", "plan_month": 2000},
            ]},
        })
        self._write("finance_data.json", {
            "generated_at": "2026-11-24T07:00:00Z", "current_month": "2026-11",
            "monthly_totals": [
                {"month": "2026-10", "revenue": 10000, "orders": 20, "avg_check": 500},
                {"month": "2026-11", "revenue": 12000, "orders": 20, "avg_check": 600},
            ],
            "by_point": {"Московская": {"monthly": {"2026-10": 4000, "2026-11": 5000},
                                           "monthly_orders": {"2026-10": 10, "2026-11": 10}}},
        })
        self._write("pnl_data.json", {
            "generated_at": "2026-11-24", "months": [{"mkey": "2026-10", "summary": {
                "revenue": 10000, "cogs": 4000, "gross_profit": 6000, "opex": 2000,
                "ebitda": 4000, "net_profit": 3000, "ebitda_pct": 40.0,
            }, "by_store": {}}],
        })
        self.service = AnalyticsService(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, name, payload):
        (self.root / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_current_avg_check_is_recalculated_from_operands(self):
        result = self.service.current("avg_check", "Московская")
        self.assertEqual(result["value"], 500)
        self.assertEqual(result["calculation"]["operands"], {"revenue": 1000.0, "orders": 2.0})
        self.assertEqual(result["verification"]["status"], "verified")

    def test_monthly_comparison_has_traceable_delta(self):
        result = self.service.compare("revenue", "2026-11", "2026-10")
        self.assertEqual(result["delta"], 2000)
        self.assertEqual(result["delta_pct"], 20)
        self.assertEqual(result["calculation"]["operands"], {"A": 12000, "B": 10000})

    def test_pnl_identity_is_checked(self):
        result = self.service.pnl_metric("2026-10", "ebitda")
        identity = next(x for x in result["verification"]["checks"] if x["name"] == "gross_profit_identity")
        self.assertTrue(identity["ok"])

    def test_missing_period_fails_instead_of_returning_zero(self):
        with self.assertRaises(AnalyticsError):
            self.service.monthly("2024-01", "revenue")

    def test_snapshot_is_privacy_safe_and_reconciled(self):
        snapshot_path, index_path, catalog_path = build_snapshot(self.root)
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        self.assertEqual(snapshot["summary"]["revenue"], 3000)
        self.assertNotIn("guests", snapshot)
        self.assertTrue(index_path.exists())
        self.assertTrue(catalog_path.exists())

    def test_archived_daily_metric_keeps_source_evidence(self):
        build_snapshot(self.root)
        self.service.dashboard["date"] = "2026-11-25"
        result = self.service.daily("2026-11-24", "revenue", "Московская")
        self.assertEqual(result["value"], 1000)
        self.assertIn("archive_file", result["source"])

    def test_plan_fails_when_store_plan_is_missing(self):
        self.service.dashboard["plan"]["points"].append({"name": "Без плана", "mtd_revenue": 10, "plan_month": 0})
        with self.assertRaises(AnalyticsError):
            self.service.plan("plan_month", "Без плана")


if __name__ == "__main__":
    unittest.main()
