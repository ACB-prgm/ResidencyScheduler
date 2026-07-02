from __future__ import annotations

import streamlit as st

from residency_scheduler.auth import require_google_auth
from residency_scheduler.cache import (
	clear_month_data_cache,
	ensure_database_initialized,
	get_cached_resident_options,
	get_cached_residents,
	preload_reference_data,
)
from residency_scheduler.repository import (
	WEEKDAYS,
	WEEKDAY_NAMES,
	create_schedule_rule,
	delete_schedule_rule,
	get_schedule_rules,
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


require_google_auth()
ensure_database_initialized()
preload_reference_data()

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

with form_col:
	st.subheader("+ Add Rule")
	selected_rule_label = st.selectbox("Rule type", list(RULE_TYPE_BY_LABEL.keys()), key="new_special_rule_type")
	selected_rule_type = RULE_TYPE_BY_LABEL[selected_rule_label]

	with st.form("add_special_rule_form", clear_on_submit=True):
		resident_label = st.selectbox("Resident", list(resident_options.keys()))
		weekday = None
		paired_weekday = None
		target_count = None

		if selected_rule_type == "weekday_count":
			weekday_label = st.selectbox("Weekday", list(WEEKDAYS.keys()))
			weekday = WEEKDAYS[weekday_label]
			target_count = int(st.number_input("Target count", min_value=0, step=1, value=1))
		elif selected_rule_type == "weekday_pair_count":
			weekday_label = st.selectbox("Weekday", list(WEEKDAYS.keys()), index=list(WEEKDAYS.keys()).index("Friday"))
			paired_label = st.selectbox("Paired weekday", list(WEEKDAYS.keys()), index=list(WEEKDAYS.keys()).index("Saturday"))
			weekday = WEEKDAYS[weekday_label]
			paired_weekday = WEEKDAYS[paired_label]
			target_count = int(st.number_input("Target count", min_value=0, step=1, value=1))

		priority = st.selectbox("Priority", ["hard", "soft"], index=0)
		reason = st.text_input("Reason")
		submitted = st.form_submit_button("Add rule", type="primary")

	if submitted:
		try:
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
			clear_month_data_cache()
			flash_success("Scheduling rule added.")
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
				if action_col.button("Delete", key=f"delete_rule_{int(row.id)}"):
					delete_schedule_rule(int(row.id), period_id)
					clear_month_data_cache()
					flash_success("Scheduling rule deleted.")
					st.rerun()
