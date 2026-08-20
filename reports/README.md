# Garden monthly analytics

`monthly/` stores an immutable HTML and structured JSON report for every period.
The JSON schema includes KPI, comparisons, anomalies/problems, opportunities,
financial impact, action plan, previous-plan review and data-quality results.

`operational/alert_history.json` stores daily purchase-price and raw-material
signals. The operational monitor requires two source adapters:

- `purchase_price_data.json`: item, supplier, previous/current price, 30/90-day
  averages, monthly volume, date and affected SKU;
- `raw_material_usage_data.json`: item, store, actual/expected usage, unit cost
  and linked SKU.

If an adapter is absent, the monitor returns `data_quality_warning`; absence of
data is never treated as absence of anomalies.
