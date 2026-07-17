from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from residency_scheduler.auth import current_user_is_allowed
from residency_scheduler.cache import (
	clear_schedule_request_cache,
	get_cached_hard_schedule_requests_for_conflict_check,
	get_cached_period,
	get_cached_recurring_preferences_for_editor,
	get_cached_residents,
	get_cached_schedule_requests_for_editor,
)
from residency_scheduler.repository import (
	WEEKDAYS,
	WEEKDAY_NAMES,
	create_recurring_preference,
	create_schedule_request,
	default_priority_for_request_type,
	delete_recurring_preference,
	delete_schedule_request,
	find_hard_schedule_request_conflicts,
	update_recurring_preference,
	update_schedule_request,
)
from residency_scheduler.ui import (
	flash_success,
	render_card_action_styles,
	render_page_header,
	render_user_guide,
)

REQUEST_TYPE_OPTIONS = [
	"vacation",
	"unavailable",
	"approved_absence",
	"medical_leave",
	"prefer_off",
	"prefer_work",
	"assign",
]
RECURRING_TYPE_OPTIONS = ["prefer_off", "prefer_work"]


def _display_type(value: str) -> str:
	return str(value).replace("_", " ").title()


def _display_date(value: date) -> str:
	return f"{value.strftime('%B')} {value.day}, {value.year}"


def _display_date_range(start_date: date, end_date: date) -> str:
	if start_date == end_date:
		return _display_date(start_date)
	return f"{_display_date(start_date)} through {_display_date(end_date)}"


def _hard_conflict_warning(candidate_type: str, conflicts: list[dict]) -> str:
	lines = ["**Conflicting hard availability or preferences:**"]
	for conflict in conflicts:
		lines.append(
			f"- **{conflict['resident_name']}**: hard {_display_type(candidate_type)} conflicts with saved hard "
			f"{_display_type(conflict['request_type'])} on "
			f"**{_display_date_range(conflict['overlap_start'], conflict['overlap_end'])}**."
		)
	lines.append(
		"A hard entry cannot require the same resident to both work and be off. "
		"Review **User Guide: Availability and Preferences** at the top of this page."
	)
	return "\n\n".join(lines)


def _filter_rows(frame: pd.DataFrame, resident: str, search: str, fields: list[str]) -> pd.DataFrame:
	filtered = frame.copy()
	if resident != "All residents":
		filtered = filtered[filtered["resident"].astype(str) == resident]
	query = search.strip().casefold()
	if query and not filtered.empty:
		search_text = filtered[fields].fillna("").astype(str).agg(" ".join, axis=1).str.casefold()
		filtered = filtered[search_text.str.contains(query, regex=False)]
	return filtered


def _resident_options(residents: pd.DataFrame, active_only: bool) -> dict[str, int]:
	options: dict[str, int] = {}
	for row in residents.sort_values("name").itertuples():
		if active_only and not bool(row.active):
			continue
		label = str(row.name) if bool(row.active) else f"{row.name} (inactive)"
		options[label] = int(row.id)
	return options


def _resident_label_for_id(options: dict[str, int], resident_id: int) -> str:
	for label, option_id in options.items():
		if int(option_id) == int(resident_id):
			return label
	raise ValueError(f"Resident #{resident_id} is not available.")


period_id = render_page_header(
	"Availability and Preferences",
	"Add dated availability or reusable weekly preferences.",
	month_location="requests",
)
render_user_guide(
	"Availability and Preferences",
	"""
	Use **Dated** for one date or a date range. Use **Recurring** for weekly prefer-off or prefer-work patterns.

	- **Availability type:** vacation, unavailable, approved absence, medical leave, prefer off, prefer work, or assign.
	- **Dates:** start and end dates are inclusive and refer to the date the call shift starts. Dated ranges may cross month boundaries and appear in every month they overlap.
	- **Recurring preference:** applies on one weekday from its start date, either indefinitely or through an end date.
	- **Priority:** hard entries must be honored. Soft entries influence the schedule but may be missed when needed. Dated prefer off/work defaults to soft; recurring preferences are always soft.
	- **Description:** optional context shown with the saved entry.

	For a single shift, select the date that the shift starts; the end date remains the same automatically. For example, a shift beginning at 6:00 PM on August 14 and ending at 7:00 AM on August 15 is entered as **August 14 through August 14**. A range of **August 14 through August 16** includes the shifts starting on August 14, August 15, and August 16.

	Vacation, unavailable, approved absence, medical leave, and assign default to hard. Vacation ranges also add a soft prefer-work preference on the Thursday before vacation begins when that Thursday is in the generated month. A dated preference takes precedence over a recurring preference on the same resident and date. Duplicate preferences are evaluated once.

	Deleting a dated or recurring entry removes only that saved entry. Recurring entries remain visible if a resident becomes inactive, but inactive residents are not included in solver inputs.
	""",
)
render_card_action_styles()

