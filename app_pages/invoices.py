import os
import uuid
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

import cost_tracker
import export
import storage
from extractor import CostCapExceeded, extract_invoice
from validation import parse_date, to_float, validate_invoice

IMAGES_DIR = Path(__file__).parent.parent / "data" / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

DEPARTMENTS = ["bar", "kitchen", "other", "mixed", "unclear"]
LINE_ITEM_COLUMNS = ["description", "qty", "unit", "unit_price", "line_total", "check", "check_reason"]

st.write("Upload one or more photos/PDFs of invoices - you can select several at once, then extract them one at a time.")

uploaded_files = st.file_uploader(
    "Invoice file(s)",
    type=["jpg", "jpeg", "png", "pdf"],
    accept_multiple_files=True,
    label_visibility="collapsed",
)

if "invoice_processed_ids" not in st.session_state:
    st.session_state["invoice_processed_ids"] = set()

pending_files = [f for f in (uploaded_files or []) if f.file_id not in st.session_state["invoice_processed_ids"]]

if pending_files:
    next_file = pending_files[0]
    if len(pending_files) > 1:
        st.write(f"{len(pending_files)} file(s) waiting. Next: **{next_file.name}**")
    else:
        st.write(f"Selected: **{next_file.name}**")

    if st.button("Extract invoice details", type="primary"):
        file_bytes = next_file.getvalue()

        with st.spinner("Reading the invoice..."):
            try:
                extraction, call_cost = extract_invoice(file_bytes, next_file.name)
            except CostCapExceeded as e:
                st.error(str(e))
                st.stop()
            except Exception as e:
                st.error(f"Couldn't read that invoice: {e}")
                st.stop()

        record_id = uuid.uuid4().hex
        saved_name = f"{record_id}_{next_file.name}"
        (IMAGES_DIR / saved_name).write_bytes(file_bytes)

        st.session_state["extraction"] = extraction.model_dump()
        st.session_state["record_id"] = record_id
        st.session_state["saved_image_name"] = saved_name
        st.session_state["invoice_processed_ids"].add(next_file.file_id)
        st.success(f"Done. This extraction cost about ${call_cost:.4f}.")

