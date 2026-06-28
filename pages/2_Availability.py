from __future__ import annotations

import pandas as pd
import streamlit as st

from residency_scheduler.db import init_db
from residency_scheduler.repository import (
	get_availability,
	get_residents,
	get_schedule_periods,
	replace_availability,
)

init_db()

st.title("Availability & Preferences")
st.caption("Enter hard exceptions and soft resident preferences for a schedule period.")

periods = get_schedule_periods()
residents = get_residents(active_only=True)

if periods.empty:
	st.warning("Create a schedule period on the home page first.")
	st.stop()

if residents.empty:
	st.warning("Add active residents before entering availability.")
	st.stop()

period_options = {
	f"{row.year}-{row.month:02d} · period #{row.id}": int(row.id)
	for row in periods.itertuples()
}
period_label = st.selectbox("Schedule period", options=list(period_options.keys()))
period_id = period_options[period_label]

resident_options = {row.name: int(row.id) for row in residents.itertuples()}
st.markdown("### Resident IDs")
st.dataframe(residents[["id", "name", "email"]], use_container_width=True, hide_index=True)

existing = get_availability(period_id)
if existing.empty:
	existing = pd.DataFrame(
		[
			{
				"resident_id": list(resident_options.values())[0],
				"work_date": f"{periods.loc[periods['id'] == period_id, 'year'].iloc[0]}-{periods.loc[periods['id'] == period_id, 'month'].iloc[0]:02d}-01",
				"availability_type": "prefer_off",
				"priority": "soft",
				"reason": "",
			}
		]
	)
else:
	existing = existing[["resident_id", "work_date", "availability_type", "priority", "reason"]]

edited = st.data_editor(
	existing,
	num_rows="dynamic",
	use_container_width=True,
	column_config={
		"resident_id": st.column_config.NumberColumn("Resident ID", min_value=1, step=1, required=True),
		"work_date": st.column_config.DateColumn("Date", required=True),
		"availability_type": st.column_config.SelectboxColumn(
			"Type",
			options=["vacation", "unavailable", "approved_absence", "medical_leave", "prefer_off", "prefer_work"],
			required=True,
		),
		"priority": st.column_config.SelectboxColumn("Priority", options=["hard", "soft"], required=True),
		"reason": st.column_config.TextColumn("Reason"),
	},
)

if st.button("Save availability", type="primary"):
	try:
		replace_availability(period_id, edited)
	except ValueError as exc:
		st.error(str(exc))
	else:
		st.success("Availability/preferences saved.")
		st.rerun()

st.markdown(
	"""
	### Priority guidance

	- `hard`: vacation, approved leave, or true unavailability. The solver must not assign the resident.
	- `soft`: preference. The solver may violate it if needed for coverage/fairness.
	"""
)
