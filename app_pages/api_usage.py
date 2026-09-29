import pandas as pd
import streamlit as st

import cost_tracker
import storage

st.write("Live view of what this tool is spending on the Claude API.")

stats = cost_tracker.get_monthly_stats()
cap = cost_tracker.get_monthly_cap()
spent = stats["estimated_cost_usd"]

with st.container(horizontal=True):
    st.metric("Spent this month", f"${spent:.2f}", border=True)
    st.metric("Monthly cap", f"${cap:.2f}", border=True)
    st.metric("Calls this month", str(stats["calls"]), border=True)
    remaining = max(cap - spent, 0)
    st.metric("Remaining this month", f"${remaining:.2f}", border=True)

st.progress(min(spent / cap, 1.0) if cap > 0 else 0.0, text=f"${spent:.2f} of ${cap:.2f} used this month")

if spent >= cap:
    st.warning("Monthly cap reached - new extractions are paused until next month (or raise the cap in .env).")

st.subheader("Recent calls", icon=":material/monitoring:")

calls = storage.get_recent_api_calls(limit=100)
if not calls:
    st.caption("No API calls yet - extract an invoice or stocktake sheet to see activity here.")
else:
    df = pd.DataFrame(calls)
    df["status"] = df["success"].map({1: "OK", 0: "Failed"})
    df = df[["created_at", "kind", "filename", "input_tokens", "output_tokens", "cost_usd", "status"]]
    st.dataframe(
        df,
        hide_index=True,
        width="stretch",
        column_config={
            "created_at": st.column_config.DatetimeColumn("When", format="D MMM, h:mm a"),
            "kind": st.column_config.TextColumn("Type"),
            "filename": st.column_config.TextColumn("File"),
            "input_tokens": st.column_config.NumberColumn("Input tokens"),
            "output_tokens": st.column_config.NumberColumn("Output tokens"),
            "cost_usd": st.column_config.NumberColumn("Cost ($)", format="%.4f"),
            "status": st.column_config.TextColumn("Status"),
        },
    )

    recent_costs = list(reversed([c["cost_usd"] for c in calls[:20]]))
    st.metric("Last 20 calls - cost trend", f"${sum(recent_costs):.4f}", chart_data=recent_costs, chart_type="bar")