if "extraction" in st.session_state:
    data = st.session_state["extraction"]
    record_id = st.session_state["record_id"]

    st.divider()
    st.subheader("Review")

    image_path = IMAGES_DIR / st.session_state["saved_image_name"]
    with st.expander("View original invoice"):
        if image_path.suffix.lower() in (".jpg", ".jpeg", ".png"):
            st.image(str(image_path), width="stretch")
        else:
            st.download_button(
                "Open original PDF",
                data=image_path.read_bytes(),
                file_name=image_path.name,
                mime="application/pdf",
                key=f"view_pdf_{record_id}",
            )

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        supplier = st.text_input("Supplier", value=data.get("supplier") or "", key=f"supplier_{record_id}")
    with col2:
        invoice_number = st.text_input(
            "Invoice number", value=data.get("invoice_number") or "", key=f"invnum_{record_id}"
        )
    with col3:
        date_extracted = parse_date(data.get("invoice_date"))
        date_value = date_extracted or date.today()
        invoice_date_picked = st.date_input("Invoice date", value=date_value, key=f"invdate_{record_id}")
        invoice_date = invoice_date_picked.isoformat()
        if date_extracted is None:
            st.caption("Wasn't readable - defaulted to today. Please set the real date.")
    with col4:
        dept_value = data.get("department") or "unclear"
        department = st.selectbox(
            "Department",
            DEPARTMENTS,
            index=DEPARTMENTS.index(dept_value) if dept_value in DEPARTMENTS else DEPARTMENTS.index("unclear"),
            key=f"dept_{record_id}",
        )

    total_extracted = data.get("invoice_total")
    total_suggested = False
    if not total_extracted:
        lines_sum = sum(to_float(item.get("line_total")) or 0.0 for item in (data.get("line_items") or []))
        if lines_sum > 0:
            total_extracted = round(lines_sum + (data.get("gst") or 0.0), 2)
            total_suggested = True

    gst_extracted = data.get("gst")
    gst_suggested = False
    if not gst_extracted and total_extracted:
        gst_extracted = round(total_extracted / 11, 2)  # standard AU 10% GST, GST-inclusive total
        gst_suggested = True

    col5, col6 = st.columns(2)
    with col5:
        gst = st.number_input(
            "GST ($)", value=float(gst_extracted or 0.0), step=0.01, format="%.2f", key=f"gst_{record_id}"
        )
        if gst_suggested:
            st.caption("GST wasn't readable - this is 10% of the total. Please check it against the paper copy.")
    with col6:
        invoice_total = st.number_input(
            "Invoice total ($)",
            value=float(total_extracted or 0.0),
            step=0.01,
            format="%.2f",
            key=f"total_{record_id}",
        )
        if total_suggested:
            st.caption(
                "Total wasn't readable on the invoice - this is line items + GST added up. "
                "Please check it against the paper copy."
            )

    st.write("Line items")
    line_items = data.get("line_items") or []
    df = pd.DataFrame(line_items, columns=LINE_ITEM_COLUMNS)

    edited_df = st.data_editor(
        df,
        key=f"lines_{record_id}",
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_config={
            "description": st.column_config.TextColumn("Description", width="large"),
            "qty": st.column_config.NumberColumn("Qty", format="%.2f"),
            "unit": st.column_config.TextColumn("Unit"),
            "unit_price": st.column_config.NumberColumn("Unit price ($)", format="%.2f"),
            "line_total": st.column_config.NumberColumn("Line total ($)", format="%.2f"),
            "check": st.column_config.CheckboxColumn("CHECK"),
            "check_reason": st.column_config.TextColumn("Why", width="large"),
        },
    )

    header = {
        "supplier": supplier,
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "department": department,
        "gst": gst,
        "invoice_total": invoice_total,
    }
    current_line_items = edited_df.to_dict("records")

    issues = validate_invoice(header, current_line_items)
    price_flags = storage.get_price_flags(supplier, current_line_items)

    st.write("")
    if (
        not issues["totals"]
        and not issues["gst"]
        and not issues["missing_fields"]
        and not issues["line_issues"]
        and not price_flags
    ):
        st.success("0 issues found - looks ready to save.")
    else:
        if issues["totals"]:
            message = issues["totals"]["message"].replace("$", r"\$")
            if issues["totals"]["level"] == "error":
                st.error(message)
            else:
                st.warning(message)

        if issues["gst"]:
            st.warning(issues["gst"].replace("$", r"\$"))

        if issues["missing_fields"]:
            with st.expander(f"{len(issues['missing_fields'])} field(s) missing"):
                for label in issues["missing_fields"]:
                    st.write(f"- {label}")

        if issues["line_issues"]:
            with st.expander(f"{len(issues['line_issues'])} line item(s) need confirming"):
                for item in issues["line_issues"]:
                    reason = item["reason"].replace("$", r"\$")
                    st.write(f"- **{item['description']}**: {reason}")

        if price_flags:
            with st.expander(f"{len(price_flags)} price change(s) over 3%"):
                for flag in price_flags:
                    st.write(f"- {flag['message'].replace('$', r'\$')}")

    st.write("")
    if st.session_state.get(f"saved_{record_id}", False):
        st.info(
            f"Saved. CSV written to csv_exports/{st.session_state['csv_name']}, "
            f"and added to exports/{st.session_state['workbook_name']}."
        )
        st.download_button(
            "Download CSV again",
            data=st.session_state["csv_bytes"],
            file_name=st.session_state["csv_name"],
            mime="text/csv",
        )
        if len(pending_files) > 0:
            st.caption(f"{len(pending_files)} more file(s) waiting - scroll up and click Extract for the next one.")
        else:
            st.caption("Upload another invoice to continue.")
    elif st.button("Save & export", type="primary"):
        storage.save_invoice(header, current_line_items, st.session_state["saved_image_name"])
        workbook_path = export.append_to_monthly_workbook(header, current_line_items)
        csv_path = export.save_invoice_csv(header, current_line_items, record_id)
        st.session_state["csv_bytes"] = csv_path.read_bytes()
        st.session_state["csv_name"] = csv_path.name
        st.session_state["workbook_name"] = workbook_path.name
        st.session_state[f"saved_{record_id}"] = True
        st.rerun()

