from __future__ import annotations

from datetime import datetime, timedelta, timezone

import streamlit as st
from streamlit_calendar import calendar

from residency_scheduler.auth import get_current_auth_session
from residency_scheduler.calendar.google import (
	find_existing_period_events,
	has_calendar_scopes,
	list_writable_calendars,
	publish_period_to_calendar,
	wipe_period_from_calendar,
)
from residency_scheduler.calendar.ical import build_fullcalendar_events, build_ical_calendar
from residency_scheduler.cache import (
	clear_month_data_cache,
	get_cached_month_context,
	get_cached_resident_options,
	get_cached_residents,
	get_cached_workload_summary_for_scope,
)
from residency_scheduler.repository import (
	get_user_default_google_calendar_id,
	set_user_default_google_calendar_id,
	swap_assignment_residents,
	update_assignment_resident,
	update_schedule_period_settings,
)
from residency_scheduler.solver import solve_period
from residency_scheduler.ui import flash_error, flash_success, flash_warning, render_page_header, render_user_guide

GOOGLE_PUBLISH_IN_PROGRESS_KEY = "google_publish_in_progress"
GOOGLE_PUBLISH_LAST_KEY = "google_publish_last_signature"
GOOGLE_WIPE_IN_PROGRESS_KEY = "google_wipe_in_progress"
GOOGLE_WIPE_LAST_KEY = "google_wipe_last_signature"
GOOGLE_CALENDARS_CACHE_KEY = "google_calendar_list_cache"
GOOGLE_EXISTING_EVENTS_CACHE_KEY = "google_existing_events_cache"
GOOGLE_PUBLISH_DUPLICATE_WINDOW = timedelta(seconds=60)


def _publish_signature(period_id: int, calendar_id: str, assignments) -> str:
	assignment_parts = [
		f"{int(row.id)}:{row.work_date}:{int(row.resident_id)}"
		for row in assignments.sort_values(["work_date", "id"]).itertuples()
	]
	return f"{int(period_id)}|{calendar_id}|" + "|".join(assignment_parts)


def _calendar_action_signature(action: str, period_id: int, calendar_id: str) -> str:
	return f"{action}|{int(period_id)}|{calendar_id}"


def _recent_calendar_action(signature: str, session_key: str) -> bool:
	last = st.session_state.get(session_key) or {}
	if last.get("signature") != signature:
		return False
	try:
		action_at = datetime.fromisoformat(str(last.get("action_at")))
	except ValueError:
		return False
	return datetime.now(timezone.utc) - action_at < GOOGLE_PUBLISH_DUPLICATE_WINDOW


def _remember_calendar_action(signature: str, session_key: str) -> None:
	st.session_state[session_key] = {
		"signature": signature,
		"action_at": datetime.now(timezone.utc).isoformat(),
	}


def _google_user_key(auth_session: dict) -> str:
	return str(auth_session.get("google_sub") or auth_session.get("email") or "unknown")


def _cached_writable_calendars(auth_session: dict):
	cache = st.session_state.get(GOOGLE_CALENDARS_CACHE_KEY) or {}
	user_key = _google_user_key(auth_session)
	if cache.get("user_key") == user_key:
		return list(cache.get("calendars") or [])
	calendars = list_writable_calendars(auth_session)
	st.session_state[GOOGLE_CALENDARS_CACHE_KEY] = {
		"user_key": user_key,
		"calendars": calendars,
	}
	return calendars


def _cached_existing_event_ids(period_id: int, calendar_id: str, auth_session: dict) -> list[str]:
	cache = st.session_state.get(GOOGLE_EXISTING_EVENTS_CACHE_KEY) or {}
	cache_key = {
		"user_key": _google_user_key(auth_session),
		"period_id": int(period_id),
		"calendar_id": str(calendar_id),
	}
	if all(cache.get(key) == value for key, value in cache_key.items()):
		return list(cache.get("event_ids") or [])
	event_ids = find_existing_period_events(int(period_id), calendar_id, auth_session)
	st.session_state[GOOGLE_EXISTING_EVENTS_CACHE_KEY] = cache_key | {"event_ids": event_ids}
	return event_ids


def _clear_google_event_cache() -> None:
	st.session_state.pop(GOOGLE_EXISTING_EVENTS_CACHE_KEY, None)


def _clear_google_calendar_caches() -> None:
	st.session_state.pop(GOOGLE_CALENDARS_CACHE_KEY, None)
	_clear_google_event_cache()


auth_session = get_current_auth_session()

