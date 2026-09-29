"""Checks an extracted invoice for problems Mick should look at before export."""

from datetime import date, datetime
from typing import Optional

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y")


def to_float(value) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_date(value) -> Optional[date]:
    """Tolerantly parses a date that may have been typed as DD/MM/YYYY
    instead of the expected YYYY-MM-DD. Returns None if nothing matches."""
    if not value:
        return None
    text = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def validate_invoice(header: dict, line_items: list[dict]) -> dict:
    """Checks an invoice for problems before saving.

    Returns issues grouped by type, so the UI can show a count per group and
    only reveal the item list on request - a long invoice can otherwise
    produce a wall of one-line-per-item warnings:

    - "totals": the single most important check (line items vs. invoice
      total) - {"level": "error"|"warning", "message": str} or None.
    - "gst": the GST plausibility check - str message or None.
    - "missing_fields": list of missing header field labels.
    - "line_issues": list of {"description", "reason"} for line items
      that are CHECK-flagged or missing a number.
    """
    missing_fields = []
    required = [
        ("supplier", "Supplier"),
        ("invoice_number", "Invoice number"),
        ("invoice_date", "Invoice date"),
        ("invoice_total", "Invoice total"),
    ]
    for field, label in required:
        if not header.get(field):
            missing_fields.append(label)

    line_issues = []
    for i, item in enumerate(line_items, start=1):
        description = item.get("description") or f"line {i}"
        if item.get("check"):
            reason = item.get("check_reason") or "flagged for review by the extraction"
            line_issues.append({"description": description, "reason": reason})
            continue

        missing = [f for f in ("qty", "unit_price", "line_total") if to_float(item.get(f)) is None]
        if missing:
            line_issues.append(
                {"description": description, "reason": f"missing {', '.join(missing)} - fill in or leave for CHECK."}
            )

    line_totals = [to_float(item.get("line_total")) for item in line_items]
    line_totals = [v for v in line_totals if v is not None]
    total = to_float(header.get("invoice_total"))
    gst = to_float(header.get("gst"))

    totals_flag = None
    if line_totals and total is not None:
        lines_sum = round(sum(line_totals), 2)
        tolerance = max(0.05, total * 0.01)

        candidates = [("line items vs. total", abs(lines_sum - total))]
        if gst is not None:
            candidates.append(("line items + GST vs. total", abs(lines_sum + gst - total)))

        best_label, best_diff = min(candidates, key=lambda c: c[1])
        if best_diff > tolerance:
            totals_flag = {
                "level": "error",
                "message": (
                    f"Line items (${lines_sum:.2f}) don't add up to the invoice "
                    f"total (${total:.2f}) - off by ${best_diff:.2f} ({best_label})."
                ),
            }
    elif not line_totals:
        totals_flag = {
            "level": "warning",
            "message": "No line item totals available to check against the invoice total.",
        }

    gst_flag = None
    if total is not None and gst is not None:
        expected_gst = total / 11  # standard AU 10% GST, GST-inclusive total
        if abs(gst - expected_gst) > max(0.10, expected_gst * 0.15):
            gst_flag = (
                f"GST (${gst:.2f}) doesn't look like standard 10% of the total "
                f"(expected about ${expected_gst:.2f}) - check for GST-free items."
            )

    return {
        "totals": totals_flag,
        "gst": gst_flag,
        "missing_fields": missing_fields,
        "line_issues": line_issues,
    }


def validate_stocktake(rows: list[dict]) -> list[dict]:
    """Checks counted stock rows for problems before saving.

    Returns issues grouped by type, so the UI can show a count per group
    and only reveal the item list on request - a sheet with 30+ items can
    otherwise produce a wall of one-line-per-item warnings.
    """
    blank_count = []
    needs_confirming = []
    no_price_history = []

    for i, row in enumerate(rows, start=1):
        description = row.get("description") or f"row {i}"

        if row.get("check"):
            reason = row.get("check_reason") or "flagged for review by the extraction"
            needs_confirming.append({"description": description, "reason": reason})
        elif to_float(row.get("counted_qty")) is None:
            blank_count.append(description)

        if row.get("unit_price") is None:
            no_price_history.append(description)

    return {
        "blank_count": blank_count,
        "needs_confirming": needs_confirming,
        "no_price_history": no_price_history,
    }
