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
from residency_scheduler.ui import select_period

init_db()

st.title("Special Rules")
st.caption("Add hard or soft scheduling rules such as exactly two Fridays for a resident.")

period_id = select_period("rules")
residents = get_residents(active_only=True)

if residents.empty:
	st.warning("Add active residents before entering special rules.")
	st.stop()

resident_options = get_resident_options(active_only=True)
existing = get_schedule_rules_for_editor(period_id)

edited = st.data_editor(
	existing,
	num_rows="dynamic",
	use_container_width=True,
	column_config={
		"resident": st.column_config.SelectboxColumn("Resident", options=list(resident_options.keys()), required=True),
		"rule_type": st.column_config.SelectboxColumn("Rule type", options=["weekday_count"], required=True),
		"weekday": st.column_config.SelectboxColumn("Weekday", options=list(WEEKDAYS.keys()), required=True),
		"comparator": st.column_config.SelectboxColumn("Comparator", options=["exactly"], required=True),
		"target_count": st.column_config.NumberColumn("Target count", min_value=0, step=1, required=True),
		"priority": st.column_config.SelectboxColumn("Priority", options=["hard", "soft"], required=True),
		"reason": st.column_config.TextColumn("Reason"),
	},
)

if st.button("Save special rules", type="primary"):
	try:
		replace_schedule_rules(period_id, edited)
	except ValueError as exc:
		st.error(str(exc))
	else:
		st.success("Special rules saved.")
		st.rerun()

st.info("For City of Hope Friday coverage, add a weekday_count rule with Friday, exactly, target count 2, and hard priority.")
