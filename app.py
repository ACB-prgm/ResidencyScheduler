from __future__ import annotations

import calendar

import streamlit as st

from residency_scheduler.auth import require_google_auth
from residency_scheduler.cache import (
	clear_month_data_cache,
	ensure_database_initialized,
	get_cached_assignments,
	get_cached_period,
	get_cached_residents,
	preload_reference_data,
)
from residency_scheduler.repository import update_schedule_period_settings
from residency_scheduler.ui import flash_error, flash_success, render_page_header

st.set_page_config(
	page_title="Schedule Month",
	page_icon="📅",
	layout="wide",
)

require_google_auth()
ensure_database_initialized()
preload_reference_data()

period_id = render_page_header(
	"Schedule Month",
	"Configure the active schedule month and review month-level status.",
	month_location="home",
)

with st.sidebar:
	st.header("Current workflow")
	st.markdown(
		"""
		1. Select year-month  
		2. Maintain residents  
		3. Enter availability and preferences  
		4. Enter scheduling rules  
		5. Generate, review, edit, and export
		"""
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

with st.form("month_settings"):
	st.markdown("### Month settings")
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

with st.expander("Scheduling assumptions"):
	st.markdown(
		"""
		This version assumes one 6:00 PM-7:00 AM night shift per calendar day. The solver honors hard schedule requests and rules, then balances workload, weekends, preferences, and back-to-back shifts.
		"""
	)

with st.expander("Developer details"):
	st.write({"period_id": int(period["id"]), "year": int(period["year"]), "month": int(period["month"])})
