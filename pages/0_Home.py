from __future__ import annotations

import calendar

import streamlit as st

from residency_scheduler.cache import (
	clear_month_data_cache,
	get_cached_assignments,
	get_cached_period,
	get_cached_residents,
)
from residency_scheduler.repository import update_schedule_period_settings
from residency_scheduler.ui import flash_error, flash_success, render_page_header, render_user_guide

period_id = render_page_header(
	"Residency Call Scheduler",
	"Configure the active schedule month and review month-level status.",
	month_location="home",
)

render_user_guide(
	"Home",
	"""
	### Overview
	The Residency Call Scheduler builds one call schedule for the selected year-month. It uses the resident roster, availability and preferences, scheduling rules, and recent prior schedules to create balanced assignments.

	### Current workflow
	1. Select the year-month you want to edit.
	2. Maintain the active resident roster.
	3. Enter availability, preferences, vacations, and hard preassignments.
	4. Add scheduling rules for month-specific constraints.
	5. Generate the schedule, review workload and violations, make manual edits if needed, then publish or export.

	### Key definitions
	- **Year-month:** the schedule period currently being edited.
	- **Resident:** a person eligible for call assignments.
	- **Availability/preference:** a dated entry such as vacation, unavailable, prefer off, prefer work, or assign.
	- **Scheduling rule:** a month-specific rule such as weekday counts, weekday pairs, or away rotation.
	- **Generated assignment:** a solver-created call assignment for a resident on a work date.
	- **Publish/export:** publishing writes app-generated events to Google Calendar; export downloads an ICS calendar file.
	- **Hard:** must be honored by the solver.
	- **Soft:** affects the solver score but can be violated when needed.

	### How this works
	This version assumes one 6:00 PM-7:00 AM night shift per calendar day. The solver honors hard availability, preferences, and rules first, then balances total workload, Friday/Saturday/Sunday shifts, preferences, rolling fairness, and back-to-back shifts.
	""",
	expanded=True,
)

period = get_cached_period(period_id)
assignments = get_cached_assignments(period_id)
active_residents = get_cached_residents(active_only=True)
nights_in_month = calendar.monthrange(int(period["year"]), int(period["month"]))[1]
total_assignments = nights_in_month * int(period["required_count"])

cols = st.columns(4)
cols[0].metric("Nights in month", nights_in_month)
cols[1].metric("Residents/night", int(period["required_count"]))
cols[2].metric("Total assignments", total_assignments)
cols[3].metric("Active residents", len(active_residents))

with st.expander("Month settings"):
	with st.form("month_settings"):
		required_count = st.number_input(
			"Residents per night",
			min_value=1,
			max_value=10,
			value=int(period["required_count"]),
			step=1,
		)
		submitted = st.form_submit_button("Save month settings", type="primary")

if submitted:
	try:
		update_schedule_period_settings(int(period_id), int(required_count), period.get("google_calendar_id"))
	except ValueError as exc:
		flash_error(str(exc))
	else:
		clear_month_data_cache()
		flash_success("Month settings saved.")
	st.rerun()

if not assignments.empty:
	st.caption(f"{len(assignments)} assignment(s) currently generated for this month.")

with st.expander("Developer details"):
	st.write({"period_id": int(period["id"]), "year": int(period["year"]), "month": int(period["month"])})
