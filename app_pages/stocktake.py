import uuid
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

import storage
from extractor import CostCapExceeded, extract_stocktake
from validation import validate_stocktake

IMAGES_DIR = Path(__file__).parent.parent / "data" / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

DEPARTMENTS = ["bar", "kitchen", "other", "mixed", "unclear"]
ROW_COLUMNS = ["department", "description", "counted_qty", "unit", "check", "check_reason"]

st.write(
    "Photograph or upload your stocktake count sheets, then click Extract. "
    "You can add more sheets before saving."
)

count_date = st.date_input("Stocktake date", value=date.today(), key="stocktake_count_date")

uploaded_files = st.file_uploader(
    "Stocktake sheets",
    type=["jpg", "jpeg", "png", "pdf"],
    accept_multiple_files=True,
    label_visibility="collapsed",
)

if "stocktake_rows" not in st.session_state:
    st.session_state["stocktake_rows"] = []
if "stocktake_processed_ids" not in st.session_state:
    st.session_state["stocktake_processed_ids"] = set()
if "stocktake_source_images" not in st.session_state:
    st.session_state["stocktake_source_images"] = []

if uploaded_files:
    new_files = [f for f in uploaded_files if f.file_id not in st.session_state["stocktake_processed_ids"]]
    if new_files and st.button(f"Extract {len(new_files)} new sheet(s)", type="primary", key="extract_stocktake_btn"):
        progress = st.progress(0.0)
        total_cost = 0.0
        success_count = 0
        failures = []
        for i, f in enumerate(new_files):
            file_bytes = f.getvalue()
            try:
                extraction, call_cost = extract_stocktake(file_bytes, f.name)
            except CostCapExceeded as e:
                st.error(str(e))
                break
            except Exception as e:
                failures.append((f.name, str(e)))
                progress.progress((i + 1) / len(new_files))
                continue

            total_cost += call_cost
            success_count += 1
            saved_name = f"{uuid.uuid4().hex}_{f.name}"
            (IMAGES_DIR / saved_name).write_bytes(file_bytes)
            st.session_state["stocktake_source_images"].append(saved_name)

            for item in extraction.items:
                st.session_state["stocktake_rows"].append(
                    {
                        "department": extraction.department,
                        "description": item.description,
                        "counted_qty": item.counted_qty,
                        "unit": item.unit,
                        "check": item.check,
                        "check_reason": item.check_reason,
                    }
                )

            st.session_state["stocktake_processed_ids"].add(f.file_id)
            progress.progress((i + 1) / len(new_files))

        if success_count:
            st.success(f"Extracted {success_count} sheet(s) for about ${total_cost:.4f} total.")
        for name, err in failures:
            st.error(f"Couldn't read {name}: {err}")

