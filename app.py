from __future__ import annotations

import streamlit as st

from residency_scheduler.db import init_db
from residency_scheduler.repository import create_schedule_period, delete_schedule_period, get_schedule_periods, rename_schedule_period
from residency_scheduler.ui import clear_active_draft, flash_error, flash_success, render_flash_messages, select_draft, select_month

st.set_page_config(
	page_title="Residency Scheduler",
	page_icon="📅",
	layout="wide",
)

init_db()

st.title("Residency Scheduler")
st.caption("Local-first monthly night-shift scheduler for medical residency programs.")
render_flash_messages()

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
selected_period_id = None
if drafts.empty:
	st.info("No drafts exist for this month yet.")
else:
	selected_period_id = select_draft(year, month, allow_empty=True, location="home")
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
	create_submitted = st.form_submit_button("Create draft", type="primary")

if create_submitted:
	try:
		period_id = create_schedule_period(
			year=year,
			month=month,
			draft_name=draft_name,
			required_count=int(required_count),
			google_calendar_id=None,
		)
	except ValueError as exc:
		flash_error(str(exc))
	else:
		flash_success(f"Created {draft_name.strip() or 'Draft 1'} as draft #{period_id}.")
	st.rerun()

if selected_period_id is not None:
	selected_draft = drafts[drafts["id"].astype(int) == int(selected_period_id)].iloc[0]
	with st.form("rename_draft"):
		st.markdown("### Rename selected draft")
		new_draft_name = st.text_input("Draft name", value=str(selected_draft["draft_name"]))
		rename_submitted = st.form_submit_button("Rename draft")

	if rename_submitted:
		try:
			rename_schedule_period(int(selected_period_id), new_draft_name)
		except ValueError as exc:
			flash_error(str(exc))
		else:
			flash_success("Draft renamed.")
		st.rerun()

	with st.form("delete_draft"):
		st.markdown("### Delete selected draft")
		st.warning("Deleting a draft permanently removes its requests, rules, assignments, and solver runs.")
		confirm_delete = st.checkbox(f"Permanently delete draft #{int(selected_period_id)}")
		delete_submitted = st.form_submit_button("Delete draft")

	if delete_submitted:
		if not confirm_delete:
			flash_error("Confirm deletion before deleting the draft.")
		else:
			try:
				delete_schedule_period(int(selected_period_id))
			except ValueError as exc:
				flash_error(str(exc))
			else:
				clear_active_draft()
				flash_success("Draft deleted.")
		st.rerun()

st.markdown("---")
st.markdown(
	"""
	### MVP scope

	This version assumes one 6:00 PM-7:00 AM night shift per calendar day. The solver honors hard schedule requests and rules, then balances workload, weekends, preferences, and back-to-back shifts.
	"""
)