period_id = render_page_header(
	"Generate Schedule",
	"Run, review, edit, and export the selected month.",
	month_location="generate",
)
render_user_guide(
	"Generate Schedule",
	"""
	Use this page to generate, review, adjust, and publish the selected month.

	- **Run scheduler:** creates assignments for the month using active residents, availability, preferences, scheduling rules, and rolling fairness from prior months.
	- **Calendar:** shows the generated schedule by date.
	- **Workload summary:** shows total shifts, weekend shifts, hard assigned shifts, and manual shifts by resident.
	  - Use the range selector to view the selected Month, L3M (selected month plus the prior two months), or YTD (January through the selected month).
	- **Preference violations:** lists soft prefer-off entries that could not be honored.
	- **Edit Assignment:** lets you manually reassign one unlocked assignment or swap residents between two unlocked assignments.
	- **Google Calendar publishing:** writes the current schedule to a selected writable Google Calendar after deleting only prior Residency Scheduler events for the same month and calendar.
	- **ICS export:** downloads a single call-schedule calendar file.
	- **Developer details:** shows recent solver run diagnostics for troubleshooting.
	""",
)
month_context = get_cached_month_context(period_id)
period = month_context["period"]
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
	clear_month_data_cache()
	st.rerun()

assignments = month_context["assignments"]
if not assignments.empty:
	calendar_col, workload_col = st.columns([2, 1], gap="large")
	with calendar_col:
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

	with workload_col:
		st.markdown("### Workload summary")
		workload_range = st.radio(
			"Workload range",
			["Month", "L3M", "YTD"],
			horizontal=True,
			label_visibility="collapsed",
			key=f"workload_range_{period_id}",
		)
		summary = get_cached_workload_summary_for_scope(period_id, workload_range)
		st.caption(f"Showing workload: {workload_range}")
		metric_cols = st.columns(2)
		metric_cols[0].metric("Total shifts", int(summary["total_shifts"].sum()) if not summary.empty else 0)
		metric_cols[1].metric("Violations", len(month_context["preference_violations"]))
		st.dataframe(summary, width="stretch", hide_index=True, key=f"workload_summary_{period_id}_{workload_range.lower()}")

	st.markdown("### Preference violations")
	violations = month_context["preference_violations"]
	if violations.empty:
		st.success("No prefer-off violations in the current schedule.")
	else:
		st.dataframe(violations, width="stretch", hide_index=True)

	with st.expander("Edit Assignment"):
		residents = get_cached_residents(active_only=True)
		editable_assignments = assignments[assignments["is_locked"].astype(int) == 0]
		if residents.empty or editable_assignments.empty:
			st.info("No unlocked assignments are available for manual edits.")
		else:
			assignment_options = {
				f"{row.work_date} · {row.resident_name}": int(row.id)
				for row in editable_assignments.itertuples()
			}
			assignments_by_id = {int(row.id): row for row in editable_assignments.itertuples()}
			resident_options = get_cached_resident_options(active_only=True)
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
							clear_month_data_cache()
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
							clear_month_data_cache()
							flash_success("Swap saved.")
							st.rerun()

	st.markdown("### Google Calendar publishing")
	if not has_calendar_scopes(auth_session):
		st.warning("Sign out and sign in again to grant Google Calendar access.")
	else:
		try:
			calendars = _cached_writable_calendars(auth_session)
		except Exception as exc:
			st.error(f"Could not load Google calendars: {exc}")
			calendars = []

		if not calendars:
			st.info("No writable Google calendars were found for this account.")
		else:
			calendar_options = {
				f"{item['summary']} · {item['accessRole']}{' · primary' if item.get('primary') else ''}": item["id"]
				for item in calendars
			}
			user_default_calendar_id = str(get_user_default_google_calendar_id(auth_session.get("google_sub")) or "")
			selected_calendar_id = user_default_calendar_id or str(period.get("google_calendar_id") or "")
			selected_index = 0
			for index, calendar_id in enumerate(calendar_options.values()):
				if calendar_id == selected_calendar_id:
					selected_index = index
					break
			calendar_label = st.selectbox("Calendar", list(calendar_options.keys()), index=selected_index)
			calendar_id = calendar_options[calendar_label]
			if st.button("Refresh Google Calendar status"):
				_clear_google_calendar_caches()
				st.rerun()
			try:
				existing_event_ids = _cached_existing_event_ids(int(period_id), calendar_id, auth_session)
			except Exception as exc:
				st.error(f"Could not check existing Google Calendar events: {exc}")
				existing_event_ids = []
				can_publish = False
			else:
				can_publish = True

			if existing_event_ids:
				st.warning(
					f"Found {len(existing_event_ids)} existing Residency Scheduler events for this month in the selected calendar."
				)
				confirm_replace = st.checkbox(
					"Delete those existing events, then publish the current schedule."
				)
			else:
				st.info("No existing Residency Scheduler events were found for this month in the selected calendar.")
				confirm_replace = True

			publish_signature = _publish_signature(int(period_id), calendar_id, assignments)
			publish_in_progress = bool(st.session_state.get(GOOGLE_PUBLISH_IN_PROGRESS_KEY))
			wipe_signature = _calendar_action_signature("wipe", int(period_id), calendar_id)
			wipe_in_progress = bool(st.session_state.get(GOOGLE_WIPE_IN_PROGRESS_KEY))
			publish_disabled = not can_publish or not confirm_replace or publish_in_progress or wipe_in_progress
			wipe_disabled = not can_publish or not existing_event_ids or publish_in_progress or wipe_in_progress
			if publish_in_progress:
				st.info("Google Calendar publish is already running.")
			if wipe_in_progress:
				st.info("Google Calendar wipe is already running.")

			wipe_col, publish_col = st.columns([1, 1])

			with wipe_col:
				if st.button("Wipe Scheduler Events", disabled=wipe_disabled):
					if _recent_calendar_action(wipe_signature, GOOGLE_WIPE_LAST_KEY):
						flash_warning("These scheduler events were just wiped. Ignoring the duplicate click.")
						st.rerun()
					st.session_state[GOOGLE_WIPE_IN_PROGRESS_KEY] = True
					try:
						update_schedule_period_settings(
							int(period_id),
							int(period["required_count"]),
							google_calendar_id=calendar_id,
						)
						set_user_default_google_calendar_id(auth_session.get("google_sub"), calendar_id)
						result = wipe_period_from_calendar(
							int(period_id),
							calendar_id,
							auth_session,
							existing_event_ids=list(existing_event_ids),
						)
					except Exception as exc:
						flash_error(f"Google Calendar wipe failed: {exc}")
						st.session_state[GOOGLE_WIPE_IN_PROGRESS_KEY] = False
					else:
						clear_month_data_cache()
						_clear_google_event_cache()
						_remember_calendar_action(wipe_signature, GOOGLE_WIPE_LAST_KEY)
						st.session_state[GOOGLE_WIPE_IN_PROGRESS_KEY] = False
						flash_success(f"Deleted {result.deleted_count} Residency Scheduler event(s) from Google Calendar.")
					st.rerun()

			with publish_col:
				if st.button("Publish to Google Calendar", type="primary", disabled=publish_disabled):
					if _recent_calendar_action(publish_signature, GOOGLE_PUBLISH_LAST_KEY):
						flash_warning("This schedule was just published. Ignoring the duplicate click.")
						st.rerun()
					st.session_state[GOOGLE_PUBLISH_IN_PROGRESS_KEY] = True
					try:
						update_schedule_period_settings(
							int(period_id),
							int(period["required_count"]),
							google_calendar_id=calendar_id,
						)
						set_user_default_google_calendar_id(auth_session.get("google_sub"), calendar_id)
						result = publish_period_to_calendar(
							int(period_id),
							calendar_id,
							auth_session,
							existing_event_ids=list(existing_event_ids),
						)
					except Exception as exc:
						flash_error(f"Google Calendar publish failed: {exc}")
						st.session_state[GOOGLE_PUBLISH_IN_PROGRESS_KEY] = False
					else:
						clear_month_data_cache()
						_clear_google_event_cache()
						_remember_calendar_action(publish_signature, GOOGLE_PUBLISH_LAST_KEY)
						st.session_state[GOOGLE_PUBLISH_IN_PROGRESS_KEY] = False
						flash_success(
							f"Published {result.inserted_count} assignments to Google Calendar. "
							f"Deleted {result.deleted_count} prior scheduler events."
						)
					st.rerun()

	with st.expander("ICS export"):
		calendar_name = f"{int(period['year'])}-{int(period['month']):02d}-call-schedule"
		st.download_button(
			"Download call schedule ICS",
			data=build_ical_calendar(assignments, calendar_name=calendar_name),
			file_name=f"{calendar_name}.ics",
			mime="text/calendar",
			type="primary",
		)
else:
	st.info("No assignments have been generated for this month yet.")

runs = month_context["latest_runs"]
with st.expander("Developer details"):
	if runs.empty:
		st.info("No solver runs recorded for this month.")
	else:
		st.markdown("Latest solver run")
		st.dataframe(runs, width="stretch", hide_index=True)
