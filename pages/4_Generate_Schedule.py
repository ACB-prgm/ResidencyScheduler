from __future__ import annotations

import streamlit as st
from streamlit_calendar import calendar

from residency_scheduler.calendar.ical import build_fullcalendar_events, build_ical_calendar
from residency_scheduler.db import init_db
from residency_scheduler.repository import (
	get_assignments,
	get_period,
	get_preference_violations,
	get_resident_options,
	get_residents,
	get_schedule_runs,
	get_workload_summary,
	update_assignment_resident,
)
from residency_scheduler.solver import solve_period
from residency_scheduler.ui import select_period

init_db()

st.title("Generate Schedule")
st.caption("Run, review, edit, and export the selected draft.")

period_id = select_period("generate")
period = get_period(period_id)
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
	st.markdown("### Calendar")
	calendar(
		events=build_fullcalendar_events(assignments),
		options={
			"initialView": "dayGridMonth",
			"initialDate": f"{int(period['year'])}-{int(period['month']):02d}-01",
			"height": "auto",
			"editable": False,
			"selectable": False,
			"headerToolbar": {
				"left": "prev,next today",
				"center": "title",
				"right": "dayGridMonth,listMonth",
			},
		},
		key=f"assignment_calendar_{period_id}",
	)

	st.markdown("### Assignment rows")
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
	if residents.empty or editable_assignments.empty:
		st.info("No unlocked assignments are available for manual reassignment.")
	else:
		assignment_options = {
			f"{row.work_date} · {row.resident_name} · assignment #{row.id}": int(row.id)
			for row in editable_assignments.itertuples()
		}
		resident_options = get_resident_options(active_only=True)

		with st.form("manual_reassignment"):
			assignment_label = st.selectbox("Assignment", list(assignment_options.keys()))
			resident_label = st.selectbox("New resident", list(resident_options.keys()))
			make_locked = st.checkbox("Create hard assign request from this edit")
			lock_reason = st.text_input("Reason", value="Manual review edit")
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

	st.markdown("### Export calendar")
	download_name = f"residency-schedule-{int(period['year'])}-{int(period['month']):02d}-draft-{period_id}.ics"
	st.download_button(
		"Download ICS file",
		data=build_ical_calendar(assignments),
		file_name=download_name,
		mime="text/calendar",
		type="primary",
	)
else:
	st.info("No assignments have been generated for this draft yet.")

runs = get_schedule_runs(period_id)
if not runs.empty:
	st.markdown("### Recent solver runs")
	st.dataframe(runs.head(5), use_container_width=True, hide_index=True)
