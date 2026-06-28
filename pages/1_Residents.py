from __future__ import annotations

import pandas as pd
import streamlit as st

from residency_scheduler.db import init_db
from residency_scheduler.repository import get_residents, replace_residents

init_db()

st.title("Residents")
st.caption("Maintain the active resident roster used by the scheduler.")

existing = get_residents(active_only=False)

if existing.empty:
	existing = pd.DataFrame(
		[
			{"name": "", "email": "", "max_shifts": 6, "min_shifts": None, "weight": 1.0, "active": 1}
		]
	)
else:
	existing = existing.drop(columns=["id"], errors="ignore")

edited = st.data_editor(
	existing,
	num_rows="dynamic",
	use_container_width=True,
	column_config={
		"name": st.column_config.TextColumn("Name", required=True),
		"email": st.column_config.TextColumn("Email"),
		"max_shifts": st.column_config.NumberColumn("Max shifts", min_value=0, step=1),
		"min_shifts": st.column_config.NumberColumn("Min shifts", min_value=0, step=1),
		"weight": st.column_config.NumberColumn("Weight", min_value=0.1, step=0.1),
		"active": st.column_config.CheckboxColumn("Active"),
	},
)

if st.button("Save residents", type="primary"):
	replace_residents(edited)
	st.success("Residents saved.")
	st.rerun()

st.info("For the MVP, this page rewrites the roster table when saved. Later this should become row-level create/update/delete to preserve resident IDs across edits.")
