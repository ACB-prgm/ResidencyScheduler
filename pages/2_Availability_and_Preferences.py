from __future__ import annotations

from datetime import date

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
	create_schedule_request,
	default_priority_for_request_type,
	delete_schedule_request,
	update_schedule_request,
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
	- **Start/End date:** one date or a date range. Ranges may cross month boundaries.
	- **Priority:** hard entries must be honored; soft entries affect the solver score but can be violated if needed.
	- **Reason:** optional note shown with the entry.

	Vacation, unavailable, approved absence, medical leave, and assign default to hard. Prefer off and prefer work default to soft. Vacation ranges automatically add a soft prefer-work preference on the Thursday before vacation starts when that Thursday is inside the selected month.

	The Current Availability list shows saved entries that overlap the selected month. Use **Delete** on an entry to remove it everywhere it appears.
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
edit_request_key = f"edit_request_id_{period_id}"
pending_request_form_key = f"pending_request_form_{period_id}"
request_type_key = f"request_type_{period_id}"
resident_key = f"request_resident_{period_id}"
start_date_key = f"request_start_{period_id}"
end_date_key = f"request_end_{period_id}"
priority_key = f"request_priority_{period_id}"
reason_key = f"request_reason_{period_id}"
pending_request_form = st.session_state.pop(pending_request_form_key, None)
if pending_request_form is not None:
	if pending_request_form["edit_request_id"] is None:
		st.session_state.pop(edit_request_key, None)
	else:
		st.session_state[edit_request_key] = int(pending_request_form["edit_request_id"])
	st.session_state[request_type_key] = pending_request_form["request_type"]
	st.session_state[resident_key] = pending_request_form["resident"]
	st.session_state[start_date_key] = pending_request_form["start_date"]
	st.session_state[end_date_key] = pending_request_form["end_date"]
	st.session_state[priority_key] = pending_request_form["priority"]
	st.session_state[reason_key] = pending_request_form["reason"]
if request_type_key not in st.session_state:
	st.session_state[request_type_key] = request_type_options[0]
if resident_key not in st.session_state:
	st.session_state[resident_key] = next(iter(resident_options))
if start_date_key not in st.session_state:
	st.session_state[start_date_key] = default_request_date
if end_date_key not in st.session_state:
	st.session_state[end_date_key] = default_request_date
if priority_key not in st.session_state:
	st.session_state[priority_key] = default_priority_for_request_type(st.session_state[request_type_key])
if reason_key not in st.session_state:
	st.session_state[reason_key] = ""
if st.session_state[end_date_key] < st.session_state[start_date_key]:
	st.session_state[end_date_key] = st.session_state[start_date_key]


def _resident_label_for_id(resident_id: int) -> str:
	for label, option_resident_id in resident_options.items():
		if int(option_resident_id) == int(resident_id):
			return label
	return next(iter(resident_options))


existing = get_cached_schedule_requests_for_editor(period_id)
if "id" not in existing.columns or "resident_id" not in existing.columns:
	clear_month_data_cache()
	st.rerun()
metric_cols = st.columns(3)
metric_cols[0].metric("Active residents", len(residents))
metric_cols[1].metric("Active availability", len(existing))
metric_cols[2].metric("Availability types", int(existing["request_type"].nunique()) if not existing.empty else 0)

form_col, availability_col = st.columns([1, 1], gap="large")
edit_request_id = st.session_state.get(edit_request_key)
is_editing = edit_request_id is not None
if is_editing and int(edit_request_id) not in set(existing["id"].astype(int)):
	st.session_state.pop(edit_request_key, None)
	edit_request_id = None
	is_editing = False


def _sync_default_priority() -> None:
	if st.session_state.get(edit_request_key) is None:
		st.session_state[priority_key] = default_priority_for_request_type(st.session_state[request_type_key])


def _queue_request_form_reset() -> None:
	st.session_state[pending_request_form_key] = {
		"edit_request_id": None,
		"request_type": request_type_options[0],
		"resident": next(iter(resident_options)),
		"start_date": default_request_date,
		"end_date": default_request_date,
		"priority": default_priority_for_request_type(request_type_options[0]),
		"reason": "",
	}


def _load_request_for_edit(row) -> None:
	st.session_state[pending_request_form_key] = {
		"edit_request_id": int(row["id"]),
		"request_type": str(row["request_type"]),
		"resident": _resident_label_for_id(int(row["resident_id"])),
		"start_date": row["start_date"],
		"end_date": row["end_date"],
		"priority": str(row["priority"]),
		"reason": str(row["reason"] or ""),
	}

with form_col:
	st.subheader("Edit Availability" if is_editing else "+ Add Availability")
	selected_request_type = st.selectbox(
		"Availability type",
		request_type_options,
		key=request_type_key,
		on_change=_sync_default_priority,
	)
	selected_resident = st.selectbox("Resident", list(resident_options.keys()), key=resident_key)
	start_date = st.date_input("Start date", key=start_date_key)
	if st.session_state[end_date_key] < start_date:
		st.session_state[end_date_key] = start_date
		st.rerun()
	end_date = st.date_input("End date", min_value=start_date, key=end_date_key)
	priority = st.selectbox(
		"Priority",
		["hard", "soft"],
		key=priority_key,
	)
	reason = st.text_input("Reason", key=reason_key)

	button_cols = st.columns([1, 1]) if is_editing else [st.container()]
	save_clicked = button_cols[0].button("Save changes" if is_editing else "Add availability or preference", type="primary")
	cancel_clicked = is_editing and button_cols[1].button("Cancel")
	if cancel_clicked:
		_queue_request_form_reset()
		st.rerun()

	if save_clicked:
		try:
			if is_editing:
				update_schedule_request(
					request_id=int(edit_request_id),
					resident_id=resident_options[selected_resident],
					start_date=start_date,
					end_date=end_date,
					request_type=selected_request_type,
					priority=priority,
					reason=reason,
				)
			else:
				create_schedule_request(
					resident_id=resident_options[selected_resident],
					start_date=start_date,
					end_date=end_date,
					request_type=selected_request_type,
					priority=priority,
					reason=reason,
				)
		except ValueError as exc:
			st.error(str(exc))
		else:
			_queue_request_form_reset()
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
				with action_col:
					if st.button("Edit", key=f"edit_request_{int(row['id'])}"):
						_load_request_for_edit(row)
						st.rerun()
					if st.button("Delete", key=f"delete_request_{int(row['id'])}"):
						delete_schedule_request(int(row["id"]))
						if st.session_state.get(edit_request_key) == int(row["id"]):
							_queue_request_form_reset()
						clear_month_data_cache()
						flash_success("Availability and preferences saved.")
						st.rerun()
