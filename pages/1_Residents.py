from __future__ import annotations

import pandas as pd
import streamlit as st
from urllib.parse import quote

from residency_scheduler.colors import RESIDENT_COLOR_PALETTE
from residency_scheduler.db import init_db
from residency_scheduler.repository import get_residents, save_residents


def color_swatch_data_uri(color: str | None) -> str:
	if not color:
		return ""
	value = str(color).strip()
	if not value.startswith("#"):
		return ""
	svg = (
		"<svg xmlns='http://www.w3.org/2000/svg' width='64' height='24' viewBox='0 0 64 24'>"
		f"<rect x='1' y='1' width='62' height='22' rx='4' fill='{value}' stroke='#D1D5DB'/>"
		"</svg>"
	)
	return "data:image/svg+xml;utf8," + quote(svg)

init_db()

st.title("Residents")
st.caption("Maintain the active resident roster used by the scheduler.")

existing = get_residents(active_only=False)

if existing.empty:
	existing = pd.DataFrame(
		[
			{"name": "", "email": "", "max_shifts": 6, "min_shifts": None, "weight": 1.0, "color": "", "active": 1}
		]
	)
else:
	existing = existing[["id", "name", "email", "max_shifts", "min_shifts", "weight", "color", "active"]]

existing["color_preview"] = existing["color"].apply(color_swatch_data_uri)
column_order = ["id", "name", "email", "max_shifts", "min_shifts", "weight", "color_preview", "color", "active"]

edited = st.data_editor(
	existing[column_order],
	num_rows="dynamic",
	use_container_width=True,
	column_config={
		"id": st.column_config.NumberColumn("ID", disabled=True),
		"name": st.column_config.TextColumn("Name", required=True),
		"email": st.column_config.TextColumn("Email"),
		"max_shifts": st.column_config.NumberColumn("Max shifts", min_value=0, step=1),
		"min_shifts": st.column_config.NumberColumn("Min shifts", min_value=0, step=1),
		"weight": st.column_config.NumberColumn("Weight", min_value=0.1, step=0.1),
		"color_preview": st.column_config.ImageColumn("Swatch", width="small"),
		"color": st.column_config.SelectboxColumn("Color", options=RESIDENT_COLOR_PALETTE),
		"active": st.column_config.CheckboxColumn("Active"),
	},
)

if st.button("Save residents", type="primary"):
	try:
		save_residents(edited)
	except ValueError as exc:
		st.error(str(exc))
	else:
		st.success("Residents saved.")
		st.rerun()

st.info("Removing a resident row marks that resident inactive instead of deleting historical schedule data.")
