from __future__ import annotations

import pandas as pd
import streamlit as st

from residency_scheduler.db import init_db
from residency_scheduler.repository import (
	get_locked_assignments,
	get_residents,
	get_schedule_periods,
	replace_locked_assignments,
)

init_db()

st.title("Locked Assignments")
st.caption("Preassign residents to specific dates. These assignments are hard constraints and will not be moved by the solver.")

periods = get_schedule_periods()
residents = get_residents(active_only=True)

if periods.empty:
	st.warning("Create a schedule period on the home page first.")
	st.stop()

if residents.empty:
	st.warning("Add active residents before entering locked assignments.")
	st.stop()

period_options = {
	f"{row.year}-{row.month:02d} · period #{row.id}": int(row.id)
	for row in periods.itertuples()
}
period_label = st.selectbox("Schedule period", options=list(period_options.keys()))
period_id = period_options[period_label]

st.markdown("### Resident IDs")
st.dataframe(residents[["id", "name", "email"]], use_container_width=True, hide_index=True)

period_row = periods.loc[periods["id"] == period_id].iloc[0]
existing = get_locked_assignments(period_id)
if existing.empty:
	existing = pd.DataFrame(
		[
			{
				"work_date": f"{int(period_row.year)}-{int(period_row.month):02d}-01",
				"resident_id": int(residents.iloc[0]["id"]),
				"reason": "",
			}
		]
	)
else:
	existing = existing[["work_date", "resident_id", "reason"]]

edited = st.data_editor(
	existing,
	num_rows="dynamic",
	use_container_width=True,
	column_config={
		"work_date": st.column_config.DateColumn("Date", required=True),
		"resident_id": st.column_config.NumberColumn("Resident ID", min_value=1, step=1, required=True),
		"reason": st.column_config.TextColumn("Reason"),
	},
)

if st.button("Save locked assignments", type="primary"):
	try:
		replace_locked_assignments(period_id, edited)
	except ValueError as exc:
		st.error(str(exc))
	else:
		st.success("Locked assignments saved.")
		st.rerun()

st.info("Locked assignments become hard constraints. If a locked assignment conflicts with vacation/hard unavailable, schedule generation will fail validation.")
