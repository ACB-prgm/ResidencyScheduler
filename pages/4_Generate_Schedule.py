from __future__ import annotations

from datetime import datetime, timedelta, timezone

import streamlit as st
from streamlit_calendar import calendar

from residency_scheduler.auth import require_google_auth
from residency_scheduler.calendar.google import (
	find_existing_period_events,
	has_calendar_scopes,
	list_writable_calendars,
	publish_period_to_calendar,
)
from residency_scheduler.calendar.ical import build_fullcalendar_events, build_ical_calendar
from residency_scheduler.cache import (
	clear_month_data_cache,
	ensure_database_initialized,
	get_cached_month_context,
	get_cached_resident_options,
	get_cached_residents,
	preload_reference_data,
)
from residency_scheduler.repository import (
	swap_assignment_residents,
	update_assignment_resident,
	update_schedule_period_settings,
)
from residency_scheduler.solver import solve_period
from residency_scheduler.ui import flash_error, flash_success, flash_warning, render_flash_messages, select_period

st.set_page_config(page_title="Generate Schedule", layout="wide")

GOOGLE_PUBLISH_IN_PROGRESS_KEY = "google_publish_in_progress"
GOOGLE_PUBLISH_LAST_KEY = "google_publish_last_signature"
GOOGLE_PUBLISH_DUPLICATE_WINDOW = timedelta(seconds=60)


def _publish_signature(period_id: int, calendar_id: str, assignments) -> str:
	assignment_parts = [
		f"{int(row.id)}:{row.work_date}:{int(row.resident_id)}"
		for row in assignments.sort_values(["work_date", "id"]).itertuples()
	]
	return f"{int(period_id)}|{calendar_id}|" + "|".join(assignment_parts)


def _recently_published(signature: str) -> bool:
	last = st.session_state.get(GOOGLE_PUBLISH_LAST_KEY) or {}
	if last.get("signature") != signature:
		return False
	try:
		published_at = datetime.fromisoformat(str(last.get("published_at")))
	except ValueError:
		return False
	return datetime.now(timezone.utc) - published_at < GOOGLE_PUBLISH_DUPLICATE_WINDOW


auth_session = require_google_auth()
ensure_database_initialized()
preload_reference_data()

st.title("Generate Schedule")
st.caption("Run, review, edit, and export the selected month.")
render_flash_messages()

period_id = select_period("generate")
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

	st.markdown("### Workload summary")
	summary = month_context["workload_summary"]
	st.dataframe(summary, width="stretch", hide_index=True)

	st.markdown("### Preference violations")
	violations = month_context["preference_violations"]
	if violations.empty:
		st.success("No prefer-off violations in the current schedule.")
	else:
		st.dataframe(violations, width="stretch", hide_index=True)

	st.markdown("### Manual edit")
	residents = get_cached_residents(active_only=True)
	editable_assignments = assignments[assignments["is_locked"].astype(int) == 0]
	if residents.empty or editable_assignments.empty:
		st.info("No unlocked assignments are available for manual edits.")
	else:
		assignment_options = {
			f"{row.work_date} · {row.resident_name} · assignment #{row.id}": int(row.id)
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

	st.markdown("### Export calendar")
	calendar_name = f"{int(period['year'])}-{int(period['month']):02d}-call-schedule"
	st.download_button(
		"Download call schedule ICS",
		data=build_ical_calendar(assignments, calendar_name=calendar_name),
		file_name=f"{calendar_name}.ics",
		mime="text/calendar",
		type="primary",
	)

	st.markdown("### Google Calendar publish")
	if not has_calendar_scopes(auth_session):
		st.warning("Sign out and sign in again to grant Google Calendar access.")
	else:
		try:
			calendars = list_writable_calendars(auth_session)
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
			selected_calendar_id = str(period.get("google_calendar_id") or "")
			selected_index = 0
			for index, calendar_id in enumerate(calendar_options.values()):
				if calendar_id == selected_calendar_id:
					selected_index = index
					break
			calendar_label = st.selectbox("Calendar", list(calendar_options.keys()), index=selected_index)
			calendar_id = calendar_options[calendar_label]
			try:
				existing_event_ids = find_existing_period_events(int(period_id), calendar_id, auth_session)
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
			publish_disabled = not can_publish or not confirm_replace or publish_in_progress
			if publish_in_progress:
				st.info("Google Calendar publish is already running.")

			if st.button("Publish to Google Calendar", type="primary", disabled=publish_disabled):
				if _recently_published(publish_signature):
					flash_warning("This schedule was just published. Ignoring the duplicate click.")
					st.rerun()
				st.session_state[GOOGLE_PUBLISH_IN_PROGRESS_KEY] = True
				try:
					update_schedule_period_settings(
						int(period_id),
						int(period["required_count"]),
						google_calendar_id=calendar_id,
					)
					result = publish_period_to_calendar(int(period_id), calendar_id, auth_session)
				except Exception as exc:
					flash_error(f"Google Calendar publish failed: {exc}")
					st.session_state[GOOGLE_PUBLISH_IN_PROGRESS_KEY] = False
				else:
					clear_month_data_cache()
					st.session_state[GOOGLE_PUBLISH_LAST_KEY] = {
						"signature": publish_signature,
						"published_at": datetime.now(timezone.utc).isoformat(),
					}
					st.session_state[GOOGLE_PUBLISH_IN_PROGRESS_KEY] = False
					flash_success(
						f"Published {result.inserted_count} assignments to Google Calendar. "
						f"Deleted {result.deleted_count} prior scheduler events."
					)
				st.rerun()
else:
	st.info("No assignments have been generated for this month yet.")

runs = month_context["latest_runs"]
if not runs.empty:
	st.markdown("### Latest solver run")
	st.dataframe(runs, width="stretch", hide_index=True)
