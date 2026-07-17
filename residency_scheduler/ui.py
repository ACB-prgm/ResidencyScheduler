from __future__ import annotations

from datetime import date
from pathlib import Path

import streamlit as st

from residency_scheduler.cache import get_cached_calendar_months, get_cached_or_create_schedule_period
from residency_scheduler.repository import (
	get_app_state,
	set_app_state,
)

ACTIVE_MONTH_KEY = "active_year_month"
MONTH_QUERY_PARAM = "month"
APP_STATE_MONTH_KEY = "active_year_month"
SAVED_MONTH_LOADED_KEY = "active_year_month_loaded"
FLASH_KEY = "flash_messages"
LOGO_PATH = Path(__file__).resolve().parents[1] / "assets" / "residency_scheduler_logo.png"


def render_sidebar_logo(streamlit_module=st) -> None:
	"""Render the app logo at the top of the sidebar when the asset is available."""
	if LOGO_PATH.exists():
		streamlit_module.image(str(LOGO_PATH), width="stretch")


def render_page_header(title: str, caption: str, month_location: str | None = None) -> int | None:
	"""Render a consistent page header and optional month selector."""
	if month_location:
		title_col, month_col = st.columns([3, 1], gap="large")
		with title_col:
			st.title(title)
			st.caption(caption)
		with month_col:
			period_id = select_period(month_location)
		render_flash_messages()
		return period_id

	st.title(title)
	st.caption(caption)
	render_flash_messages()
	return None


def render_user_guide(page_name: str, body: str, expanded: bool = False, callout: str | None = None) -> None:
	"""Render the page-level help section with a consistent label."""
	with st.expander(f"User Guide: {page_name}", expanded=expanded):
		if callout:
			st.info(callout)
		st.markdown(body)


def render_card_action_styles() -> None:
	"""Keep compact card actions readable as two-column layouts narrow."""
	st.markdown(
		"""
		<style>
		div[data-testid="stButton"] > button,
		div[data-testid="stFormSubmitButton"] > button {
			min-width: 6rem;
			white-space: nowrap;
		}
		</style>
		""",
		unsafe_allow_html=True,
	)


def select_month(location: str = "global") -> tuple[int, int]:
	months = get_cached_calendar_months()
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
	if not st.session_state.get(SAVED_MONTH_LOADED_KEY):
		st.session_state[APP_STATE_MONTH_KEY] = get_app_state(APP_STATE_MONTH_KEY)
		st.session_state[SAVED_MONTH_LOADED_KEY] = True
	saved_month = st.session_state.get(APP_STATE_MONTH_KEY)
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
	st.query_params[MONTH_QUERY_PARAM] = st.session_state[ACTIVE_MONTH_KEY]
	return year, month


def select_period(location: str = "main") -> int:
	"""Select the active schedule month using global page-shared state."""
	year, month = select_month(location)
	return get_cached_or_create_schedule_period(year, month)


def flash(level: str, message: str) -> None:
	messages = list(st.session_state.get(FLASH_KEY, []))
	messages.append({"level": level, "message": message})
	st.session_state[FLASH_KEY] = messages


def flash_success(message: str) -> None:
	flash("success", message)


def flash_error(message: str) -> None:
	flash("error", message)


def flash_warning(message: str) -> None:
	flash("warning", message)


def render_flash_messages() -> None:
	messages = list(st.session_state.pop(FLASH_KEY, []))
	for item in messages:
		level = str(item.get("level", "info"))
		message = str(item.get("message", ""))
		if level == "success":
			st.success(message)
		elif level == "error":
			st.error(message)
		elif level == "warning":
			st.warning(message)
		else:
			st.info(message)


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


def _persist_month_selection(widget_key: str) -> None:
	label = st.session_state.get(widget_key)
	month_key = str(label).split(" · ", maxsplit=1)[0] if label else None
	if month_key:
		st.session_state[ACTIVE_MONTH_KEY] = month_key
		st.session_state[APP_STATE_MONTH_KEY] = month_key
		set_app_state(APP_STATE_MONTH_KEY, month_key)
		st.query_params[MONTH_QUERY_PARAM] = month_key
