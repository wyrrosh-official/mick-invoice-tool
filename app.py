import os

import streamlit as st
from dotenv import load_dotenv

import storage

load_dotenv(override=True)

# Bridge Streamlit Cloud's secrets.toml into os.environ so extractor.py /
# cost_tracker.py can keep reading plain env vars either way. setdefault
# means a local .env value always wins over anything in st.secrets.
# st.secrets raises if no secrets.toml exists anywhere, which is the normal
# case for local dev with just a .env file.
try:
    for _key, _value in st.secrets.items():
        os.environ.setdefault(_key, str(_value))
except Exception:
    pass

st.set_page_config(page_title="Invoice Assistant", page_icon=":material/inventory:", layout="wide")

app_password = os.environ.get("APP_PASSWORD", "").strip()
if app_password and not st.session_state.get("authenticated"):
    st.title("Invoice Assistant")
    entered = st.text_input("Password", type="password")
    if st.button("Enter", type="primary"):
        if entered == app_password:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Wrong password.")
    st.stop()

storage.backup_if_needed()

with st.sidebar:
    st.markdown("## Invoice Assistant")
    checks = storage.count_unresolved_checks()
    if checks["total"] > 0:
        st.badge(f"{checks['total']} item(s) need attention", icon=":material/warning:", color="orange")
        with st.expander("What needs attention?"):
            unresolved = storage.get_unresolved_checks()
            for item in unresolved["invoices"]:
                reason = (item["check_reason"] or "flagged for review").replace("$", r"\$")
                st.caption(
                    f"**{item['description']}** - {reason}  \n"
                    f"Invoice: {item['supplier'] or '?'} {item['invoice_number'] or '?'} "
                    f"({item['invoice_date'] or '?'})"
                )
            for item in unresolved["stocktake"]:
                reason = (item["check_reason"] or "flagged for review").replace("$", r"\$")
                st.caption(
                    f"**{item['description']}** - {reason}  \n"
                    f"Stocktake: {item['department'] or '?'} ({item['count_date'] or '?'})"
                )

page = st.navigation(
    [
        st.Page("app_pages/invoices.py", title="Invoices", icon=":material/receipt_long:"),
        st.Page("app_pages/stocktake.py", title="Stocktake", icon=":material/inventory_2:"),
        st.Page("app_pages/reports.py", title="Reports", icon=":material/bar_chart:"),
        st.Page("app_pages/api_usage.py", title="API usage", icon=":material/monitoring:"),
    ],
    position="sidebar",
)

st.title(page.title, icon=page.icon)
page.run()
