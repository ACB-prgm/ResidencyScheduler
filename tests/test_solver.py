from __future__ import annotations

from collections import Counter

import pandas as pd
import pytest

from residency_scheduler.db import init_db
from residency_scheduler.repository import (
	create_schedule_period,
	get_assignments,
	get_preference_violations,
	replace_availability,
	replace_locked_assignments,
	save_residents,
)
from residency_scheduler.solver import solve_period


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
	monkeypatch.setenv("RESIDENCY_SCHEDULER_DB", str(tmp_path / "test.sqlite"))
	init_db()


def add_residents(names: list[str], max_shifts: int | None = 10) -> None:
	save_residents(
		pd.DataFrame(
			[
				{
					"name": name,
					"email": f"{name.lower()}@example.com",
					"max_shifts": max_shifts,
					"min_shifts": None,
					"weight": 1.0,
					"active": 1,
				}
				for name in names
			]
		)
	)


def assignments_by_date(period_id: int) -> dict[str, list[int]]:
	assignments = get_assignments(period_id)
	grouped: dict[str, list[int]] = {}
	for row in assignments.itertuples():
		grouped.setdefault(str(row.work_date), []).append(int(row.resident_id))
	return grouped


def test_normal_feasible_schedule_covers_every_day(isolated_db):
	add_residents(["Ada", "Ben", "Cam", "Dee"], max_shifts=10)
	period_id = create_schedule_period(2026, 1, 1, None)

	result = solve_period(period_id, max_time_seconds=5)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assert len(result.assignments) == 31
	assert all(len(resident_ids) == 1 for resident_ids in assignments_by_date(period_id).values())


def test_hard_unavailable_is_honored(isolated_db):
	add_residents(["Ada", "Ben", "Cam", "Dee"], max_shifts=10)
	period_id = create_schedule_period(2026, 2, 1, None)
	replace_availability(
		period_id,
		pd.DataFrame(
			[
				{
					"resident_id": 1,
					"work_date": "2026-02-10",
					"availability_type": "vacation",
					"priority": "hard",
					"reason": "PTO",
				}
			]
		),
	)

	result = solve_period(period_id, max_time_seconds=5)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assert 1 not in assignments_by_date(period_id)["2026-02-10"]


def test_locked_assignment_is_honored(isolated_db):
	add_residents(["Ada", "Ben", "Cam", "Dee"], max_shifts=10)
	period_id = create_schedule_period(2026, 3, 1, None)
	replace_locked_assignments(
		period_id,
		pd.DataFrame([{"work_date": "2026-03-05", "resident_id": 2, "reason": "Chief request"}]),
	)

	result = solve_period(period_id, max_time_seconds=5)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assert assignments_by_date(period_id)["2026-03-05"] == [2]


def test_locked_assignment_conflict_with_hard_unavailable_is_rejected(isolated_db):
	add_residents(["Ada", "Ben", "Cam", "Dee"], max_shifts=10)
	period_id = create_schedule_period(2026, 4, 1, None)
	replace_availability(
		period_id,
		pd.DataFrame(
			[
				{
					"resident_id": 1,
					"work_date": "2026-04-02",
					"availability_type": "unavailable",
					"priority": "hard",
					"reason": "Clinic",
				}
			]
		),
	)

	with pytest.raises(ValueError, match="Locked assignment conflict"):
		replace_locked_assignments(
			period_id,
			pd.DataFrame([{"work_date": "2026-04-02", "resident_id": 1, "reason": "Conflict"}]),
		)


def test_too_many_locked_assignments_on_one_date_is_rejected(isolated_db):
	add_residents(["Ada", "Ben", "Cam"], max_shifts=12)
	period_id = create_schedule_period(2026, 5, 1, None)

	with pytest.raises(ValueError, match="locked assignments"):
		replace_locked_assignments(
			period_id,
			pd.DataFrame(
				[
					{"work_date": "2026-05-01", "resident_id": 1, "reason": ""},
					{"work_date": "2026-05-01", "resident_id": 2, "reason": ""},
				]
			),
		)


def test_max_shifts_can_make_schedule_infeasible(isolated_db):
	add_residents(["Ada", "Ben"], max_shifts=5)
	period_id = create_schedule_period(2026, 6, 1, None)

	result = solve_period(period_id, max_time_seconds=5)

	assert result.status == "INVALID_INPUT"
	assert any("Configured max shifts" in warning for warning in result.warnings)


def test_preference_heavy_schedule_remains_feasible_and_reports_violations(isolated_db):
	add_residents(["Ada", "Ben", "Cam"], max_shifts=12)
	period_id = create_schedule_period(2026, 2, 1, None)
	preferences = [
		{
			"resident_id": resident_id,
			"work_date": f"2026-02-{day:02d}",
			"availability_type": "prefer_off",
			"priority": "soft",
			"reason": "Preference",
		}
		for day in range(1, 8)
		for resident_id in [1, 2]
	]
	replace_availability(period_id, pd.DataFrame(preferences))

	result = solve_period(period_id, max_time_seconds=5)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assert len(result.assignments) == 28
	assert set(get_preference_violations(period_id).columns) >= {"work_date", "resident_name", "availability_type"}


def test_back_to_back_is_avoided_when_enough_residents_exist(isolated_db):
	add_residents([f"Resident {index}" for index in range(1, 32)], max_shifts=1)
	period_id = create_schedule_period(2026, 7, 1, None)

	result = solve_period(period_id, max_time_seconds=10)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assignments = get_assignments(period_id).sort_values("work_date")
	ordered_residents = assignments["resident_id"].astype(int).tolist()
	assert len(ordered_residents) == 31
	assert all(first != second for first, second in zip(ordered_residents, ordered_residents[1:]))
	assert Counter(ordered_residents).most_common(1)[0][1] == 1