all_residents = get_cached_residents(active_only=False)
active_residents = all_residents[all_residents["active"].astype(bool)].copy() if not all_residents.empty else all_residents
if active_residents.empty:
	st.warning("Add active residents before entering availability and preferences.")
	st.stop()

active_resident_options = _resident_options(all_residents, active_only=True)
all_resident_options = _resident_options(all_residents, active_only=False)
period = get_cached_period(period_id)
default_date = date(int(period["year"]), int(period["month"]), 1)
dated = get_cached_schedule_requests_for_editor(period_id)
hard_request_snapshot = get_cached_hard_schedule_requests_for_conflict_check()
recurring = get_cached_recurring_preferences_for_editor()

metric_cols = st.columns(3)
metric_cols[0].metric("Active residents", len(active_residents))
metric_cols[1].metric("Dated this month", len(dated))
metric_cols[2].metric("Recurring preferences", len(recurring))

dated_tab, recurring_tab = st.tabs(
	["Dated", "Recurring"],
	default="Dated",
	key="availability_preference_tabs",
	on_change="rerun",
)

with dated_tab:
	form_col, list_col = st.columns([1, 1], gap="large")
	edit_key = f"dated_preference_edit_{period_id}"
	pending_key = f"dated_preference_pending_{period_id}"
	type_key = f"dated_type_{period_id}"
	resident_key = f"dated_resident_{period_id}"
	start_key = f"dated_start_{period_id}"
	end_key = f"dated_end_{period_id}"
	priority_key = f"dated_priority_{period_id}"
	description_key = f"dated_description_{period_id}"

	pending = st.session_state.pop(pending_key, None)
	if pending is not None:
		if pending["id"] is None:
			st.session_state.pop(edit_key, None)
		else:
			st.session_state[edit_key] = int(pending["id"])
		st.session_state[type_key] = pending["request_type"]
		st.session_state[resident_key] = pending["resident"]
		st.session_state[start_key] = pending["start_date"]
		st.session_state[end_key] = pending["end_date"]
		st.session_state[priority_key] = pending["priority"]
		st.session_state[description_key] = pending["reason"]

	st.session_state.setdefault(type_key, REQUEST_TYPE_OPTIONS[0])
	st.session_state.setdefault(resident_key, next(iter(active_resident_options)))
	st.session_state.setdefault(start_key, default_date)
	st.session_state.setdefault(end_key, default_date)
	st.session_state.setdefault(priority_key, default_priority_for_request_type(st.session_state[type_key]))
	st.session_state.setdefault(description_key, "")
	if st.session_state[end_key] < st.session_state[start_key]:
		st.session_state[end_key] = st.session_state[start_key]

	edit_id = st.session_state.get(edit_key)
	is_editing = edit_id is not None and not dated.empty and int(edit_id) in set(dated["id"].astype(int))
	if edit_id is not None and not is_editing:
		st.session_state.pop(edit_key, None)
		edit_id = None

	def queue_dated_reset() -> None:
		st.session_state[pending_key] = {
			"id": None,
			"request_type": REQUEST_TYPE_OPTIONS[0],
			"resident": next(iter(active_resident_options)),
			"start_date": default_date,
			"end_date": default_date,
			"priority": default_priority_for_request_type(REQUEST_TYPE_OPTIONS[0]),
			"reason": "",
		}

	def load_dated(row: pd.Series) -> None:
		st.session_state[pending_key] = {
			"id": int(row["id"]),
			"request_type": str(row["request_type"]),
			"resident": _resident_label_for_id(all_resident_options, int(row["resident_id"])),
			"start_date": row["start_date"],
			"end_date": row["end_date"],
			"priority": str(row["priority"]),
			"reason": str(row["reason"] or ""),
		}

	def sync_dated_priority() -> None:
		if st.session_state.get(edit_key) is None:
			st.session_state[priority_key] = default_priority_for_request_type(st.session_state[type_key])

	def sync_dated_end() -> None:
		if st.session_state[end_key] < st.session_state[start_key]:
			st.session_state[end_key] = st.session_state[start_key]

	def render_dated_form() -> None:
		if not current_user_is_allowed():
			st.rerun(scope="app")
		current_edit_id = st.session_state.get(edit_key)
		current_is_editing = (
			current_edit_id is not None
			and not dated.empty
			and int(current_edit_id) in set(dated["id"].astype(int))
		)
		current_resident_options = all_resident_options if current_is_editing else active_resident_options
		st.subheader("Edit Dated Availability" if current_is_editing else "+ Add Dated Availability")
		request_type = st.selectbox("Availability type", REQUEST_TYPE_OPTIONS, key=type_key, on_change=sync_dated_priority)
		resident_label = st.selectbox("Resident", list(current_resident_options), key=resident_key)
		start_date = st.date_input("Start date", key=start_key, on_change=sync_dated_end)
		end_date = st.date_input("End date", min_value=start_date, key=end_key)
		priority = st.selectbox("Priority", ["hard", "soft"], key=priority_key)
		description = st.text_input("Description", key=description_key)
		conflicts = find_hard_schedule_request_conflicts(
			hard_request_snapshot,
			current_resident_options[resident_label],
			start_date,
			end_date,
			request_type,
			priority,
			exclude_request_id=int(current_edit_id) if current_is_editing else None,
		)
		if conflicts:
			st.warning(_hard_conflict_warning(request_type, conflicts))
		buttons = st.columns([1, 1]) if current_is_editing else [st.container()]
		save = buttons[0].button(
			"Save changes" if current_is_editing else "Add availability or preference",
			type="primary",
			width="stretch",
			disabled=bool(conflicts),
		)
		cancel = current_is_editing and buttons[1].button("Cancel", width="stretch")
		if cancel:
			queue_dated_reset()
			st.rerun(scope="app")
		if not save:
			return
		try:
			if current_is_editing:
				update_schedule_request(
					int(current_edit_id),
					current_resident_options[resident_label],
					start_date,
					end_date,
					request_type,
					priority,
					description,
				)
			else:
				create_schedule_request(
					current_resident_options[resident_label],
					start_date,
					end_date,
					request_type,
					priority,
					description,
				)
		except ValueError as exc:
			if str(exc).startswith("Conflicting hard requests"):
				clear_schedule_request_cache()
				st.rerun(scope="app")
			st.error(str(exc))
		else:
			queue_dated_reset()
			clear_schedule_request_cache()
			flash_success("Dated availability or preference saved.")
			st.rerun(scope="app")

	with form_col:
		render_dated_form()

	with list_col:
		st.subheader("Current Dated Availability")
		filter_col, search_col = st.columns([1, 1])
		resident_filter = filter_col.selectbox(
			"Filter resident",
			["All residents"] + sorted(dated["resident"].astype(str).unique().tolist()) if not dated.empty else ["All residents"],
			key=f"dated_filter_{period_id}",
		)
		search = search_col.text_input("Search", key=f"dated_search_{period_id}", placeholder="Type, priority, description...")
		shown = dated.copy()
		if not shown.empty:
			shown["status"] = shown["resident_active"].map({1: "active", 0: "inactive", True: "active", False: "inactive"})
			shown = _filter_rows(shown, resident_filter, search, ["resident", "request_type", "priority", "status", "reason"])
			shown = shown.sort_values(["start_date", "resident", "request_type", "id"])
		if shown.empty:
			st.info("No dated availability or preferences match this view.")
		else:
			for _, row in shown.iterrows():
				with st.container(border=True):
					details, actions = st.columns([2.2, 1], gap="medium")
					with details:
						inactive = " · Inactive" if not bool(row["resident_active"]) else ""
						st.markdown(f"**{row['resident']}**{inactive}")
						st.write(_display_type(row["request_type"]))
						date_label = row["start_date"] if row["start_date"] == row["end_date"] else f"{row['start_date']} to {row['end_date']}"
						st.caption(f"Dates: {date_label}")
						st.caption(f"Priority: {str(row['priority']).title()}")
						if row["reason"]:
							st.caption(f"Description: {row['reason']}")
					with actions:
						if st.button("Edit", key=f"edit_dated_{int(row['id'])}", width="stretch"):
							load_dated(row)
							st.rerun()
						if st.button("Delete", key=f"delete_dated_{int(row['id'])}", width="stretch"):
							delete_schedule_request(int(row["id"]))
							if st.session_state.get(edit_key) == int(row["id"]):
								queue_dated_reset()
							clear_schedule_request_cache()
							flash_success("Dated availability or preference deleted.")
							st.rerun()

