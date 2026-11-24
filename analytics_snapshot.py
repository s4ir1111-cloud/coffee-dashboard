#!/usr/bin/env python3
"""Archive a privacy-safe, query-ready daily analytics snapshot."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analytics_query import AnalyticsService, ROOT


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def build_snapshot(root: Path = ROOT) -> tuple[Path, Path, Path]:
    service = AnalyticsService(root)
    data = service.dashboard
    day = str(data.get("date") or "")
    if not day:
        raise RuntimeError("dashboard_data_view.json does not contain date")
    if not data.get("points") or not data.get("summary", {}).get("revenue"):
        raise RuntimeError("Refusing to archive an empty sales snapshot")

    snapshot = {
        "schema_version": 1,
        "date": day,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source": service._source(service.dashboard_path, service.dashboard),
        "summary": data.get("summary", {}),
        "points": data.get("points", []),
        "daily_sales_mtd": data.get("weekly", []),
        "top_food_mtd": data.get("top_food", []),
        "top_drinks_mtd": data.get("top_drinks", []),
        "plan": data.get("plan", {}),
        "privacy": "No guest-level personal data is stored.",
    }
    history_dir = root / "analytics" / "history" / "daily"
    snapshot_path = history_dir / f"{day}.json"
    _atomic_json(snapshot_path, snapshot)

    entries = []
    for path in sorted(history_dir.glob("*.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        entries.append({
            "date": item.get("date"),
            "captured_at": item.get("captured_at"),
            "generated_at": item.get("source", {}).get("generated_at"),
            "revenue": item.get("summary", {}).get("revenue"),
            "orders": item.get("summary", {}).get("orders"),
            "file": str(path.relative_to(root)),
        })
    index_path = root / "analytics" / "history" / "index.json"
    _atomic_json(index_path, {"schema_version": 1, "entries": entries})

    catalog_path = root / "analytics" / "catalog.json"
    _atomic_json(catalog_path, service.catalog())
    return snapshot_path, index_path, catalog_path


def main() -> int:
    paths = build_snapshot(ROOT)
    print("Analytics snapshot updated:")
    for path in paths:
        print(f"- {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
