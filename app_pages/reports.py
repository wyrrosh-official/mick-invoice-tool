from datetime import date, timedelta

import pandas as pd
import streamlit as st

import storage
from validation import parse_date

st.write("Totals by supplier and department, weekly or monthly, plus the closing-stock check.")

saved_invoices = storage.get_all_invoices()

dated_invoices = []
unparseable = []
for inv in saved_invoices:
    parsed = parse_date(inv["invoice_date"])
    if parsed:
        inv["_parsed_date"] = parsed
        dated_invoices.append(inv)
    elif inv["invoice_date"]:
        unparseable.append(inv)

if unparseable:
    with st.expander(f"{len(unparseable)} invoice(s) have a date that couldn't be read and are left out of reports"):
        for inv in unparseable:
            st.write(f"- {inv['supplier'] or '?'} {inv['invoice_number'] or '?'}: \"{inv['invoice_date']}\"")

if not dated_invoices:
    st.caption("No dated invoices saved yet - save a few from the Invoices page first, and reports will appear here.")
    st.stop()

period_type = st.radio("Period", ["Weekly", "Monthly", "Custom range"], horizontal=True, key="report_period_type")


def _week_range(any_date: date) -> tuple[date, date]:
    start = any_date - timedelta(days=any_date.weekday())  # Monday
    return start, start + timedelta(days=6)


if period_type == "Weekly":
    weeks = sorted({_week_range(inv["_parsed_date"]) for inv in dated_invoices}, reverse=True)
    week_labels = {f"{w[0].isoformat()} to {w[1].isoformat()}": w for w in weeks}
    chosen_label = st.selectbox("Week", list(week_labels.keys()), key="report_week_select")
    period_start, period_end = week_labels[chosen_label]
elif period_type == "Monthly":
    months = sorted({inv["_parsed_date"].strftime("%Y-%m") for inv in dated_invoices}, reverse=True)
    chosen_month = st.selectbox("Month", months, key="report_month_select")
    period_start = parse_date(f"{chosen_month}-01")
    if period_start.month == 12:
        period_end = date(period_start.year, 12, 31)
    else:
        period_end = date(period_start.year, period_start.month + 1, 1) - timedelta(days=1)
else:
    earliest = min(inv["_parsed_date"] for inv in dated_invoices)
    latest = max(inv["_parsed_date"] for inv in dated_invoices)
    range_col1, range_col2 = st.columns(2)
    with range_col1:
        period_start = st.date_input(
            "From", value=earliest, min_value=earliest, max_value=latest, key="report_range_start"
        )
    with range_col2:
        period_end = st.date_input(
            "To", value=latest, min_value=earliest, max_value=latest, key="report_range_end"
        )
    if period_start > period_end:
        st.error("The 'From' date is after the 'To' date.")
        st.stop()

period_invoices = [inv for inv in dated_invoices if period_start <= inv["_parsed_date"] <= period_end]

st.caption(f"{len(period_invoices)} invoice(s) between {period_start.isoformat()} and {period_end.isoformat()}.")

if not period_invoices:
    st.info("No invoices saved in this period.")
    st.stop()

total_spend = sum(inv["invoice_total"] or 0 for inv in period_invoices)
st.metric("Total purchases this period", f"${total_spend:,.2f}")

st.subheader("By supplier")
supplier_totals: dict[str, float] = {}
for inv in period_invoices:
    key = inv["supplier"] or "Unknown"
    supplier_totals[key] = supplier_totals.get(key, 0) + (inv["invoice_total"] or 0)

supplier_df = pd.DataFrame(
    sorted(supplier_totals.items(), key=lambda kv: kv[1], reverse=True), columns=["Supplier", "Total"]
)
st.dataframe(
    supplier_df,
    hide_index=True,
    width="stretch",
    column_config={"Total": st.column_config.NumberColumn("Total ($)", format="%.2f")},
)

drill_supplier = st.selectbox(
    "View invoices for", ["All suppliers"] + list(supplier_totals.keys()), key="report_supplier_drill"
)
drill_invoices = (
    period_invoices
    if drill_supplier == "All suppliers"
    else [inv for inv in period_invoices if (inv["supplier"] or "Unknown") == drill_supplier]
)
drill_df = pd.DataFrame(
    [
        {
            "Date": inv["invoice_date"],
            "Supplier": inv["supplier"],
            "Invoice #": inv["invoice_number"],
            "Department": inv["department"],
            "Total": inv["invoice_total"],
        }
        for inv in drill_invoices
    ]
)
st.dataframe(
    drill_df,
    hide_index=True,
    width="stretch",
    column_config={"Total": st.column_config.NumberColumn("Total ($)", format="%.2f")},
)

st.subheader("By department")
dept_totals: dict[str, float] = {}
for inv in period_invoices:
    key = inv["department"] or "unclear"
    dept_totals[key] = dept_totals.get(key, 0) + (inv["invoice_total"] or 0)
dept_df = pd.DataFrame(
    sorted(dept_totals.items(), key=lambda kv: kv[1], reverse=True), columns=["Department", "Total"]
)
st.dataframe(
    dept_df,
    hide_index=True,
    width="stretch",
    column_config={"Total": st.column_config.NumberColumn("Total ($)", format="%.2f")},
)

st.subheader("By item", icon=":material/restaurant:")
st.caption("Total spend per product for this period - matches the food item breakdown built by hand today.")
item_totals = storage.get_line_item_totals_for_period(period_start.isoformat(), period_end.isoformat())
if item_totals:
    item_df = pd.DataFrame(item_totals).rename(columns={"description": "Item", "total": "Total"})
    st.dataframe(
        item_df,
        hide_index=True,
        width="stretch",
        column_config={"Total": st.column_config.NumberColumn("Total ($)", format="%.2f")},
    )
else:
    st.caption("No line items in this period yet.")

st.divider()
st.subheader("Closing stock check")
st.caption("Flags when closing stock value is over 30% of this period's purchases - often a keying error or a stock problem.")

latest_stocktake = storage.get_latest_stocktake_value(as_of=period_end.isoformat())

col1, col2 = st.columns(2)
with col1:
    if latest_stocktake:
        st.metric(
            "Latest stocktake value",
            f"${latest_stocktake['total_value']:,.2f}",
            help=f"Counted {latest_stocktake['count_date']}",
        )
    else:
        st.caption("No stocktake saved yet for or before this period.")
with col2:
    manual_closing_stock = st.number_input(
        "Or type closing stock value from Bevlink ($)",
        min_value=0.0,
        step=0.01,
        format="%.2f",
        key="report_manual_closing_stock",
    )

closing_stock_value = manual_closing_stock if manual_closing_stock > 0 else (
    latest_stocktake["total_value"] if latest_stocktake else None
)

if closing_stock_value is not None and total_spend > 0:
    pct = closing_stock_value / total_spend * 100
    st.metric("Closing stock as % of period purchases", f"{pct:.1f}%")
    if pct > 30:
        message = (
            f"Closing stock (${closing_stock_value:,.2f}) is over 30% of this period's purchases "
            f"(${total_spend:,.2f}) - worth checking for a keying error or a stock problem."
        )
        st.warning(message.replace("$", r"\$"))
    else:
        st.success("Closing stock is under 30% of purchases - no red flag.")
else:
    st.caption("Add a stocktake or type a closing stock value to run the 30% check.")
