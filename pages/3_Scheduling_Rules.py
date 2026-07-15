from __future__ import annotations

import streamlit as st

from residency_scheduler.cache import (
	clear_month_data_cache,
	get_cached_resident_options,
	get_cached_residents,
)
from residency_scheduler.repository import (
	WEEKDAYS,
	WEEKDAY_NAMES,
	create_schedule_rule,
	delete_schedule_rule,
	get_schedule_rules,
	update_schedule_rule,
)
from residency_scheduler.ui import flash_success, render_page_header, render_user_guide

RULE_TYPE_LABELS = {
	"weekday_count": "Weekday count",
	"weekday_pair_count": "Weekday pair count",
	"away_rotation": "Away rotation",
}
RULE_TYPE_BY_LABEL = {label: value for value, label in RULE_TYPE_LABELS.items()}


def _rule_summary(row) -> str:
	rule_type = str(row.rule_type)
	resident_name = str(row.resident_name)
	if rule_type == "weekday_count":
		weekday = WEEKDAY_NAMES.get(int(row.weekday), str(row.weekday))
		return f"{resident_name}: exactly {int(row.target_count)} {weekday} shift(s)"
	if rule_type == "weekday_pair_count":
		weekday = WEEKDAY_NAMES.get(int(row.weekday), str(row.weekday))
		paired_weekday = WEEKDAY_NAMES.get(int(row.paired_weekday), str(row.paired_weekday))
		return f"{resident_name}: exactly {int(row.target_count)} adjacent {weekday}+{paired_weekday} pair(s)"
	if rule_type == "away_rotation":
		return f"{resident_name}: away rotation"
	return f"{resident_name}: {rule_type}"


period_id = render_page_header(
	"Scheduling Rules",
	"Add month-specific scheduling rules. New rules default to hard priority.",
	month_location="rules",
)
render_user_guide(
	"Scheduling Rules",
	"""
	Use this page for month-specific rules that go beyond simple availability.

	- **Weekday count:** requires a resident to work exactly the target number of a selected weekday.
	- **Weekday pair count:** requires adjacent paired weekdays, such as one Friday+Saturday pair.
	- **Away rotation:** keeps a resident off ordinary call assignments for the month unless another hard assign/rule explicitly requires dates.
	- **Priority:** hard rules must be honored; soft rules influence the solver score but can be missed if needed.

	Example: for an away rotation with required City of Hope weekend coverage, add an Away rotation rule plus a Weekday pair count rule for Friday and Saturday with target count 1. Keep rules hard unless the solver may miss the target when needed.
	""",
)
residents = get_cached_residents(active_only=True)

if residents.empty:
	st.warning("Add active residents before entering scheduling rules.")
	st.stop()

resident_options = get_cached_resident_options(active_only=True)
rules = get_schedule_rules(period_id)

form_col, rules_col = st.columns([1, 1], gap="large")
edit_rule_key = f"edit_rule_id_{period_id}"
pending_rule_form_key = f"pending_rule_form_{period_id}"
rule_type_key = f"rule_type_label_{period_id}"
rule_resident_key = f"rule_resident_{period_id}"
rule_weekday_key = f"rule_weekday_{period_id}"
rule_paired_weekday_key = f"rule_paired_weekday_{period_id}"
rule_target_count_key = f"rule_target_count_{period_id}"
rule_priority_key = f"rule_priority_{period_id}"
rule_reason_key = f"rule_reason_{period_id}"
pending_rule_form = st.session_state.pop(pending_rule_form_key, None)
if pending_rule_form is not None:
	if pending_rule_form["edit_rule_id"] is None:
		st.session_state.pop(edit_rule_key, None)
	else:
		st.session_state[edit_rule_key] = int(pending_rule_form["edit_rule_id"])
	st.session_state[rule_type_key] = pending_rule_form["rule_type_label"]
	st.session_state[rule_resident_key] = pending_rule_form["resident"]
	st.session_state[rule_weekday_key] = pending_rule_form["weekday"]
	st.session_state[rule_paired_weekday_key] = pending_rule_form["paired_weekday"]
	st.session_state[rule_target_count_key] = pending_rule_form["target_count"]
	st.session_state[rule_priority_key] = pending_rule_form["priority"]
	st.session_state[rule_reason_key] = pending_rule_form["reason"]
if rule_type_key not in st.session_state:
	st.session_state[rule_type_key] = "Weekday count"
if rule_resident_key not in st.session_state:
	st.session_state[rule_resident_key] = next(iter(resident_options))
if rule_weekday_key not in st.session_state:
	st.session_state[rule_weekday_key] = "Friday"
if rule_paired_weekday_key not in st.session_state:
	st.session_state[rule_paired_weekday_key] = "Saturday"
if rule_target_count_key not in st.session_state:
	st.session_state[rule_target_count_key] = 1
if rule_priority_key not in st.session_state:
	st.session_state[rule_priority_key] = "hard"
if rule_reason_key not in st.session_state:
	st.session_state[rule_reason_key] = ""
edit_rule_id = st.session_state.get(edit_rule_key)
is_editing_rule = edit_rule_id is not None
if is_editing_rule and int(edit_rule_id) not in set(rules["id"].astype(int)):
	st.session_state.pop(edit_rule_key, None)
	edit_rule_id = None
	is_editing_rule = False


