from __future__ import annotations

import streamlit as st

from residency_scheduler.db import init_db
from residency_scheduler.repository import create_schedule_period, get_schedule_periods
from residency_scheduler.ui import select_draft, select_month

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
		1. Select year-month and draft  
		2. Maintain residents  
		3. Enter availability and preferences  
		4. Enter special rules  
		5. Generate, review, edit, and export
		"""
	)

st.subheader("Schedule drafts")
year, month = select_month("home")

drafts = get_schedule_periods(year=year, month=month)
if drafts.empty:
	st.info("No drafts exist for this month yet.")
else:
	select_draft(year, month, allow_empty=True, location="home")
	st.dataframe(
		drafts[["id", "draft_name", "year", "month", "required_count", "status", "created_at"]],
		use_container_width=True,
		hide_index=True,
	)

with st.form("create_draft"):
	st.markdown("### Create draft")
	cols = st.columns(2)
	draft_name = cols[0].text_input("Draft name", value=f"Draft {len(drafts) + 1}")
	required_count = cols[1].number_input("Residents per night", min_value=1, max_value=10, value=1, step=1)
	submitted = st.form_submit_button("Create draft", type="primary")

if submitted:
	try:
		period_id = create_schedule_period(
			year=year,
			month=month,
			draft_name=draft_name,
			required_count=int(required_count),
			google_calendar_id=None,
		)
	except ValueError as exc:
		st.error(str(exc))
	else:
		st.success(f"Created {draft_name.strip() or 'Draft 1'} as draft #{period_id}.")
		st.rerun()

st.markdown("---")
st.markdown(
	"""
	### MVP scope

	This version assumes one 6:00 PM-7:00 AM night shift per calendar day. The solver honors hard schedule requests and rules, then balances workload, weekends, preferences, and back-to-back shifts.
	"""
)
