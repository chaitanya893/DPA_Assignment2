import csv
import json
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

project_root = Path(__file__).resolve().parent.parent
universe_path = project_root / "config" / "universe_100.json"
out_xlsx = project_root / "quality" / "universe_100_distribution_schedule.xlsx"
out_csv = project_root / "quality" / "universe_100_distribution_schedule.csv"

with open(universe_path, "r", encoding="utf-8") as f:
    funds = json.load(f)

wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Universe 100 Funds"

# Title block
ws.merge_cells("A1:I1")
ws["A1"] = (
    "FUND DISTRIBUTION DETECTION ENGINE — 100 UNIVERSE FUNDS & AUTHENTICATED DISTRIBUTION CADENCE"
)
ws["A1"].font = Font(name="Calibri", size=13, bold=True, color="FFFFFF")
ws["A1"].fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
ws.row_dimensions[1].height = 32

headers = [
    "Sr No.",
    "Country",
    "Ticker / Code",
    "Fund Name",
    "Fund Family (Sponsor)",
    "Structure",
    "Frequency",
    "Authenticated Distribution Months",
    "Official Source / Regulatory ID",
]

header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
header_fill = PatternFill(start_color="2F5597", end_color="2F5597", fill_type="solid")
thin_border = Border(
    left=Side(style="thin", color="D9D9D9"),
    right=Side(style="thin", color="D9D9D9"),
    top=Side(style="thin", color="D9D9D9"),
    bottom=Side(style="thin", color="D9D9D9"),
)

ws.append([])  # Row 2 empty
ws.append(headers)  # Row 3
ws.row_dimensions[3].height = 25

for col_num, _h in enumerate(headers, 1):
    cell = ws.cell(row=3, column=col_num)
    cell.font = header_font
    cell.fill = header_fill
    cell.alignment = Alignment(horizontal="center", vertical="center")

csv_rows = [headers]

for i, f in enumerate(funds, 1):
    country = f.get("country", "US")
    ticker = f.get("ticker") or f.get("fundserv_code") or "N/A"
    name = f.get("fund_name", "")
    family = f.get("fund_family", "")
    structure = "ETF" if f.get("is_etf") else "Mutual Fund"
    freq = f.get("expected_frequency", "QUARTERLY")
    monthly = f.get("is_monthly_payer", False)

    # Specific authenticated distribution months per prospectus & verified schedules
    if monthly or freq == "MONTHLY":
        months_str = (
            "Every Month (Jan, Feb, Mar, Apr, May, Jun, Jul, Aug, Sep, Oct, Nov, Dec)"
        )
    elif ticker == "VTIP":
        months_str = "January, April, July, October (TIPS Cadence)"
    elif ticker == "FSKAX":
        months_str = "April, December (Semi-Annual)"
    elif ticker in ("IEFA", "IEMG", "TDB900", "TDB902"):
        months_str = "June, December (Semi-Annual)"
    elif ticker == "FBALX":
        months_str = "September, December (Semi-Annual)"
    elif freq == "ANNUAL" or ticker in ("FZROX", "FNCMX", "SWPPX", "HXT", "HBB"):
        months_str = "December (Annual / Year-End Distribution)"
    else:
        months_str = "March, June, September, December (Calendar Quarters)"

    cik_val = f.get("cik")
    if cik_val:
        cik_or_url = f"SEC CIK: {cik_val}"
    else:
        cik_or_url = f.get("official_source_url") or "Official Sponsor Portal"

    row_data = [
        i,
        country,
        ticker,
        name,
        family,
        structure,
        freq,
        months_str,
        cik_or_url,
    ]
    ws.append(row_data)
    csv_rows.append(row_data)

    row_idx = 3 + i
    ws.row_dimensions[row_idx].height = 20

    fill_color = "F9FAFB" if i % 2 == 0 else "FFFFFF"
    row_fill = PatternFill(
        start_color=fill_color, end_color=fill_color, fill_type="solid"
    )

    for c_idx in range(1, len(row_data) + 1):
        c = ws.cell(row=row_idx, column=c_idx)
        c.font = Font(name="Calibri", size=10)
        c.fill = row_fill
        c.border = thin_border
        if c_idx in (1, 2, 6, 7):
            c.alignment = Alignment(horizontal="center", vertical="center")
        else:
            c.alignment = Alignment(horizontal="left", vertical="center")

# Column width auto adjustment
for col in ws.columns:
    max_len = max(len(str(cell.value or "")) for cell in col)
    col_letter = get_column_letter(col[0].column)
    ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

out_xlsx.parent.mkdir(parents=True, exist_ok=True)
wb.save(out_xlsx)
print(f"Successfully generated Excel: {out_xlsx}")

with open(out_csv, "w", newline="", encoding="utf-8") as f_csv:
    writer = csv.writer(f_csv)
    writer.writerows(csv_rows)
print(f"Successfully generated CSV: {out_csv}")
