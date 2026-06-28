from __future__ import annotations

import streamlit as st

from residency_scheduler.calendar.google_calendar import build_calendar_events_preview, google_calendar_setup_instructions
from residency_scheduler.db import init_db
from residency_scheduler.repository import get_assignments, get_period, get_schedule_periods

init_db()

st.title("Publish")
st.caption("Preview Google Calendar events for an approved period.")

periods = get_schedule_periods()
if periods.empty:
	st.warning("Create a schedule period first.")
	st.stop()

options = {f"{row.year}-{row.month:02d} · period #{row.id}": int(row.id) for row in periods.itertuples()}
period_id = options[st.selectbox("Schedule period", list(options.keys()))]
period = get_period(period_id)
assignments = get_assignments(period_id)

if assignments.empty:
	st.info("No assignments exist yet. Generate and review the schedule before publishing.")
	st.stop()

st.markdown("### Calendar target")
st.write(period.get("google_calendar_id") or "No calendar ID configured yet.")

st.markdown("### Event preview")
events = build_calendar_events_preview(assignments)
st.dataframe(events, use_container_width=True, hide_index=True)

st.markdown("### Publishing setup")
st.info(google_calendar_setup_instructions())
st.warning("This page currently previews event payloads only. The calendar module supports upsert publishing once an authenticated Google Calendar service is wired in.")
