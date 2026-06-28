from __future__ import annotations

import pandas as pd
import pytest

from residency_scheduler.colors import RESIDENT_COLOR_PALETTE
from residency_scheduler.db import init_db
from residency_scheduler.repository import (
	create_schedule_period,
	get_assignment_calendar,
	get_calendar_months,
	get_assignments,
	get_residents,
	get_schedule_periods,
	get_schedule_requests_for_editor,
	save_residents,
	seed_months,
	update_assignment_resident,
)
from residency_scheduler.solver import solve_period


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
	monkeypatch.setenv("RESIDENCY_SCHEDULER_DB", str(tmp_path / "test.sqlite"))
	init_db()


def test_calendar_month_seeding_creates_ten_years(isolated_db):
	seed_months(start_year=2030, years=10)

	months = get_calendar_months()
	window = months[(months["year"] >= 2030) & (months["year"] <= 2039)]
	assert len(window) == 120
	assert {"2030-01", "2039-12"}.issubset(set(window["month_key"]))


def test_multiple_drafts_can_share_year_month(isolated_db):
	first = create_schedule_period(2026, 9, "First draft", 1, None)
	second = create_schedule_period(2026, 9, "Second draft", 1, None)

	drafts = get_schedule_periods(year=2026, month=9)
	assert {first, second}.issubset(set(drafts["id"].astype(int)))
	assert set(drafts["draft_name"]) >= {"First draft", "Second draft"}


def test_empty_request_editor_has_no_placeholder_rows(isolated_db):
	period_id = create_schedule_period(2026, 10, "Empty request draft", 1, None)

	editor = get_schedule_requests_for_editor(period_id)

	assert editor.empty
	assert list(editor.columns) == ["resident", "start_date", "end_date", "request_type", "priority", "reason"]


def test_resident_edits_preserve_existing_ids(isolated_db):
	save_residents(
		pd.DataFrame(
			[
				{"name": "Ada", "email": "ada@example.com", "max_shifts": 10, "min_shifts": None, "weight": 1.0, "active": 1},
				{"name": "Ben", "email": "ben@example.com", "max_shifts": 10, "min_shifts": None, "weight": 1.0, "active": 1},
			]
		)
	)
	existing = get_residents(active_only=False)
	ada_id = int(existing.loc[existing["name"] == "Ada", "id"].iloc[0])

	edited = existing.copy()
	edited.loc[edited["id"] == ada_id, "email"] = "ada.new@example.com"
	save_residents(edited)

	updated = get_residents(active_only=False)
	assert int(updated.loc[updated["name"] == "Ada", "id"].iloc[0]) == ada_id
	assert updated.loc[updated["id"] == ada_id, "email"].iloc[0] == "ada.new@example.com"


def test_resident_colors_are_auto_assigned_and_unique(isolated_db):
	save_residents(
		pd.DataFrame(
			[
				{"name": "Ada", "email": "ada@example.com", "max_shifts": 10, "min_shifts": None, "weight": 1.0, "active": 1},
				{"name": "Ben", "email": "ben@example.com", "max_shifts": 10, "min_shifts": None, "weight": 1.0, "active": 1},
			]
		)
	)

	residents = get_residents(active_only=False)

	assert residents["color"].notna().all()
	assert len(set(residents["color"])) == len(residents)
	assert set(residents["color"]).issubset(set(RESIDENT_COLOR_PALETTE))


def test_duplicate_resident_colors_are_rejected(isolated_db):
	with pytest.raises(ValueError, match="Resident colors must be unique"):
		save_residents(
			pd.DataFrame(
				[
					{
						"name": "Ada",
						"email": "ada@example.com",
						"max_shifts": 10,
						"min_shifts": None,
						"weight": 1.0,
						"color": RESIDENT_COLOR_PALETTE[0],
						"active": 1,
					},
					{
						"name": "Ben",
						"email": "ben@example.com",
						"max_shifts": 10,
						"min_shifts": None,
						"weight": 1.0,
						"color": RESIDENT_COLOR_PALETTE[0],
						"active": 1,
					},
				]
			)
		)


def test_manual_reassignment_marks_assignment_manual_and_can_create_assign_request(isolated_db):
	save_residents(
		pd.DataFrame(
			[
				{"name": "Ada", "email": "ada@example.com", "max_shifts": 20, "min_shifts": None, "weight": 1.0, "active": 1},
				{"name": "Ben", "email": "ben@example.com", "max_shifts": 20, "min_shifts": None, "weight": 1.0, "active": 1},
			]
		)
	)
	period_id = create_schedule_period(2026, 8, "Manual draft", 1, None)
	result = solve_period(period_id, max_time_seconds=5)
	assert result.status in {"OPTIMAL", "FEASIBLE"}

	assignments = get_assignments(period_id)
	target = assignments.iloc[0]
	new_resident_id = 1 if int(target.resident_id) == 2 else 2

	update_assignment_resident(int(target.id), new_resident_id, make_locked=True, lock_reason="Test lock")

	updated = get_assignments(period_id)
	changed = updated.loc[updated["id"] == int(target.id)].iloc[0]
	requests = get_schedule_requests_for_editor(period_id)
	assert int(changed.resident_id) == new_resident_id
	assert changed.source == "manual"
	assert int(changed.is_locked) == 1
	assert len(requests) == 1
	assert requests.iloc[0]["request_type"] == "assign"


def test_assignment_calendar_returns_month_grid(isolated_db):
	save_residents(
		pd.DataFrame(
			[
				{"name": "Ada", "email": "ada@example.com", "max_shifts": 20, "min_shifts": None, "weight": 1.0, "active": 1},
				{"name": "Ben", "email": "ben@example.com", "max_shifts": 20, "min_shifts": None, "weight": 1.0, "active": 1},
			]
		)
	)
	period_id = create_schedule_period(2026, 11, "Calendar draft", 1, None)
	assert solve_period(period_id, max_time_seconds=5).assignments

	calendar_df = get_assignment_calendar(period_id)

	assert list(calendar_df.columns) == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
	assert calendar_df.astype(str).apply(lambda col: col.str.contains("Ada|Ben", regex=True)).any().any()