if st.session_state["stocktake_rows"]:
    st.divider()
    st.subheader("Review counted stock")

    if st.session_state["stocktake_source_images"]:
        with st.expander(f"View uploaded sheets ({len(st.session_state['stocktake_source_images'])})"):
            for name in st.session_state["stocktake_source_images"]:
                sheet_path = IMAGES_DIR / name
                if sheet_path.suffix.lower() in (".jpg", ".jpeg", ".png"):
                    st.image(str(sheet_path), caption=name, width="stretch")
                else:
                    st.download_button(
                        f"Open {name}",
                        data=sheet_path.read_bytes(),
                        file_name=name,
                        mime="application/pdf",
                        key=f"view_pdf_{name}",
                    )

    df = pd.DataFrame(st.session_state["stocktake_rows"], columns=ROW_COLUMNS)
    edited_df = st.data_editor(
        df,
        key="stocktake_editor",
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_config={
            "department": st.column_config.SelectboxColumn("Department", options=DEPARTMENTS),
            "description": st.column_config.TextColumn("Description", width="large"),
            "counted_qty": st.column_config.NumberColumn("Counted qty", format="%.2f"),
            "unit": st.column_config.TextColumn("Unit"),
            "check": st.column_config.CheckboxColumn("CHECK"),
            "check_reason": st.column_config.TextColumn("Why", width="large"),
        },
    )

    current_rows = edited_df.to_dict("records")

    valued_rows = []
    total_value = 0.0
    by_department = {}
    for row in current_rows:
        description = (row.get("description") or "").strip()
        history = storage.get_latest_price_for_description(description) if description else None
        unit_price = history["unit_price"] if history else None

        qty = row.get("counted_qty")
        try:
            qty_f = float(qty) if qty not in (None, "") else None
        except (TypeError, ValueError):
            qty_f = None

        value = round(qty_f * unit_price, 2) if (qty_f is not None and unit_price is not None) else None

        valued_row = dict(row)
        valued_row["unit_price"] = unit_price
        valued_row["value"] = value
        valued_rows.append(valued_row)

        if value is not None:
            total_value += value
            dept = row.get("department") or "unclear"
            by_department[dept] = by_department.get(dept, 0.0) + value

    st.write("Valued stock (based on the last known invoice price for each item)")
    value_df = pd.DataFrame(
        valued_rows, columns=["department", "description", "counted_qty", "unit", "unit_price", "value"]
    )
    st.dataframe(
        value_df,
        hide_index=True,
        width="stretch",
        column_config={
            "unit_price": st.column_config.NumberColumn("Last known unit price ($)", format="%.2f"),
            "value": st.column_config.NumberColumn("Value ($)", format="%.2f"),
        },
    )

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Total counted stock value", f"${total_value:,.2f}")
    with col2:
        st.write("By department:")
        for dept, val in sorted(by_department.items()):
            st.write(f"- {dept}: ${val:,.2f}")

    issues = validate_stocktake(valued_rows)
    st.write("")
    if not issues["blank_count"] and not issues["needs_confirming"]:
        st.success("0 issues found - looks ready to save.")
    else:
        if issues["blank_count"]:
            with st.expander(f"{len(issues['blank_count'])} item(s) have a blank count box"):
                for description in issues["blank_count"]:
                    st.write(f"- {description}")
        if issues["needs_confirming"]:
            with st.expander(f"{len(issues['needs_confirming'])} item(s) need confirming"):
                for item in issues["needs_confirming"]:
                    reason = item["reason"].replace("$", r"\$")
                    st.write(f"- **{item['description']}**: {reason}")

    if issues["no_price_history"]:
        with st.expander(f"{len(issues['no_price_history'])} item(s) have no price history yet (won't be valued)"):
            for description in issues["no_price_history"]:
                st.write(f"- {description}")

    st.write("")
    if st.session_state.get("stocktake_saved"):
        st.info(f"Stocktake saved ({st.session_state['stocktake_saved_count']} items).")
        if st.button("Start a new stocktake"):
            st.session_state["stocktake_saved"] = False
            st.session_state["stocktake_rows"] = []
            st.session_state["stocktake_processed_ids"] = set()
            st.session_state["stocktake_source_images"] = []
            st.rerun()
    elif st.button("Save stocktake", type="primary"):
        storage.save_stocktake(str(count_date), valued_rows, st.session_state["stocktake_source_images"])
        st.session_state["stocktake_saved"] = True
        st.session_state["stocktake_saved_count"] = len(valued_rows)
        st.rerun()

st.divider()
st.subheader("Previously saved stocktakes")

saved_stocktakes = storage.get_all_stocktakes()
if not saved_stocktakes:
    st.caption("No stocktakes saved yet.")
else:
    options = {
        f"{st_.get('count_date') or '?'} - {st_.get('item_count', 0)} items (${(st_.get('total_value') or 0):,.2f}) [{st_['id'][:8]}]": st_
        for st_ in saved_stocktakes
    }
    choice = st.selectbox("Choose a stocktake to view", list(options.keys()), key="browse_stocktake_select")
    if choice:
        picked = options[choice]
        items = storage.get_stocktake_items(picked["id"])
        item_df = pd.DataFrame(
            items, columns=["department", "description", "counted_qty", "unit", "unit_price", "value"]
        )
        st.dataframe(
            item_df,
            hide_index=True,
            width="stretch",
            column_config={
                "unit_price": st.column_config.NumberColumn("Unit price ($)", format="%.2f"),
                "value": st.column_config.NumberColumn("Value ($)", format="%.2f"),
            },
        )

        if picked.get("source_images"):
            with st.expander("View sheets for this stocktake"):
                for name in picked["source_images"].split(","):
                    if not name:
                        continue
                    sheet_path = IMAGES_DIR / name
                    if sheet_path.exists() and sheet_path.suffix.lower() in (".jpg", ".jpeg", ".png"):
                        st.image(str(sheet_path), caption=name, width="stretch")

        st.write("")
        if st.session_state.get(f"confirm_delete_st_{picked['id']}"):
            st.warning("Delete this saved stocktake? (The original sheets are kept.)")
            confirm_col1, confirm_col2 = st.columns(2)
            with confirm_col1:
                if st.button("Yes, delete it", key=f"confirm_delete_st_yes_{picked['id']}", type="primary"):
                    storage.delete_stocktake(picked["id"])
                    st.session_state[f"confirm_delete_st_{picked['id']}"] = False
                    st.rerun()
            with confirm_col2:
                if st.button("Cancel", key=f"confirm_delete_st_no_{picked['id']}"):
                    st.session_state[f"confirm_delete_st_{picked['id']}"] = False
                    st.rerun()
        elif st.button("Delete this stocktake", key=f"delete_st_{picked['id']}"):
            st.session_state[f"confirm_delete_st_{picked['id']}"] = True
            st.rerun()
