from __future__ import annotations

import streamlit as st

from residency_scheduler.db import init_db
from residency_scheduler.repository import create_schedule_period, get_schedule_periods

st.set_page_config(
	page_title="Residency Scheduler",
	page_icon="📅",
	layout="wide",
)

init_db()

st.title("Residency Scheduler")
st.caption("Local-first monthly night-shift scheduler for medical residency programs.")

with st.sidebar:
	st.header("Current workflow")
	st.markdown(
		"""
		1. Create/select month  
		2. Maintain residents  
		3. Enter availability/preferences  
		4. Enter locked assignments  
		5. Generate schedule  
		6. Review and publish
		"""
	)

st.subheader("Schedule periods")

periods = get_schedule_periods()

if periods.empty:
	st.info("No schedule periods exist yet. Create one to start.")
else:
	st.dataframe(periods, use_container_width=True, hide_index=True)

with st.form("create_period"):
	st.markdown("### Create schedule period")
	cols = st.columns(4)
	year = cols[0].number_input("Year", min_value=2024, max_value=2100, value=2026, step=1)
	month = cols[1].number_input("Month", min_value=1, max_value=12, value=7, step=1)
	required_count = cols[2].number_input("Residents per night", min_value=1, max_value=10, value=1, step=1)
	calendar_id = cols[3].text_input("Google Calendar ID", value="")
	submitted = st.form_submit_button("Create period")

if submitted:
	period_id = create_schedule_period(
		year=int(year),
		month=int(month),
		required_count=int(required_count),
		google_calendar_id=calendar_id.strip() or None,
	)
	st.success(f"Created schedule period #{period_id}.")
	st.rerun()

st.markdown("---")
st.markdown(
	"""
	### MVP scope

	This version assumes one 6:00 PM–7:00 AM night shift per calendar day. The solver honors hard unavailable dates and locked assignments, then tries to balance total shifts, weekends, and preferences.
	"""
)
