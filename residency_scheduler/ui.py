from __future__ import annotations

from datetime import date

import streamlit as st

from residency_scheduler.repository import (
	get_app_state,
	get_calendar_months,
	get_schedule_periods,
	set_app_state,
)

GLOBAL_MONTH_KEY = "global_year_month"
GLOBAL_DRAFT_KEY = "global_schedule_draft"
ACTIVE_MONTH_KEY = "active_year_month"
ACTIVE_DRAFT_KEY = "active_schedule_draft_id"
MONTH_QUERY_PARAM = "month"
DRAFT_QUERY_PARAM = "draft_id"
APP_STATE_MONTH_KEY = "active_year_month"
APP_STATE_DRAFT_KEY = "active_schedule_draft_id"


def select_month(location: str = "global") -> tuple[int, int]:
	months = get_calendar_months()
	if months.empty:
		st.warning("No calendar months exist. Initialize the database first.")
		st.stop()

	month_options = {
		f"{row.month_key} · {row.display_name}": (int(row.year), int(row.month))
		for row in months.itertuples()
	}
	month_labels = list(month_options.keys())
	month_keys = {label: f"{value[0]}-{value[1]:02d}" for label, value in month_options.items()}
	valid_month_keys = set(month_keys.values())
	query_month = _query_param(MONTH_QUERY_PARAM)
	saved_month = get_app_state(APP_STATE_MONTH_KEY)
	session_month = st.session_state.get(ACTIVE_MONTH_KEY)
	if query_month in valid_month_keys:
		st.session_state[ACTIVE_MONTH_KEY] = query_month
	elif saved_month in valid_month_keys:
		st.session_state[ACTIVE_MONTH_KEY] = saved_month
	elif session_month in valid_month_keys:
		st.session_state[ACTIVE_MONTH_KEY] = session_month
	else:
		current_month = f"{date.today().year}-{date.today().month:02d}"
		st.session_state[ACTIVE_MONTH_KEY] = current_month if current_month in valid_month_keys else month_keys[month_labels[0]]

	widget_key = f"_{location}_year_month"
	active_label = _month_label_for_key(month_labels, st.session_state[ACTIVE_MONTH_KEY]) or month_labels[0]
	if st.session_state.get(widget_key) != active_label:
		st.session_state[widget_key] = active_label

	month_label = st.selectbox(
		"Year-Month",
		month_labels,
		key=widget_key,
		on_change=_persist_month_selection,
		args=(widget_key,),
	)
	year, month = month_options[month_label]
	st.session_state[ACTIVE_MONTH_KEY] = f"{year}-{month:02d}"
	set_app_state(APP_STATE_MONTH_KEY, st.session_state[ACTIVE_MONTH_KEY])
	st.query_params[MONTH_QUERY_PARAM] = st.session_state[ACTIVE_MONTH_KEY]
	return year, month


def select_draft(year: int, month: int, allow_empty: bool = False, location: str = "global") -> int | None:
	drafts = get_schedule_periods(year=year, month=month)
	if drafts.empty:
		if allow_empty:
			st.session_state.pop(ACTIVE_DRAFT_KEY, None)
			return None
		st.warning("No drafts exist for the selected month. Create one on the home page first.")
		st.stop()

	draft_options = {
		f"{row.draft_name} · #{row.id}": int(row.id)
		for row in drafts.itertuples()
	}
	draft_labels = list(draft_options.keys())
	valid_draft_ids = set(draft_options.values())
	query_draft_id = _coerce_int(_query_param(DRAFT_QUERY_PARAM))
	saved_draft_id = _coerce_int(get_app_state(APP_STATE_DRAFT_KEY))
	session_draft_id = _coerce_int(st.session_state.get(ACTIVE_DRAFT_KEY))
	if query_draft_id in valid_draft_ids:
		st.session_state[ACTIVE_DRAFT_KEY] = query_draft_id
	elif saved_draft_id in valid_draft_ids:
		st.session_state[ACTIVE_DRAFT_KEY] = saved_draft_id
	elif session_draft_id in valid_draft_ids:
		st.session_state[ACTIVE_DRAFT_KEY] = session_draft_id
	else:
		st.session_state[ACTIVE_DRAFT_KEY] = draft_options[draft_labels[0]]

	widget_key = f"_{location}_schedule_draft"
	active_label = _draft_label_for_id(draft_options, str(st.session_state[ACTIVE_DRAFT_KEY])) or draft_labels[0]
	if st.session_state.get(widget_key) != active_label:
		st.session_state[widget_key] = active_label

	draft_label = st.selectbox(
		"Draft",
		draft_labels,
		key=widget_key,
		on_change=_persist_draft_selection,
		args=(widget_key, draft_options),
	)
	draft_id = draft_options[draft_label]
	st.session_state[ACTIVE_DRAFT_KEY] = draft_id
	set_app_state(APP_STATE_DRAFT_KEY, str(draft_id))
	st.query_params[DRAFT_QUERY_PARAM] = str(draft_id)
	return draft_id


def select_period(location: str = "main") -> int:
	"""Select the active schedule draft using global page-shared state."""
	year, month = select_month(location)
	period_id = select_draft(year, month, allow_empty=False, location=location)
	if period_id is None:
		st.stop()
	return period_id


def _query_param(key: str) -> str | None:
	value = st.query_params.get(key)
	if isinstance(value, list):
		return value[0] if value else None
	return value


def _month_label_for_key(month_labels: list[str], month_key: str | None) -> str | None:
	if not month_key:
		return None
	for label in month_labels:
		if label.startswith(f"{month_key} ·"):
			return label
	return None


def _draft_label_for_id(draft_options: dict[str, int], draft_id: str | None) -> str | None:
	target_id = _coerce_int(draft_id)
	if target_id is None:
		return None
	for label, option_id in draft_options.items():
		if option_id == target_id:
			return label
	return None


def _persist_month_selection(widget_key: str) -> None:
	label = st.session_state.get(widget_key)
	month_key = str(label).split(" · ", maxsplit=1)[0] if label else None
	if month_key:
		st.session_state[ACTIVE_MONTH_KEY] = month_key
		st.session_state.pop(ACTIVE_DRAFT_KEY, None)
		set_app_state(APP_STATE_MONTH_KEY, month_key)
		set_app_state(APP_STATE_DRAFT_KEY, None)
		st.query_params[MONTH_QUERY_PARAM] = month_key
		st.query_params.pop(DRAFT_QUERY_PARAM, None)


def _persist_draft_selection(widget_key: str, draft_options: dict[str, int]) -> None:
	label = st.session_state.get(widget_key)
	if label in draft_options:
		draft_id = int(draft_options[label])
		st.session_state[ACTIVE_DRAFT_KEY] = draft_id
		set_app_state(APP_STATE_DRAFT_KEY, str(draft_id))
		st.query_params[DRAFT_QUERY_PARAM] = str(draft_id)


def _coerce_int(value: str | int | None) -> int | None:
	if value is None:
		return None
	try:
		return int(value)
	except (TypeError, ValueError):
		return None
