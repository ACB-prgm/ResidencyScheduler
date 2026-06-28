from __future__ import annotations

import streamlit as st

from residency_scheduler.db import init_db
from residency_scheduler.repository import get_assignments, get_schedule_periods

init_db()

st.title("Review")

periods = get_schedule_periods()
if periods.empty:
	st.warning("Create a period first.")
	st.stop()

options = {f"{row.year}-{row.month:02d} · #{row.id}": int(row.id) for row in periods.itertuples()}
period_id = options[st.selectbox("Period", list(options.keys()))]

assignments = get_assignments(period_id)
if assignments.empty:
	st.info("No generated assignments yet.")
	st.stop()

st.dataframe(assignments, use_container_width=True, hide_index=True)

summary = assignments.groupby("resident_name").size().reset_index(name="total_shifts")
st.dataframe(summary, use_container_width=True, hide_index=True)
