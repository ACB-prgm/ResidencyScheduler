from __future__ import annotations

import streamlit as st
from streamlit_calendar import calendar

from residency_scheduler.calendar.ical import build_fullcalendar_events, build_pgy_ical_zip
from residency_scheduler.db import init_db
from residency_scheduler.repository import (
	get_assignments,
	get_period,
	get_preference_violations,
	get_resident_options,
	get_residents,
	get_schedule_runs,
	get_workload_summary,
	swap_assignment_residents,
	update_assignment_resident,
)
from residency_scheduler.solver import solve_period
from residency_scheduler.ui import flash_error, flash_success, flash_warning, render_flash_messages, select_period

init_db()

st.title("Generate Schedule")
st.caption("Run, review, edit, and export the selected draft.")
render_flash_messages()

period_id = select_period("generate")
period = get_period(period_id)
max_time = st.slider("Solver max time, seconds", min_value=5, max_value=120, value=30, step=5)

if st.button("Run scheduler", type="primary"):
	with st.spinner("Generating schedule..."):
		result = solve_period(period_id, max_time_seconds=max_time)

	if result.assignments:
		flash_success(f"Solver status: {result.status}. Objective score: {result.objective_score}.")
	else:
		flash_error(f"Solver status: {result.status}.")

	for warning in result.warnings:
		flash_warning(warning)
	st.rerun()

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

	st.markdown("### Workload summary")
	summary = get_workload_summary(period_id)
	st.dataframe(summary, use_container_width=True, hide_index=True)

	st.markdown("### Preference violations")
	violations = get_preference_violations(period_id)
	if violations.empty:
		st.success("No prefer-off violations in the current schedule.")
	else:
		st.dataframe(violations, use_container_width=True, hide_index=True)

	st.markdown("### Manual edit")
	residents = get_residents(active_only=True)
	editable_assignments = assignments[assignments["is_locked"].astype(int) == 0]
	if residents.empty or editable_assignments.empty:
		st.info("No unlocked assignments are available for manual edits.")
	else:
		assignment_options = {
			f"{row.work_date} · {row.resident_name} · assignment #{row.id}": int(row.id)
			for row in editable_assignments.itertuples()
		}
		assignments_by_id = {int(row.id): row for row in editable_assignments.itertuples()}
		resident_options = get_resident_options(active_only=True)
		mode = st.radio("Edit mode", ["Reassign", "Swap"], horizontal=True)
		make_locked = st.checkbox("Create hard assign request from this edit")
		lock_reason = st.text_input("Reason", value="Manual review edit")

		if mode == "Reassign":
			assignment_label = st.selectbox("Assignment", list(assignment_options.keys()), key="reassign_assignment")
			assignment_id = assignment_options[assignment_label]
			current_resident_id = int(assignments_by_id[assignment_id].resident_id)
			filtered_resident_options = {
				label: resident_id
				for label, resident_id in resident_options.items()
				if int(resident_id) != current_resident_id
			}
			if not filtered_resident_options:
				st.warning("No alternate active resident is available for reassignment.")
			else:
				resident_label = st.selectbox("New resident", list(filtered_resident_options.keys()), key="reassign_resident")
				if st.button("Save reassignment", type="primary"):
					try:
						update_assignment_resident(
							assignment_id,
							filtered_resident_options[resident_label],
							make_locked=make_locked,
							lock_reason=lock_reason,
						)
					except ValueError as exc:
						st.error(str(exc))
					else:
						flash_success("Reassignment saved.")
						st.rerun()
		else:
			from_label = st.selectbox("From assignment", list(assignment_options.keys()), key="swap_from_assignment")
			from_assignment_id = assignment_options[from_label]
			from_resident_id = int(assignments_by_id[from_assignment_id].resident_id)
			to_options = {
				label: assignment_id
				for label, assignment_id in assignment_options.items()
				if assignment_id != from_assignment_id
				and int(assignments_by_id[assignment_id].resident_id) != from_resident_id
			}
			if not to_options:
				st.warning("No swap targets are available with a different resident.")
			else:
				to_label = st.selectbox("To assignment", list(to_options.keys()), key="swap_to_assignment")
				if st.button("Save swap", type="primary"):
					try:
						swap_assignment_residents(
							from_assignment_id,
							to_options[to_label],
							make_locked=make_locked,
							lock_reason=lock_reason,
						)
					except ValueError as exc:
						st.error(str(exc))
					else:
						flash_success("Swap saved.")
						st.rerun()

	st.markdown("### Export calendar")
	download_name = f"{int(period['year'])}-{int(period['month']):02d}-PGY-calendars.zip"
	st.download_button(
		"Download PGY calendar ZIP",
		data=build_pgy_ical_zip(assignments, int(period["year"]), int(period["month"])),
		file_name=download_name,
		mime="application/zip",
		type="primary",
	)
else:
	st.info("No assignments have been generated for this draft yet.")

runs = get_schedule_runs(period_id)
if not runs.empty:
	st.markdown("### Recent solver runs")
	st.dataframe(runs.head(5), use_container_width=True, hide_index=True)
