from __future__ import annotations

import streamlit as st

from residency_scheduler.db import init_db
from residency_scheduler.repository import (
	get_assignments,
	get_preference_violations,
	get_residents,
	get_schedule_periods,
	get_workload_summary,
	update_assignment_resident,
)

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

st.markdown("### Workload summary")
summary = get_workload_summary(period_id)
st.dataframe(summary, use_container_width=True, hide_index=True)

st.markdown("### Preference violations")
violations = get_preference_violations(period_id)
if violations.empty:
	st.success("No prefer-off violations in the current schedule.")
else:
	st.dataframe(violations, use_container_width=True, hide_index=True)

st.markdown("### Manual reassignment")
residents = get_residents(active_only=True)
editable_assignments = assignments[assignments["is_locked"].astype(int) == 0]
if editable_assignments.empty:
	st.info("No unlocked assignments are available for manual reassignment.")
else:
	assignment_options = {
		f"{row.work_date} · {row.resident_name} · assignment #{row.id}": int(row.id)
		for row in editable_assignments.itertuples()
	}
	resident_options = {f"{row.name} · resident #{row.id}": int(row.id) for row in residents.itertuples()}

	with st.form("manual_reassignment"):
		assignment_label = st.selectbox("Assignment", list(assignment_options.keys()))
		resident_label = st.selectbox("New resident", list(resident_options.keys()))
		make_locked = st.checkbox("Lock this manual edit")
		lock_reason = st.text_input("Lock reason", value="Manual review edit")
		submitted = st.form_submit_button("Save manual edit", type="primary")

	if submitted:
		try:
			update_assignment_resident(
				assignment_options[assignment_label],
				resident_options[resident_label],
				make_locked=make_locked,
				lock_reason=lock_reason,
			)
		except ValueError as exc:
			st.error(str(exc))
		else:
			st.success("Manual edit saved.")
			st.rerun()
