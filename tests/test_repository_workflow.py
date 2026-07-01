from __future__ import annotations

import pandas as pd
import pytest

from residency_scheduler.colors import RESIDENT_COLOR_PALETTE
from residency_scheduler.db import init_db
from residency_scheduler.repository import (
	get_or_create_schedule_period,
	get_assignment_calendar,
	get_calendar_months,
	get_assignments,
	get_period,
	get_prior_assignment_history,
	get_resident_options,
	get_residents,
	get_user_default_google_calendar_id,
	get_schedule_rules,
	get_schedule_periods,
	get_schedule_requests_for_editor,
	get_workload_summary,
	replace_schedule_requests,
	replace_schedule_rules,
	save_assignments,
	save_residents,
	set_user_default_google_calendar_id,
	seed_months,
	swap_assignment_residents,
	update_assignment_resident,
	update_schedule_period_settings,
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


def test_schedule_period_is_unique_per_year_month(isolated_db):
	first = get_or_create_schedule_period(2026, 9, required_count=1)
	second = get_or_create_schedule_period(2026, 9, required_count=3)

	periods = get_schedule_periods(year=2026, month=9)
	assert first == second
	assert len(periods) == 1
	assert int(periods.iloc[0]["id"]) == first


def test_month_settings_update_selected_period(isolated_db):
	save_residents(
		pd.DataFrame(
			[
				{"name": "Ada", "email": "ada@example.com", "max_shifts": 10, "min_shifts": None, "weight": 1, "active": 1},
			]
		)
	)
	period_id = get_or_create_schedule_period(2026, 9, required_count=1)
	save_assignments(period_id, [{"work_date": "2026-09-01", "resident_id": 1}])

	update_schedule_period_settings(period_id, required_count=2, google_calendar_id="calendar@example.com")
	period = get_period(period_id)

	assert int(period["required_count"]) == 2
	assert period["google_calendar_id"] == "calendar@example.com"
	assert not get_assignments(period_id).empty


def test_user_default_google_calendar_is_scoped_by_google_subject(isolated_db):
	set_user_default_google_calendar_id("sub-one", "calendar-one@example.com")
	set_user_default_google_calendar_id("sub-two", "calendar-two@example.com")

	assert get_user_default_google_calendar_id("sub-one") == "calendar-one@example.com"
	assert get_user_default_google_calendar_id("sub-two") == "calendar-two@example.com"
	assert get_user_default_google_calendar_id("missing-sub") is None


def test_empty_request_editor_has_no_placeholder_rows(isolated_db):
	period_id = get_or_create_schedule_period(2026, 10, required_count=1)

	editor = get_schedule_requests_for_editor(period_id)

	assert editor.empty
	assert list(editor.columns) == ["resident", "start_date", "end_date", "request_type", "priority", "reason"]


def test_away_rotation_rule_saves_without_weekday_fields_and_defaults_hard(isolated_db):
	save_residents(
		pd.DataFrame(
			[
				{"name": "Ada", "email": "ada@example.com", "max_shifts": 10, "min_shifts": None, "weight": 1, "active": 1},
			]
		)
	)
	period_id = get_or_create_schedule_period(2026, 10, required_count=1)

	replace_schedule_rules(
		period_id,
		pd.DataFrame(
			[
				{
					"resident": "Ada · resident #1",
					"rule_type": "away_rotation",
					"priority": "",
					"reason": "Away rotation",
				}
			]
		),
	)

	rules = get_schedule_rules(period_id)
	assert len(rules) == 1
	assert rules.iloc[0]["rule_type"] == "away_rotation"
	assert rules.iloc[0]["priority"] == "hard"
	assert int(rules.iloc[0]["target_count"]) == 0


def test_weekday_rule_still_requires_weekday(isolated_db):
	save_residents(
		pd.DataFrame(
			[
				{"name": "Ada", "email": "ada@example.com", "max_shifts": 10, "min_shifts": None, "weight": 1, "active": 1},
			]
		)
	)
	period_id = get_or_create_schedule_period(2026, 10, required_count=1)

	with pytest.raises(ValueError, match="Weekday is required"):
		replace_schedule_rules(
			period_id,
			pd.DataFrame(
				[
					{
						"resident": "Ada · resident #1",
						"rule_type": "weekday_count",
						"priority": "hard",
						"target_count": 1,
						"reason": "",
					}
				]
			),
		)


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


def test_resident_save_restores_hidden_ids_by_name(isolated_db):
	save_residents(
		pd.DataFrame(
			[
				{"name": "Ada", "email": "ada@example.com", "max_shifts": 10, "min_shifts": None, "weight": 1.0, "active": 1},
				{"name": "Ben", "email": "ben@example.com", "max_shifts": 10, "min_shifts": None, "weight": 1.0, "active": 1},
			]
		)
	)
	original = get_residents(active_only=False)
	ada_id = int(original.loc[original["name"] == "Ada", "id"].iloc[0])

	edited_without_ids = original.drop(columns=["id"]).copy()
	edited_without_ids.loc[edited_without_ids["name"] == "Ada", "email"] = "ada.hidden@example.com"
	save_residents(edited_without_ids)

	updated = get_residents(active_only=False)
	assert len(updated) == 2
	assert int(updated.loc[updated["name"] == "Ada", "id"].iloc[0]) == ada_id
	assert updated.loc[updated["id"] == ada_id, "email"].iloc[0] == "ada.hidden@example.com"


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


def test_resident_options_use_names_without_internal_ids(isolated_db):
	save_residents(
		pd.DataFrame(
			[
				{"name": "Ada", "email": "ada@example.com", "max_shifts": 10, "min_shifts": None, "weight": 1.0, "active": 1},
				{"name": "Ben", "email": "ben@example.com", "max_shifts": 10, "min_shifts": None, "weight": 1.0, "active": 1},
			]
		)
	)

	options = get_resident_options(active_only=True)

	assert set(options) == {"Ada", "Ben"}
	assert all("resident #" not in label for label in options)


def test_duplicate_resident_names_are_rejected(isolated_db):
	with pytest.raises(ValueError, match="Resident names must be unique"):
		save_residents(
			pd.DataFrame(
				[
					{"name": "Ada", "email": "ada@example.com", "max_shifts": 10, "min_shifts": None, "weight": 1.0, "active": 1},
					{"name": "ada", "email": "ada2@example.com", "max_shifts": 10, "min_shifts": None, "weight": 1.0, "active": 1},
				]
			)
		)


def test_resident_min_shift_above_max_shift_is_rejected(isolated_db):
	with pytest.raises(ValueError, match="Minimum shifts cannot exceed maximum shifts"):
		save_residents(
			pd.DataFrame(
				[
					{"name": "Ada", "email": "ada@example.com", "max_shifts": 3, "min_shifts": 4, "weight": 1.0, "active": 1},
				]
			)
		)


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
	period_id = get_or_create_schedule_period(2026, 8, required_count=1)
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
	period_id = get_or_create_schedule_period(2026, 8, required_count=1)
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
	period_id = get_or_create_schedule_period(2026, 8, required_count=1)
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
	period_id = get_or_create_schedule_period(2026, 11, required_count=1)
	assert solve_period(period_id, max_time_seconds=5).assignments

	calendar_df = get_assignment_calendar(period_id)

	assert list(calendar_df.columns) == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
	assert calendar_df.astype(str).apply(lambda col: col.str.contains("Ada|Ben", regex=True)).any().any()


def test_prior_assignment_history_skips_missing_months_with_single_month_periods(isolated_db):
	save_residents(
		pd.DataFrame(
			[
				{"name": "Ada", "email": "ada@example.com", "max_shifts": 20, "min_shifts": None, "weight": 1.0, "active": 1},
				{"name": "Ben", "email": "ben@example.com", "max_shifts": 20, "min_shifts": None, "weight": 1.0, "active": 1},
			]
		)
	)
	june_id = get_or_create_schedule_period(2026, 6, required_count=1)
	july_id = get_or_create_schedule_period(2026, 7, required_count=1)
	august_id = get_or_create_schedule_period(2026, 8, required_count=1)
	current_id = get_or_create_schedule_period(2026, 9, required_count=1)
	save_assignments(
		june_id,
		[
			{"work_date": "2026-06-05", "resident_id": 1},
			{"work_date": "2026-06-06", "resident_id": 1},
			{"work_date": "2026-06-08", "resident_id": 2},
		],
	)
	save_assignments(august_id, [{"work_date": "2026-08-02", "resident_id": 2}])

	history = get_prior_assignment_history(current_id, months=3)

	assert set(history["period_id"].astype(int)) == {june_id, august_id}
	assert july_id not in set(history["period_id"].astype(int))
	assert history.loc[history["work_date"] == "2026-06-05", "is_weekend"].iloc[0] == 1
	assert history.loc[history["work_date"] == "2026-06-06", "is_weekend"].iloc[0] == 1
	assert history.loc[history["work_date"] == "2026-06-08", "is_weekend"].iloc[0] == 0


def test_workload_summary_counts_friday_as_weekend(isolated_db):
	save_residents(
		pd.DataFrame(
			[
				{"name": "Ada", "email": "ada@example.com", "max_shifts": 20, "min_shifts": None, "weight": 1.0, "active": 1},
				{"name": "Ben", "email": "ben@example.com", "max_shifts": 20, "min_shifts": None, "weight": 1.0, "active": 1},
			]
		)
	)
	period_id = get_or_create_schedule_period(2026, 6, required_count=1)
	save_assignments(
		period_id,
		[
			{"work_date": "2026-06-05", "resident_id": 1},
			{"work_date": "2026-06-08", "resident_id": 1},
			{"work_date": "2026-06-06", "resident_id": 2},
		],
	)

	summary = get_workload_summary(period_id)

	assert int(summary.loc[summary["resident_name"] == "Ada", "weekend_shifts"].iloc[0]) == 1
	assert int(summary.loc[summary["resident_name"] == "Ben", "weekend_shifts"].iloc[0]) == 1
