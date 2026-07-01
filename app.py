from __future__ import annotations

import streamlit as st

from residency_scheduler.auth import require_google_auth
from residency_scheduler.cache import clear_month_data_cache, ensure_database_initialized, get_cached_period, preload_reference_data
from residency_scheduler.repository import update_schedule_period_settings
from residency_scheduler.ui import flash_error, flash_success, render_flash_messages, select_period

st.set_page_config(
	page_title="Residency Scheduler",
	page_icon="📅",
	layout="wide",
)

require_google_auth()
ensure_database_initialized()
preload_reference_data()

st.title("Residency Scheduler")
st.caption("Monthly night-shift scheduler for medical residency programs.")
render_flash_messages()

with st.sidebar:
	st.header("Current workflow")
	st.markdown(
		"""
		1. Select year-month  
		2. Maintain residents  
		3. Enter availability and preferences  
		4. Enter special rules  
		5. Generate, review, edit, and export
		"""
	)

st.subheader("Schedule month")
period_id = select_period("home")
period = get_cached_period(period_id)

cols = st.columns(4)
cols[0].metric("Year", int(period["year"]))
cols[1].metric("Month", f"{int(period['month']):02d}")
cols[2].metric("Schedule ID", int(period["id"]))
cols[3].metric("Residents/night", int(period["required_count"]))

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

st.markdown("---")
st.markdown(
	"""
	### MVP scope

	This version assumes one 6:00 PM-7:00 AM night shift per calendar day. The solver honors hard schedule requests and rules, then balances workload, weekends, preferences, and back-to-back shifts.
	"""
)