with recurring_tab:
	form_col, list_col = st.columns([1, 1], gap="large")
	edit_key = "recurring_preference_edit"
	pending_key = "recurring_preference_pending"
	type_key = "recurring_type"
	resident_key = "recurring_resident"
	weekday_key = "recurring_weekday"
	start_key = "recurring_start"
	duration_key = "recurring_duration"
	end_key = "recurring_end"
	description_key = "recurring_description"

	pending = st.session_state.pop(pending_key, None)
	if pending is not None:
		if pending["id"] is None:
			st.session_state.pop(edit_key, None)
		else:
			st.session_state[edit_key] = int(pending["id"])
		st.session_state[type_key] = pending["request_type"]
		st.session_state[resident_key] = pending["resident"]
		st.session_state[weekday_key] = pending["weekday"]
		st.session_state[start_key] = pending["start_date"]
		st.session_state[duration_key] = pending["duration"]
		st.session_state[end_key] = pending["end_date"]
		st.session_state[description_key] = pending["reason"]

	st.session_state.setdefault(type_key, RECURRING_TYPE_OPTIONS[0])
	st.session_state.setdefault(resident_key, next(iter(active_resident_options)))
	st.session_state.setdefault(weekday_key, "Monday")
	st.session_state.setdefault(start_key, default_date)
	st.session_state.setdefault(duration_key, "Indefinite")
	st.session_state.setdefault(end_key, default_date)
	st.session_state.setdefault(description_key, "")
	if st.session_state[end_key] < st.session_state[start_key]:
		st.session_state[end_key] = st.session_state[start_key]

	edit_id = st.session_state.get(edit_key)
	is_editing = edit_id is not None and not recurring.empty and int(edit_id) in set(recurring["id"].astype(int))
	if edit_id is not None and not is_editing:
		st.session_state.pop(edit_key, None)
		edit_id = None

	def queue_recurring_reset() -> None:
		st.session_state[pending_key] = {
			"id": None,
			"request_type": RECURRING_TYPE_OPTIONS[0],
			"resident": next(iter(active_resident_options)),
			"weekday": "Monday",
			"start_date": default_date,
			"duration": "Indefinite",
			"end_date": default_date,
			"reason": "",
		}

	def load_recurring(row: pd.Series) -> None:
		end_date = row["effective_end_date"]
		bounded = pd.notna(end_date)
		st.session_state[pending_key] = {
			"id": int(row["id"]),
			"request_type": str(row["request_type"]),
			"resident": _resident_label_for_id(all_resident_options, int(row["resident_id"])),
			"weekday": WEEKDAY_NAMES[int(row["weekday"])],
			"start_date": row["effective_start_date"],
			"duration": "Ends on date" if bounded else "Indefinite",
			"end_date": end_date if bounded else row["effective_start_date"],
			"reason": str(row["reason"] or ""),
		}

	def sync_recurring_end() -> None:
		if st.session_state[end_key] < st.session_state[start_key]:
			st.session_state[end_key] = st.session_state[start_key]

	def render_recurring_form() -> None:
		if not current_user_is_allowed():
			st.rerun(scope="app")
		current_edit_id = st.session_state.get(edit_key)
		current_is_editing = (
			current_edit_id is not None
			and not recurring.empty
			and int(current_edit_id) in set(recurring["id"].astype(int))
		)
		current_resident_options = all_resident_options if current_is_editing else active_resident_options
		st.subheader("Edit Recurring Preference" if current_is_editing else "+ Add Recurring Preference")
		request_type = st.selectbox("Preference type", RECURRING_TYPE_OPTIONS, key=type_key, format_func=_display_type)
		resident_label = st.selectbox("Resident", list(current_resident_options), key=resident_key)
		weekday_label = st.selectbox("Weekday", list(WEEKDAYS), key=weekday_key)
		start_date = st.date_input("Start date", key=start_key, on_change=sync_recurring_end)
		duration = st.segmented_control("Duration", ["Indefinite", "Ends on date"], key=duration_key)
		end_date = None
		if duration == "Ends on date":
			end_date = st.date_input("End date", min_value=start_date, key=end_key)
		description = st.text_input("Description", key=description_key)
		st.caption("Priority: Soft")
		buttons = st.columns([1, 1]) if current_is_editing else [st.container()]
		save = buttons[0].button(
			"Save changes" if current_is_editing else "Add recurring preference",
			type="primary",
			width="stretch",
		)
		cancel = current_is_editing and buttons[1].button("Cancel", width="stretch")
		if cancel:
			queue_recurring_reset()
			st.rerun(scope="app")
		if not save:
			return
		try:
			if current_is_editing:
				update_recurring_preference(
					int(current_edit_id),
					current_resident_options[resident_label],
					request_type,
					WEEKDAYS[weekday_label],
					start_date,
					end_date,
					description,
				)
			else:
				create_recurring_preference(
					current_resident_options[resident_label],
					request_type,
					WEEKDAYS[weekday_label],
					start_date,
					end_date,
					description,
				)
		except ValueError as exc:
			st.error(str(exc))
		else:
			queue_recurring_reset()
			clear_schedule_request_cache()
			flash_success("Recurring preference saved.")
			st.rerun(scope="app")

	with form_col:
		render_recurring_form()

	with list_col:
		st.subheader("Current Recurring Preferences")
		filter_col, search_col = st.columns([1, 1])
		resident_filter = filter_col.selectbox(
			"Filter resident",
			["All residents"] + sorted(recurring["resident"].astype(str).unique().tolist()) if not recurring.empty else ["All residents"],
			key="recurring_filter",
		)
		search = search_col.text_input("Search", key="recurring_search", placeholder="Weekday, type, description...")
		shown = recurring.copy()
		if not shown.empty:
			shown["weekday_name"] = shown["weekday"].map(WEEKDAY_NAMES)
			shown["status"] = shown["resident_active"].map({1: "active", 0: "inactive", True: "active", False: "inactive"})
			shown = _filter_rows(
				shown,
				resident_filter,
				search,
				["resident", "request_type", "weekday_name", "priority", "status", "reason"],
			)
			shown = shown.sort_values(["effective_start_date", "resident", "weekday", "request_type", "id"])
		if shown.empty:
			st.info("No recurring preferences match this view.")
		else:
			for _, row in shown.iterrows():
				with st.container(border=True):
					details, actions = st.columns([2.2, 1], gap="medium")
					with details:
						inactive = " · Inactive" if not bool(row["resident_active"]) else ""
						st.markdown(f"**{row['resident']}**{inactive}")
						st.write(_display_type(row["request_type"]))
						end_label = row["effective_end_date"] if pd.notna(row["effective_end_date"]) else "Indefinite"
						st.caption(f"Every {WEEKDAY_NAMES[int(row['weekday'])]} · {row['effective_start_date']} to {end_label}")
						st.caption("Priority: Soft")
						if row["reason"]:
							st.caption(f"Description: {row['reason']}")
					with actions:
						if st.button("Edit", key=f"edit_recurring_{int(row['id'])}", width="stretch"):
							load_recurring(row)
							st.rerun()
						if st.button("Delete", key=f"delete_recurring_{int(row['id'])}", width="stretch"):
							delete_recurring_preference(int(row["id"]))
							if st.session_state.get(edit_key) == int(row["id"]):
								queue_recurring_reset()
							clear_schedule_request_cache()
							flash_success("Recurring preference deleted.")
							st.rerun()