st.divider()
st.subheader("Previously saved invoices")

if st.button("Open invoices folder"):
    try:
        os.startfile(str(IMAGES_DIR.resolve()))
        st.caption("Opened - check for a new Explorer window (it may have opened behind this one).")
    except Exception as e:
        st.error(f"Couldn't open the folder: {e}")
st.caption(f"Folder location: {IMAGES_DIR.resolve()}")

saved_invoices = storage.get_all_invoices()
if not saved_invoices:
    st.caption("No invoices saved yet.")
else:
    filter_col1, filter_col2 = st.columns(2)
    with filter_col1:
        suppliers = sorted({inv["supplier"] for inv in saved_invoices if inv["supplier"]})
        supplier_filter = st.selectbox("Filter by supplier", ["All"] + suppliers, key="browse_supplier_filter")
    with filter_col2:
        months = sorted(
            {d.strftime("%Y-%m") for d in (parse_date(inv["invoice_date"]) for inv in saved_invoices) if d},
            reverse=True,
        )
        month_filter = st.selectbox("Filter by month", ["All"] + months, key="browse_month_filter")

    filtered_invoices = [
        inv
        for inv in saved_invoices
        if (supplier_filter == "All" or inv["supplier"] == supplier_filter)
        and (
            month_filter == "All"
            or (parse_date(inv["invoice_date"]) and parse_date(inv["invoice_date"]).strftime("%Y-%m") == month_filter)
        )
    ]

    if not filtered_invoices:
        st.caption("No invoices match that filter.")
        st.stop()

    options = {}
    for inv in filtered_invoices:
        label = (
            f"{inv['invoice_date'] or '?'} - {inv['supplier'] or '?'} - "
            f"{inv['invoice_number'] or '?'} (${(inv['invoice_total'] or 0):.2f})"
        )
        options[f"{label} [{inv['id'][:8]}]"] = inv

    choice = st.selectbox(
        f"Choose an invoice to view ({len(filtered_invoices)})", list(options.keys()), key="browse_invoice_select"
    )
    if choice:
        inv = options[choice]
        view_col1, view_col2 = st.columns(2)
        with view_col1:
            img_path = IMAGES_DIR / inv["image_filename"] if inv["image_filename"] else None
            if img_path and img_path.exists():
                if img_path.suffix.lower() in (".jpg", ".jpeg", ".png"):
                    st.image(str(img_path), width="stretch")
                else:
                    st.download_button(
                        "Open original PDF",
                        data=img_path.read_bytes(),
                        file_name=img_path.name,
                        mime="application/pdf",
                        key=f"browse_pdf_{inv['id']}",
                    )
            else:
                st.caption("Original file not found.")
        with view_col2:
            items = storage.get_invoice_line_items(inv["id"])
            item_df = pd.DataFrame(items, columns=["description", "qty", "unit", "unit_price", "line_total"])
            st.dataframe(item_df, hide_index=True, width="stretch")
            st.caption(
                f"Department: {inv['department']} | GST: ${(inv['gst'] or 0):.2f} | "
                f"Total: ${(inv['invoice_total'] or 0):.2f}"
            )

        st.write("")
        if st.session_state.get(f"confirm_delete_{inv['id']}"):
            st.warning("Delete this saved invoice? This removes it from price history and reports (the original photo/PDF is kept).")
            confirm_col1, confirm_col2 = st.columns(2)
            with confirm_col1:
                if st.button("Yes, delete it", key=f"confirm_delete_yes_{inv['id']}", type="primary"):
                    storage.delete_invoice(inv["id"])
                    st.session_state[f"confirm_delete_{inv['id']}"] = False
                    st.rerun()
            with confirm_col2:
                if st.button("Cancel", key=f"confirm_delete_no_{inv['id']}"):
                    st.session_state[f"confirm_delete_{inv['id']}"] = False
                    st.rerun()
        elif st.button("Delete this invoice", key=f"delete_{inv['id']}"):
            st.session_state[f"confirm_delete_{inv['id']}"] = True
            st.rerun()
