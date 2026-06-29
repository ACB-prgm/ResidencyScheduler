from __future__ import annotations

import pandas as pd
import pytest

from residency_scheduler.colors import RESIDENT_COLOR_PALETTE
from residency_scheduler.db import init_db
from residency_scheduler.repository import (
	create_schedule_period,
	delete_schedule_period,
	get_assignment_calendar,
	get_calendar_months,
	get_assignments,
	get_prior_assignment_history,
	get_residents,
	get_schedule_periods,
	get_schedule_requests_for_editor,
	rename_schedule_period,
	replace_schedule_requests,
	save_assignments,
	save_residents,
	seed_months,
	swap_assignment_residents,
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


def test_rename_and_delete_draft_updates_drafts_and_cascades_assignments(isolated_db):
	save_residents(
		pd.DataFrame(
			[
				{"name": "Ada", "email": "ada@example.com", "max_shifts": 10, "min_shifts": None, "weight": 1, "active": 1},
			]
		)
	)
	period_id = create_schedule_period(2026, 9, "Old name", 1, None)
	save_assignments(period_id, [{"work_date": "2026-09-01", "resident_id": 1}])

	rename_schedule_period(period_id, "New name")
	drafts = get_schedule_periods(year=2026, month=9)

	assert drafts.loc[drafts["id"].astype(int) == period_id, "draft_name"].iloc[0] == "New name"

	delete_schedule_period(period_id)

	assert period_id not in set(get_schedule_periods(year=2026, month=9)["id"].astype(int))
	assert get_assignments(period_id).empty


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


def test_resident_pgy_levels_are_normalized_to_one_through_five(isolated_db):
	save_residents(
		pd.DataFrame(
			[
				{"name": "Ada", "email": "ada@example.com", "max_shifts": 10, "min_shifts": None, "weight": 0.4, "active": 1},
				{"name": "Ben", "email": "ben@example.com", "max_shifts": 10, "min_shifts": None, "weight": 7, "active": 1},
				{"name": "Cam", "email": "cam@example.com", "max_shifts": 10, "min_shifts": None, "weight": 3.4, "active": 1},
			]
		)
	)

	residents = get_residents(active_only=False).sort_values("name")

	assert residents["weight"].astype(int).tolist() == [1, 5, 3]


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


def test_swap_assignment_residents_swaps_unlocked_assignments_and_can_lock(isolated_db):
	save_residents(
		pd.DataFrame(
			[
				{"name": "Ada", "email": "ada@example.com", "max_shifts": 20, "min_shifts": None, "weight": 1, "active": 1},
				{"name": "Ben", "email": "ben@example.com", "max_shifts": 20, "min_shifts": None, "weight": 1, "active": 1},
			]
		)
	)
	period_id = create_schedule_period(2026, 8, "Swap draft", 1, None)
	save_assignments(
		period_id,
		[
			{"work_date": "2026-08-01", "resident_id": 1},
			{"work_date": "2026-08-02", "resident_id": 2},
		],
	)
	assignments = get_assignments(period_id)
	first_id = int(assignments.loc[assignments["work_date"] == "2026-08-01", "id"].iloc[0])
	second_id = int(assignments.loc[assignments["work_date"] == "2026-08-02", "id"].iloc[0])

	swap_assignment_residents(first_id, second_id, make_locked=True, lock_reason="Swap lock")

	updated = get_assignments(period_id)
	requests = get_schedule_requests_for_editor(period_id)
	assert int(updated.loc[updated["id"] == first_id, "resident_id"].iloc[0]) == 2
	assert int(updated.loc[updated["id"] == second_id, "resident_id"].iloc[0]) == 1
	assert int(updated.loc[updated["id"] == first_id, "is_locked"].iloc[0]) == 1
	assert len(requests) == 2
	assert set(requests["request_type"]) == {"assign"}


def test_swap_assignment_residents_rejects_hard_unavailable_target(isolated_db):
	save_residents(
		pd.DataFrame(
			[
				{"name": "Ada", "email": "ada@example.com", "max_shifts": 20, "min_shifts": None, "weight": 1, "active": 1},
				{"name": "Ben", "email": "ben@example.com", "max_shifts": 20, "min_shifts": None, "weight": 1, "active": 1},
			]
		)
	)
	period_id = create_schedule_period(2026, 8, "Swap unavailable draft", 1, None)
	save_assignments(
		period_id,
		[
			{"work_date": "2026-08-01", "resident_id": 1},
			{"work_date": "2026-08-02", "resident_id": 2},
		],
	)
	replace_schedule_requests(
		period_id,
		pd.DataFrame(
			[
				{
					"resident": "Ada · resident #1",
					"start_date": "2026-08-02",
					"end_date": "2026-08-02",
					"request_type": "unavailable",
					"priority": "hard",
					"reason": "Cannot swap here",
				}
			]
		),
	)
	assignments = get_assignments(period_id)

	with pytest.raises(ValueError, match="hard unavailable"):
		swap_assignment_residents(int(assignments.iloc[0].id), int(assignments.iloc[1].id))


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


def test_prior_assignment_history_skips_missing_months_and_uses_latest_draft(isolated_db):
	save_residents(
		pd.DataFrame(
			[
				{"name": "Ada", "email": "ada@example.com", "max_shifts": 20, "min_shifts": None, "weight": 1.0, "active": 1},
				{"name": "Ben", "email": "ben@example.com", "max_shifts": 20, "min_shifts": None, "weight": 1.0, "active": 1},
			]
		)
	)
	june_id = create_schedule_period(2026, 6, "June draft", 1, None)
	july_id = create_schedule_period(2026, 7, "July empty draft", 1, None)
	august_old_id = create_schedule_period(2026, 8, "August old draft", 1, None)
	august_latest_id = create_schedule_period(2026, 8, "August latest draft", 1, None)
	current_id = create_schedule_period(2026, 9, "Current draft", 1, None)
	save_assignments(
		june_id,
		[
			{"work_date": "2026-06-06", "resident_id": 1},
			{"work_date": "2026-06-08", "resident_id": 2},
		],
	)
	save_assignments(august_old_id, [{"work_date": "2026-08-01", "resident_id": 1}])
	save_assignments(august_latest_id, [{"work_date": "2026-08-02", "resident_id": 2}])

	history = get_prior_assignment_history(current_id, months=3)

	assert set(history["period_id"].astype(int)) == {june_id, august_latest_id}
	assert july_id not in set(history["period_id"].astype(int))
	assert august_old_id not in set(history["period_id"].astype(int))
	assert history.loc[history["work_date"] == "2026-06-06", "is_weekend"].iloc[0] == 1
	assert history.loc[history["work_date"] == "2026-06-08", "is_weekend"].iloc[0] == 0
