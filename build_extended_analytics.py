#!/usr/bin/env python3
"""Reduce the detailed iiko export to report-ready datasets."""

import gzip, json, os
from collections import defaultdict

SOURCE = "iiko_extended_data.json.gz"
OUT = "iiko_analytics_ready.json"

def num(row, key):
    try: return float(row.get(key) or 0)
    except (TypeError, ValueError): return 0.0

def main():
    with gzip.open(SOURCE, "rt", encoding="utf-8") as source:
        raw = json.load(source)
    src = raw["sources"]
    checks = []
    for row in src["sales_checks"]["rows"]:
        revenue, orders = num(row,"DishDiscountSumInt"), num(row,"UniqOrderId.OrdersCount")
        checks.append({"store":row.get("Department"),"revenue":round(revenue),"checks":round(orders),"avg_check":round(revenue/orders,2) if orders else 0})
    menu = []
    for row in src["menu_sku"]["rows"]:
        qty, revenue, cost = num(row,"DishAmountInt"), num(row,"DishDiscountSumInt"), num(row,"ProductCostBase.ProductCost")
        if qty or revenue or cost:
            menu.append({"store":row.get("Department"),"sku":row.get("DishName"),"group":row.get("DishGroup"),"category":row.get("DishCategory"),"qty":qty,"revenue":round(revenue),"cost":round(cost),"margin":round(revenue-cost)})
    purchases = defaultdict(lambda:{"qty":0.0,"sum":0.0,"dates":{}})
    for row in src["purchases"]["rows"]:
        qty, total = num(row,"Amount.In"), num(row,"Sum.Incoming")
        if qty <= 0 or total <= 0: continue
        key=(row.get("Department"),row.get("Counteragent.Name"),row.get("Product.Id"),row.get("Product.Name"),row.get("Product.MeasureUnit"))
        item=purchases[key]; item["qty"]+=qty; item["sum"]+=total
        d=str(row.get("DateTime.DateTyped") or ""); item["dates"].setdefault(d,[0,0]); item["dates"][d][0]+=qty; item["dates"][d][1]+=total
    purchase_rows=[]
    for key,item in purchases.items():
        prices=[(d,v[1]/v[0]) for d,v in sorted(item["dates"].items()) if v[0]]
        purchase_rows.append({"store":key[0],"supplier":key[1],"product_id":key[2],"item":key[3],"unit":key[4],"qty":round(item["qty"],3),"sum":round(item["sum"]),"avg_price":round(item["sum"]/item["qty"],4),"first_price":round(prices[0][1],4) if prices else 0,"last_price":round(prices[-1][1],4) if prices else 0})
    usage=defaultdict(lambda:{"qty":0.0,"sum":0.0})
    for row in src["raw_material"]["rows"]:
        qty,total=num(row,"Amount.Out"),num(row,"Sum.Outgoing")
        if qty <= 0 and total <= 0: continue
        key=(row.get("Department"),row.get("Product.Id"),row.get("Product.Name"),row.get("Product.MeasureUnit"),row.get("Account.Name"),row.get("Contr-Account.Name"))
        usage[key]["qty"]+=qty; usage[key]["sum"]+=total
    usage_rows=[{"store":k[0],"product_id":k[1],"item":k[2],"unit":k[3],"account":k[4],"contra_account":k[5],"qty":round(v["qty"],3),"sum":round(v["sum"])} for k,v in usage.items()]
    ready={"generated_at":raw["generated_at"],"period":raw["period"],"sales_checks":checks,"menu_sku":menu,"purchases":purchase_rows,"raw_material":usage_rows,"sales_by_hour":src["sales_by_hour"]["rows"]}
    with open(OUT,"w",encoding="utf-8") as target: json.dump(ready,target,ensure_ascii=False,separators=(",",":"))
    print({k:len(v) if isinstance(v,list) else v for k,v in ready.items()})
if __name__ == "__main__": main()
