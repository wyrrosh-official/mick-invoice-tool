"""CSV export and the running monthly Excel workbook."""

from datetime import date
from pathlib import Path

import pandas as pd

from validation import parse_date

EXPORTS_DIR = Path(__file__).parent / "exports"
CSV_EXPORTS_DIR = Path(__file__).parent / "csv_exports"

LINE_COLUMNS = [
    "supplier",
    "invoice_number",
    "invoice_date",
    "department",
    "description",
    "qty",
    "unit",
    "unit_price",
    "line_total",
    "check",
    "check_reason",
]


def _invoice_month(invoice_date_str: str) -> str:
    d = parse_date(invoice_date_str) or date.today()
    return d.strftime("%Y-%m")


def invoice_line_rows(header: dict, line_items: list[dict]) -> list[dict]:
    return [
        {
            "supplier": header.get("supplier"),
            "invoice_number": header.get("invoice_number"),
            "invoice_date": header.get("invoice_date"),
            "department": header.get("department"),
            "description": item.get("description"),
            "qty": item.get("qty"),
            "unit": item.get("unit"),
            "unit_price": item.get("unit_price"),
            "line_total": item.get("line_total"),
            "check": item.get("check"),
            "check_reason": item.get("check_reason"),
        }
        for item in line_items
    ]


def invoice_csv_bytes(header: dict, line_items: list[dict]) -> bytes:
    df = pd.DataFrame(invoice_line_rows(header, line_items), columns=LINE_COLUMNS)
    return df.to_csv(index=False).encode("utf-8")


def csv_filename(header: dict, fallback_id: str) -> str:
    supplier = (header.get("supplier") or "invoice").strip().replace(" ", "_")
    number = (header.get("invoice_number") or fallback_id[:8]).strip().replace(" ", "_")
    return f"{supplier}_{number}.csv"


def save_invoice_csv(header: dict, line_items: list[dict], fallback_id: str) -> Path:
    """Writes this invoice's CSV to disk in csv_exports/ and returns its path."""
    CSV_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = CSV_EXPORTS_DIR / csv_filename(header, fallback_id)
    path.write_bytes(invoice_csv_bytes(header, line_items))
    return path


def append_to_monthly_workbook(header: dict, line_items: list[dict]) -> Path:
    """Adds this invoice to its month's running Excel workbook, rebuilding the
    department totals sheet. Returns the workbook path."""
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    month = _invoice_month(header.get("invoice_date"))
    path = EXPORTS_DIR / f"invoices_{month}.xlsx"

    new_invoice_row = pd.DataFrame(
        [
            {
                "invoice_date": header.get("invoice_date"),
                "supplier": header.get("supplier"),
                "invoice_number": header.get("invoice_number"),
                "department": header.get("department"),
                "gst": header.get("gst"),
                "invoice_total": header.get("invoice_total"),
            }
        ]
    )
    new_line_rows = pd.DataFrame(invoice_line_rows(header, line_items), columns=LINE_COLUMNS)

    if path.exists():
        existing_invoices = pd.read_excel(path, sheet_name="Invoices")
        existing_lines = pd.read_excel(path, sheet_name="Line items")
        invoices_df = pd.concat([existing_invoices, new_invoice_row], ignore_index=True)
        lines_df = pd.concat([existing_lines, new_line_rows], ignore_index=True)
    else:
        invoices_df = new_invoice_row
        lines_df = new_line_rows

    totals_df = (
        invoices_df.groupby("department", dropna=False)["invoice_total"]
        .sum()
        .reset_index()
        .rename(columns={"invoice_total": "total_spend"})
    )

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        invoices_df.to_excel(writer, sheet_name="Invoices", index=False)
        lines_df.to_excel(writer, sheet_name="Line items", index=False)
        totals_df.to_excel(writer, sheet_name="Department totals", index=False)

    return path