def _resident_label_for_id(resident_id: int) -> str:
	for label, option_resident_id in resident_options.items():
		if int(option_resident_id) == int(resident_id):
			return label
	return next(iter(resident_options))


def _queue_rule_form_reset() -> None:
	st.session_state[pending_rule_form_key] = {
		"edit_rule_id": None,
		"rule_type_label": "Weekday count",
		"resident": next(iter(resident_options)),
		"weekday": "Friday",
		"paired_weekday": "Saturday",
		"target_count": 1,
		"priority": "hard",
		"reason": "",
	}


def _load_rule_for_edit(row) -> None:
	rule_type = str(row.rule_type)
	paired_weekday = None if row.paired_weekday is None or str(row.paired_weekday) == "nan" else row.paired_weekday
	st.session_state[pending_rule_form_key] = {
		"edit_rule_id": int(row.id),
		"rule_type_label": RULE_TYPE_LABELS[rule_type],
		"resident": _resident_label_for_id(int(row.resident_id)),
		"weekday": WEEKDAY_NAMES.get(int(row.weekday), "Friday"),
		"paired_weekday": WEEKDAY_NAMES.get(int(paired_weekday), "Saturday") if paired_weekday is not None else "Saturday",
		"target_count": int(row.target_count),
		"priority": str(row.priority or "hard"),
		"reason": str(row.reason or ""),
	}

with form_col:
	st.subheader("Edit Rule" if is_editing_rule else "+ Add Rule")
	selected_rule_label = st.selectbox("Rule type", list(RULE_TYPE_BY_LABEL.keys()), key=rule_type_key)
	selected_rule_type = RULE_TYPE_BY_LABEL[selected_rule_label]

	with st.form("add_special_rule_form"):
		resident_label = st.selectbox("Resident", list(resident_options.keys()), key=rule_resident_key)
		weekday = None
		paired_weekday = None
		target_count = None

		if selected_rule_type == "weekday_count":
			weekday_label = st.selectbox("Weekday", list(WEEKDAYS.keys()), key=rule_weekday_key)
			weekday = WEEKDAYS[weekday_label]
			target_count = int(st.number_input("Target count", min_value=0, step=1, key=rule_target_count_key))
		elif selected_rule_type == "weekday_pair_count":
			weekday_label = st.selectbox("Weekday", list(WEEKDAYS.keys()), key=rule_weekday_key)
			paired_label = st.selectbox("Paired weekday", list(WEEKDAYS.keys()), key=rule_paired_weekday_key)
			weekday = WEEKDAYS[weekday_label]
			paired_weekday = WEEKDAYS[paired_label]
			target_count = int(st.number_input("Target count", min_value=0, step=1, key=rule_target_count_key))

		priority = st.selectbox("Priority", ["hard", "soft"], key=rule_priority_key)
		reason = st.text_input("Reason", key=rule_reason_key)
		button_cols = st.columns([1, 1]) if is_editing_rule else [st.container()]
		submitted = button_cols[0].form_submit_button("Save changes" if is_editing_rule else "Add rule", type="primary")
		cancelled = is_editing_rule and button_cols[1].form_submit_button("Cancel")

	if cancelled:
		_queue_rule_form_reset()
		st.rerun()

	if submitted:
		try:
			if is_editing_rule:
				update_schedule_rule(
					rule_id=int(edit_rule_id),
					period_id=period_id,
					resident_id=resident_options[resident_label],
					rule_type=selected_rule_type,
					weekday=weekday,
					paired_weekday=paired_weekday,
					target_count=target_count,
					priority=priority,
					reason=reason,
				)
			else:
				create_schedule_rule(
					period_id=period_id,
					resident_id=resident_options[resident_label],
					rule_type=selected_rule_type,
					weekday=weekday,
					paired_weekday=paired_weekday,
					target_count=target_count,
					priority=priority,
					reason=reason,
				)
		except ValueError as exc:
			st.error(str(exc))
		else:
			_queue_rule_form_reset()
			clear_month_data_cache()
			flash_success("Scheduling rule saved.")
			st.rerun()

with rules_col:
	st.subheader("Current Rules")
	if rules.empty:
		st.info("No scheduling rules have been added for this month.")
	else:
		for row in rules.itertuples():
			with st.container(border=True):
				summary_col, action_col = st.columns([5, 1])
				summary_col.markdown(f"**{_rule_summary(row)}**")
				details = [f"Priority: {str(row.priority).title()}"]
				if row.reason:
					details.append(f"Reason: {row.reason}")
				summary_col.caption(" · ".join(details))
				with action_col:
					if st.button("Edit", key=f"edit_rule_{int(row.id)}"):
						_load_rule_for_edit(row)
						st.rerun()
					if st.button("Delete", key=f"delete_rule_{int(row.id)}"):
						delete_schedule_rule(int(row.id), period_id)
						if st.session_state.get(edit_rule_key) == int(row.id):
							_queue_rule_form_reset()
						clear_month_data_cache()
						flash_success("Scheduling rule deleted.")
						st.rerun()
