from __future__ import annotations

import pandas as pd
import streamlit as st
from urllib.parse import quote

from residency_scheduler.auth import require_google_auth
from residency_scheduler.colors import RESIDENT_COLOR_PALETTE
from residency_scheduler.cache import clear_all_data_caches, ensure_database_initialized, get_cached_residents, preload_reference_data
from residency_scheduler.repository import save_residents
from residency_scheduler.ui import flash_error, flash_success, render_page_header

st.set_page_config(page_title="Residents", layout="wide")


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


def _restore_hidden_ids(edited: pd.DataFrame, original: pd.DataFrame) -> pd.DataFrame:
	restored = edited.copy()
	if "id" in restored.columns and restored["id"].notna().any():
		return restored

	original_by_position = original["id"].to_dict() if "id" in original.columns else {}
	original_by_name = {
		str(row.name).strip().lower(): row.id
		for row in original.itertuples()
		if pd.notna(row.id) and str(row.name).strip()
	}
	ids = []
	for index, row in restored.iterrows():
		resident_id = original_by_position.get(index)
		if pd.isna(resident_id):
			resident_id = original_by_name.get(str(row.get("name", "")).strip().lower())
		ids.append(resident_id)
	restored["id"] = ids
	return restored

require_google_auth()
ensure_database_initialized()
preload_reference_data()

render_page_header("Residents", "Maintain the active resident roster used by the scheduler.")

existing = get_cached_residents(active_only=False)
active_existing = existing[existing["active"].astype(int) == 1] if not existing.empty else existing

if existing.empty:
	existing = pd.DataFrame(
		[
			{"id": None, "name": "", "email": "", "max_shifts": 6, "min_shifts": None, "weight": 1, "color": "", "active": 1}
		]
	)
else:
	existing = existing[["id", "name", "email", "max_shifts", "min_shifts", "weight", "color", "active"]]

existing["weight"] = pd.to_numeric(existing["weight"], errors="coerce").fillna(1).round().clip(lower=1, upper=5).astype(int)
existing = existing.sort_values(["weight", "name"], ascending=[True, True], na_position="last").reset_index(drop=True)
existing["color_preview"] = existing["color"].apply(color_swatch_data_uri)
column_order = ["name", "weight", "min_shifts", "max_shifts", "email", "color_preview", "color", "active", "id"]

metric_cols = st.columns(4)
metric_cols[0].metric("Total residents", len(existing[existing["name"].astype(str).str.strip() != ""]))
metric_cols[1].metric("Active residents", len(active_existing))
for pgy_level in [1, 2]:
	count = 0 if active_existing.empty else int((pd.to_numeric(active_existing["weight"], errors="coerce").fillna(1).round().astype(int) == pgy_level).sum())
	metric_cols[pgy_level + 1].metric(f"PGY {pgy_level}", count)

with st.expander("Full PGY breakdown"):
	if active_existing.empty:
		st.info("No active residents.")
	else:
		breakdown = (
			pd.to_numeric(active_existing["weight"], errors="coerce")
			.fillna(1)
			.round()
			.clip(lower=1, upper=5)
			.astype(int)
			.value_counts()
			.reindex([1, 2, 3, 4, 5], fill_value=0)
			.rename_axis("PGY level")
			.reset_index(name="Active residents")
		)
		st.dataframe(breakdown, hide_index=True, width="stretch")

edited = st.data_editor(
	existing[column_order],
	num_rows="dynamic",
	width="stretch",
	column_config={
		"id": None,
		"name": st.column_config.TextColumn("Name", required=True),
		"email": st.column_config.TextColumn("Email"),
		"max_shifts": st.column_config.NumberColumn("Max", min_value=0, step=1),
		"min_shifts": st.column_config.NumberColumn("Min", min_value=0, step=1),
		"weight": st.column_config.SelectboxColumn("PGY", options=[1, 2, 3, 4, 5], required=True),
		"color_preview": st.column_config.ImageColumn("Swatch", width="small"),
		"color": st.column_config.SelectboxColumn("Color", options=RESIDENT_COLOR_PALETTE),
		"active": st.column_config.CheckboxColumn("Active"),
	},
)

if st.button("Save residents", type="primary"):
	try:
		save_residents(_restore_hidden_ids(edited, existing))
	except ValueError as exc:
		flash_error(str(exc))
	else:
		clear_all_data_caches()
		flash_success("Residents saved.")
	st.rerun()

st.info("Removing a resident row marks that resident inactive instead of deleting historical schedule data.")
