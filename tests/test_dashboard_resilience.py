import json
import pathlib
import unittest
from datetime import datetime
from unittest.mock import patch

import iiko_resto_connector as connector


ROOT = pathlib.Path(__file__).resolve().parents[1]


class CurrentSalesRetryTests(unittest.TestCase):
    def test_empty_report_before_opening_is_allowed(self):
        with patch.object(connector, "olap_sales_report", return_value={"data": []}) as report:
            result = connector.current_sales_report(
                "host", "token", "2026-09-28", "2026-09-29", datetime(2026, 9, 28, 5, 30)
            )
        self.assertEqual(result, {"data": []})
        report.assert_called_once()

    def test_transient_empty_report_is_retried(self):
        valid = {"data": [{"Department": "Океан"}]}
        with (
            patch.object(connector, "olap_sales_report", side_effect=[{"data": []}, valid]) as report,
            patch.object(connector.time, "sleep") as sleep,
        ):
            result = connector.current_sales_report(
                "host", "token", "2026-09-28", "2026-09-29", datetime(2026, 9, 28, 10, 0)
            )
        self.assertEqual(result, valid)
        self.assertEqual(report.call_count, 2)
        sleep.assert_called_once_with(connector.EMPTY_REPORT_RETRY_DELAY)

    def test_persistent_empty_report_never_replaces_snapshot(self):
        with (
            patch.object(connector, "olap_sales_report", return_value={"data": []}) as report,
            patch.object(connector.time, "sleep") as sleep,
        ):
            with self.assertRaisesRegex(RuntimeError, "предыдущие данные сохранены"):
                connector.current_sales_report(
                    "host", "token", "2026-09-28", "2026-09-29", datetime(2026, 9, 28, 10, 0)
                )
        self.assertEqual(report.call_count, connector.EMPTY_REPORT_ATTEMPTS)
        self.assertEqual(sleep.call_count, connector.EMPTY_REPORT_ATTEMPTS - 1)


class PublishedArtifactTests(unittest.TestCase):
    def test_current_view_has_required_shape(self):
        data = json.loads((ROOT / "dashboard_data_view.json").read_text(encoding="utf-8"))
        self.assertIn("date", data)
        self.assertIn("summary", data)
        self.assertIn("points", data)
        self.assertIn("plan", data)
        self.assertGreater(len(data["points"]), 0)
        self.assertGreater(data["summary"]["revenue"], 0)

    def test_desktop_and_mobile_have_all_recovery_layers(self):
        for filename in ("coffee_dashboard.html", "coffee_dashboard_mobile.html"):
            html = (ROOT / filename).read_text(encoding="utf-8")
            with self.subTest(filename=filename):
                self.assertIn("raw.githubusercontent.com", html)
                self.assertIn("api.github.com/repos/", html)
                self.assertIn("DASHBOARD_CACHE_KEY", html)
                self.assertIn("renderCachedData();", html)
                self.assertIn("visibilitychange", html)
                self.assertIn("setInterval(loadData, 60 * 1000)", html)


if __name__ == "__main__":
    unittest.main()
