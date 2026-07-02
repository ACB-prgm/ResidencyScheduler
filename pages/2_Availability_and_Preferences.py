from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from residency_scheduler.auth import require_google_auth
from residency_scheduler.cache import (
	clear_month_data_cache,
	ensure_database_initialized,
	get_cached_period,
	get_cached_resident_options,
	get_cached_residents,
	get_cached_schedule_requests_for_editor,
	preload_reference_data,
)
from residency_scheduler.repository import (
	default_priority_for_request_type,
	replace_schedule_requests,
)
from residency_scheduler.ui import flash_success, render_page_header, render_user_guide

require_google_auth()
ensure_database_initialized()
preload_reference_data()

period_id = render_page_header(
	"Availability and Preferences",
	"Enter availability, preferences, vacation ranges, and hard preassignments for the selected month.",
	month_location="requests",
)
render_user_guide(
	"Availability and Preferences",
	"""
	Use this page to add dated availability and preference entries for the selected month.

	- **Availability type:** vacation, unavailable, approved absence, medical leave, prefer off, prefer work, or assign.
	- **Resident:** the resident the entry applies to.
	- **Start/End date:** one date or a date range. The date must be inside the selected month.
	- **Priority:** hard entries must be honored; soft entries affect the solver score but can be violated if needed.
	- **Reason:** optional note shown with the entry.

	Vacation, unavailable, approved absence, medical leave, and assign default to hard. Prefer off and prefer work default to soft. Vacation ranges automatically add a soft prefer-work preference on the Thursday before vacation starts when that Thursday is inside the selected month.

	The Current Availability list shows saved entries for the month. Use **Delete** on an entry to remove it.
	""",
)
residents = get_cached_residents(active_only=True)

if residents.empty:
	st.warning("Add active residents before entering availability and preferences.")
	st.stop()

resident_options = get_cached_resident_options(active_only=True)
request_type_options = ["vacation", "unavailable", "approved_absence", "medical_leave", "prefer_off", "prefer_work", "assign"]
period = get_cached_period(period_id)
default_request_date = date(int(period["year"]), int(period["month"]), 1)

existing = get_cached_schedule_requests_for_editor(period_id)
metric_cols = st.columns(3)
metric_cols[0].metric("Active residents", len(residents))
metric_cols[1].metric("Active availability", len(existing))
metric_cols[2].metric("Availability types", int(existing["request_type"].nunique()) if not existing.empty else 0)

form_col, availability_col = st.columns([1, 1], gap="large")

with form_col:
	st.subheader("+ Add Availability")
	selected_request_type = st.selectbox("Availability type", request_type_options, key=f"request_type_{period_id}")
	default_priority = default_priority_for_request_type(selected_request_type)
	selected_resident = st.selectbox("Resident", list(resident_options.keys()), key=f"request_resident_{period_id}")
	start_date = st.date_input("Start date", value=default_request_date, key=f"request_start_{period_id}")
	end_date = st.date_input("End date", value=default_request_date, key=f"request_end_{period_id}")
	priority = st.selectbox(
		"Priority",
		["hard", "soft"],
		index=["hard", "soft"].index(default_priority),
		key=f"request_priority_{period_id}_{selected_request_type}",
	)
	reason = st.text_input("Reason", key=f"request_reason_{period_id}")

	if st.button("Add availability or preference", type="primary"):
		new_request = pd.DataFrame(
			[
				{
					"resident": selected_resident,
					"request_type": selected_request_type,
					"start_date": start_date,
					"end_date": end_date,
					"priority": priority,
					"reason": reason,
				}
			]
		)
		try:
			replace_schedule_requests(period_id, pd.concat([existing, new_request], ignore_index=True))
		except ValueError as exc:
			st.error(str(exc))
		else:
			clear_month_data_cache()
			flash_success("Availability and preferences saved.")
			st.rerun()

with availability_col:
	st.subheader("Current Availability")
	if existing.empty:
		st.info("No availability or preferences have been added for this month.")
	else:
		for index, row in existing.reset_index(drop=True).iterrows():
			with st.container(border=True):
				summary_col, action_col = st.columns([5, 1])
				date_label = row["start_date"] if row["start_date"] == row["end_date"] else f"{row['start_date']} to {row['end_date']}"
				summary_col.markdown(f"**{row['resident']}** · {row['request_type']} · {date_label}")
				details = [f"Priority: {row['priority']}"]
				if row["reason"]:
					details.append(f"Reason: {row['reason']}")
				summary_col.caption(" · ".join(details))
				if action_col.button("Delete", key=f"delete_request_{period_id}_{index}"):
					remaining = existing.reset_index(drop=True).drop(index=index).reset_index(drop=True)
					try:
						replace_schedule_requests(period_id, remaining)
					except ValueError as exc:
						st.error(str(exc))
					else:
						clear_month_data_cache()
						flash_success("Availability and preferences saved.")
						st.rerun()
