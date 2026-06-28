from __future__ import annotations

import streamlit as st

from residency_scheduler.db import init_db
from residency_scheduler.repository import get_assignments, get_schedule_periods, get_schedule_runs
from residency_scheduler.solver import solve_period

init_db()

st.title("Generate Schedule")
st.caption("Run the OR-Tools scheduler for a selected period.")

periods = get_schedule_periods()

if periods.empty:
	st.warning("Create a schedule period on the home page first.")
	st.stop()

period_options = {
	f"{row.year}-{row.month:02d} · period #{row.id}": int(row.id)
	for row in periods.itertuples()
}
period_label = st.selectbox("Schedule period", options=list(period_options.keys()))
period_id = period_options[period_label]

max_time = st.slider("Solver max time, seconds", min_value=5, max_value=120, value=30, step=5)

if st.button("Run scheduler", type="primary"):
	with st.spinner("Generating schedule..."):
		result = solve_period(period_id, max_time_seconds=max_time)

	if result.assignments:
		st.success(f"Solver status: {result.status}. Objective score: {result.objective_score}.")
	else:
		st.error(f"Solver status: {result.status}.")

	for warning in result.warnings:
		st.warning(warning)

assignments = get_assignments(period_id)
if not assignments.empty:
	st.markdown("### Current generated assignments")
	st.dataframe(assignments, use_container_width=True, hide_index=True)
else:
	st.info("No assignments have been generated for this period yet.")

runs = get_schedule_runs(period_id)
if not runs.empty:
	st.markdown("### Recent solver runs")
	st.dataframe(runs.head(5), use_container_width=True, hide_index=True)
