import json

with open("config/universe_100.json", "r", encoding="utf-8") as f:
    funds = json.load(f)

ca_funds = [f for f in funds if f.get("country") == "CA"]
print(f"Total Canadian funds: {len(ca_funds)}")
for i, f in enumerate(ca_funds, 1):
    symbol = f.get("ticker") or f.get("fundserv_code")
    print(f"{i:02d}. {f['fund_id']:<25} | {symbol:<10} | {f.get('fund_family'):<30} | {f.get('expected_frequency'):<12} | {f.get('official_source_url')}")
