from __future__ import annotations

import streamlit as st

from residency_scheduler.db import init_db
from residency_scheduler.repository import (
	WEEKDAYS,
	get_resident_options,
	get_residents,
	get_schedule_rules_for_editor,
	replace_schedule_rules,
)
from residency_scheduler.ui import flash_success, render_flash_messages, select_period

EDITOR_KEY = "special_rules_editor"
EDITOR_DATA_KEY = "special_rules_editor_data"
EDITOR_PERIOD_KEY = "special_rules_editor_period_id"
EDITOR_VERSION_KEY = "special_rules_editor_version"

init_db()

st.title("Special Rules")
st.caption("Add hard or soft scheduling rules such as weekday counts or adjacent weekday pairs.")
render_flash_messages()

period_id = select_period("rules")
residents = get_residents(active_only=True)

if residents.empty:
	st.warning("Add active residents before entering special rules.")
	st.stop()

resident_options = get_resident_options(active_only=True)
existing = get_schedule_rules_for_editor(period_id)
if st.session_state.get(EDITOR_PERIOD_KEY) != period_id:
	st.session_state[EDITOR_PERIOD_KEY] = period_id
	st.session_state[EDITOR_DATA_KEY] = existing
	st.session_state[EDITOR_VERSION_KEY] = 0

editor_version = int(st.session_state.get(EDITOR_VERSION_KEY, 0))
edited = st.data_editor(
	st.session_state[EDITOR_DATA_KEY],
	key=f"{EDITOR_KEY}_{editor_version}",
	num_rows="dynamic",
	use_container_width=True,
	column_config={
		"resident": st.column_config.SelectboxColumn("Resident", options=list(resident_options.keys()), required=True),
		"rule_type": st.column_config.SelectboxColumn("Rule type", options=["weekday_count", "weekday_pair_count"], required=True),
		"weekday": st.column_config.SelectboxColumn("Weekday", options=list(WEEKDAYS.keys()), required=True),
		"paired_weekday": st.column_config.SelectboxColumn("Paired weekday", options=list(WEEKDAYS.keys())),
		"comparator": st.column_config.SelectboxColumn("Comparator", options=["exactly"], required=True),
		"target_count": st.column_config.NumberColumn("Target count", min_value=0, step=1, required=True),
		"priority": st.column_config.SelectboxColumn("Priority", options=["hard", "soft"], required=True),
		"reason": st.column_config.TextColumn("Reason"),
	},
)
st.session_state[EDITOR_DATA_KEY] = edited

if st.button("Save special rules", type="primary"):
	try:
		replace_schedule_rules(period_id, edited)
	except ValueError as exc:
		st.error(str(exc))
	else:
		flash_success("Special rules saved.")
		st.session_state[EDITOR_PERIOD_KEY] = None
		st.rerun()

st.info(
	"For City of Hope weekend coverage, add a weekday_pair_count rule with Friday, Saturday, exactly, target count 1, and hard priority."
)
