import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from iiko_extended_connector import login, HOST
import requests

now = datetime.now(ZoneInfo("Asia/Yekaterinburg"))
qty, checks = "DishAmountInt", "UniqOrderId.OrdersCount"
flags = ("DeletedWithWriteoff", "OrderDeleted")
stage = "authentication"
token = None
try:
    token = login()
    def query(cols, filters, aggregates=None):
        response = requests.post(
            f"{HOST}/resto/api/v2/reports/olap", params={"key": token}, timeout=75,
            json=dict(reportType="SALES", groupByRowFields=cols, groupByColFields=[],
                      aggregateFields=aggregates or [qty, checks], filters=filters),
        )
        response.raise_for_status()
        return response.json()["data"]
    reports = []
    for start, end in [("2026-10-01", "2026-11-01"), ("2026-11-01", "2026-11-28")]:
        filt = {"OpenDate.Typed": dict(filterType="DateRange", periodType="CUSTOM", **{"from": start, "to": end})}
        stage = "verify deletion fields " + start
        probe = query(list(flags), filt)
        for field in flags:
            values = {row[field] for row in probe}
            accepted = [value for value in values if re.sub(r"[^a-z]", "", str(value).lower()) in ("notdeleted", "false")]
            if len(accepted) != 1:
                raise ValueError("No unambiguous active value")
            filt[field] = dict(filterType="IncludeValues", values=accepted)
        stage = "aggregate sales " + start
        rows = query(["Department"], filt)
        if not rows or any(not r["Department"] or r[qty] is None or not r[checks] for r in rows):
            raise ValueError("Incomplete aggregates")
        stage = "fetch product groups " + start
        items = query(["Department", "DishName", "DishGroup", "DishCategory"], filt, [qty, "DishDiscountSumInt"])
        if not items:
            raise ValueError("No product aggregates")
        reports.append(dict(start=start, end_exclusive=end, rows=rows, items=items))
    with open("basket.json", "w", encoding="utf-8") as f:
        json.dump(dict(as_of=now.isoformat(), reports=reports), f, ensure_ascii=False)
except Exception as exc:
    status = getattr(getattr(exc, "response", None), "status_code", "")
    raise SystemExit(f"Basket report failed at {stage}: {type(exc).__name__} {status}") from None
finally:
    if token:
        try:
            requests.get(f"{HOST}/resto/api/logout", params={"key": token}, timeout=10)
        except requests.RequestException:
            pass
