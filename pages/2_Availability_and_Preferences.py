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
from residency_scheduler.ui import flash_success, render_flash_messages, select_period

st.set_page_config(page_title="Availability and Preferences", layout="wide")

EDITOR_KEY = "schedule_requests_editor"
EDITOR_DATA_KEY = "schedule_requests_editor_data"
EDITOR_PERIOD_KEY = "schedule_requests_editor_period_id"
EDITOR_VERSION_KEY = "schedule_requests_editor_version"


def _with_priority_defaults(df):
	updated = df.copy()
	for index, row in updated.iterrows():
		request_type = str(row.get("request_type") or "").strip()
		if not request_type:
			continue

		priority = row.get("priority")
		priority_is_blank = priority is None or str(priority).strip() == ""
		if priority_is_blank:
			updated.at[index, "priority"] = default_priority_for_request_type(request_type)
	return updated


def _normalize_editor_dates(df):
	normalized = df.copy()
	for column in ["start_date", "end_date"]:
		if column in normalized.columns:
			normalized[column] = pd.to_datetime(normalized[column], errors="coerce").dt.date
	return normalized


def _data_changed(left, right) -> bool:
	left_compare = left.fillna("").astype(str)
	right_compare = right.fillna("").astype(str)
	return not left_compare.equals(right_compare)


require_google_auth()
ensure_database_initialized()
preload_reference_data()

st.title("Availability and Preferences")
st.caption("Enter availability, preferences, vacation ranges, and hard preassignments for the selected month.")
render_flash_messages()

period_id = select_period("requests")
residents = get_cached_residents(active_only=True)

if residents.empty:
	st.warning("Add active residents before entering schedule requests.")
	st.stop()

resident_options = get_cached_resident_options(active_only=True)
request_type_options = ["vacation", "unavailable", "approved_absence", "medical_leave", "prefer_off", "prefer_work", "assign"]
period = get_cached_period(period_id)
default_request_date = date(int(period["year"]), int(period["month"]), 1)

existing = get_cached_schedule_requests_for_editor(period_id)
if st.session_state.get(EDITOR_PERIOD_KEY) != period_id:
	st.session_state[EDITOR_PERIOD_KEY] = period_id
	st.session_state[EDITOR_DATA_KEY] = _normalize_editor_dates(existing)
	st.session_state[EDITOR_VERSION_KEY] = 0

editor_version = int(st.session_state.get(EDITOR_VERSION_KEY, 0))
st.session_state[EDITOR_DATA_KEY] = _normalize_editor_dates(st.session_state[EDITOR_DATA_KEY])
edited = st.data_editor(
	st.session_state[EDITOR_DATA_KEY],
	key=f"{EDITOR_KEY}_{editor_version}",
	num_rows="dynamic",
	width="stretch",
	column_config={
		"resident": st.column_config.SelectboxColumn("Resident", options=list(resident_options.keys()), required=True),
		"start_date": st.column_config.DateColumn("Start date", required=True, default=default_request_date),
		"end_date": st.column_config.DateColumn("End date", required=True, default=default_request_date),
		"request_type": st.column_config.SelectboxColumn("Request type", options=request_type_options, required=True),
		"priority": st.column_config.SelectboxColumn("Priority", options=["hard", "soft"]),
		"reason": st.column_config.TextColumn("Reason"),
	},
)
edited = _normalize_editor_dates(edited)
edited_with_defaults = _with_priority_defaults(edited)
if _data_changed(edited, edited_with_defaults):
	st.session_state[EDITOR_DATA_KEY] = _normalize_editor_dates(edited_with_defaults)
	st.session_state[EDITOR_VERSION_KEY] = editor_version + 1
	st.rerun()
st.session_state[EDITOR_DATA_KEY] = _normalize_editor_dates(edited)

if st.button("Save availability and preferences", type="primary"):
	try:
		replace_schedule_requests(period_id, edited_with_defaults)
	except ValueError as exc:
		st.error(str(exc))
	else:
		clear_month_data_cache()
		flash_success("Availability and preferences saved.")
		st.session_state[EDITOR_PERIOD_KEY] = None
		st.rerun()

st.markdown(
	"""
	### Priority defaults

	- `hard`: vacation, unavailable, approved absence, medical leave, and assign.
	- `soft`: prefer off and prefer work.
	- Vacation ranges automatically add a soft prefer-work on the Thursday before vacation starts when that date is in the same month.
	"""
)
