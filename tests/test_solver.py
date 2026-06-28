from __future__ import annotations

from collections import Counter

import pandas as pd
import pytest

from residency_scheduler.db import init_db
from residency_scheduler.repository import (
	create_schedule_period,
	get_assignments,
	get_expanded_schedule_requests,
	get_preference_violations,
	replace_schedule_requests,
	replace_schedule_rules,
	save_residents,
)
from residency_scheduler.solver import solve_period


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
	monkeypatch.setenv("RESIDENCY_SCHEDULER_DB", str(tmp_path / "test.sqlite"))
	init_db()


def add_residents(names: list[str], max_shifts: int | None = 10, weights: dict[str, float] | None = None) -> None:
	weights = weights or {}
	save_residents(
		pd.DataFrame(
			[
				{
					"name": name,
					"email": f"{name.lower().replace(' ', '.')}@example.com",
					"max_shifts": max_shifts,
					"min_shifts": None,
					"weight": weights.get(name, 1.0),
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
	period_id = create_schedule_period(2026, 1, "Draft 1", 1, None)

	result = solve_period(period_id, max_time_seconds=5)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assert len(result.assignments) == 31
	assert all(len(resident_ids) == 1 for resident_ids in assignments_by_date(period_id).values())


def test_date_range_vacation_is_honored(isolated_db):
	add_residents(["Ada", "Ben", "Cam", "Dee"], max_shifts=10)
	period_id = create_schedule_period(2026, 2, "Vacation draft", 1, None)
	replace_schedule_requests(
		period_id,
		pd.DataFrame(
			[
				{
					"resident": "Ada · resident #1",
					"start_date": "2026-02-10",
					"end_date": "2026-02-12",
					"request_type": "vacation",
					"priority": "",
					"reason": "PTO",
				}
			]
		),
	)

	result = solve_period(period_id, max_time_seconds=5)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assignments = assignments_by_date(period_id)
	for work_date in ["2026-02-10", "2026-02-11", "2026-02-12"]:
		assert 1 not in assignments[work_date]


def test_vacation_adds_prior_thursday_prefer_work(isolated_db):
	add_residents(["Ada", "Ben", "Cam"], max_shifts=12)
	period_id = create_schedule_period(2026, 2, "Vacation preference", 1, None)
	replace_schedule_requests(
		period_id,
		pd.DataFrame(
			[
				{
					"resident": "Ada · resident #1",
					"start_date": "2026-02-16",
					"end_date": "2026-02-20",
					"request_type": "vacation",
					"priority": "hard",
					"reason": "PTO",
				}
			]
		),
	)

	expanded = get_expanded_schedule_requests(period_id)

	derived = expanded[
		(expanded["resident_id"] == 1)
		& (expanded["work_date"] == "2026-02-12")
		& (expanded["request_type"] == "prefer_work")
		& (expanded["source"] == "derived")
	]
	assert not derived.empty


def test_hard_assign_request_is_honored(isolated_db):
	add_residents(["Ada", "Ben", "Cam", "Dee"], max_shifts=10)
	period_id = create_schedule_period(2026, 3, "Assign draft", 1, None)
	replace_schedule_requests(
		period_id,
		pd.DataFrame(
			[
				{
					"resident": "Ben · resident #2",
					"start_date": "2026-03-05",
					"end_date": "2026-03-05",
					"request_type": "assign",
					"priority": "",
					"reason": "Chief request",
				}
			]
		),
	)

	result = solve_period(period_id, max_time_seconds=5)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assert assignments_by_date(period_id)["2026-03-05"] == [2]


def test_hard_assign_conflict_with_hard_unavailable_is_rejected(isolated_db):
	add_residents(["Ada", "Ben", "Cam", "Dee"], max_shifts=10)
	period_id = create_schedule_period(2026, 4, "Conflict draft", 1, None)
	replace_schedule_requests(
		period_id,
		pd.DataFrame(
			[
				{
					"resident": "Ada · resident #1",
					"start_date": "2026-04-02",
					"end_date": "2026-04-02",
					"request_type": "unavailable",
					"priority": "hard",
					"reason": "Clinic",
				},
				{
					"resident": "Ada · resident #1",
					"start_date": "2026-04-02",
					"end_date": "2026-04-02",
					"request_type": "assign",
					"priority": "hard",
					"reason": "Conflict",
				},
			]
		),
	)

	result = solve_period(period_id, max_time_seconds=5)

	assert result.status == "INVALID_INPUT"
	assert any("Hard request conflict" in warning for warning in result.warnings)


def test_too_many_hard_assign_requests_on_one_date_is_rejected(isolated_db):
	add_residents(["Ada", "Ben", "Cam"], max_shifts=12)
	period_id = create_schedule_period(2026, 5, "Overassigned draft", 1, None)
	replace_schedule_requests(
		period_id,
		pd.DataFrame(
			[
				{"resident": "Ada · resident #1", "start_date": "2026-05-01", "end_date": "2026-05-01", "request_type": "assign", "priority": "hard", "reason": ""},
				{"resident": "Ben · resident #2", "start_date": "2026-05-01", "end_date": "2026-05-01", "request_type": "assign", "priority": "hard", "reason": ""},
			]
		),
	)

	result = solve_period(period_id, max_time_seconds=5)

	assert result.status == "INVALID_INPUT"
	assert any("hard assign request" in warning for warning in result.warnings)


def test_max_shifts_can_make_schedule_infeasible(isolated_db):
	add_residents(["Ada", "Ben"], max_shifts=5)
	period_id = create_schedule_period(2026, 6, "Max shift draft", 1, None)

	result = solve_period(period_id, max_time_seconds=5)

	assert result.status == "INVALID_INPUT"
	assert any("Configured max shifts" in warning for warning in result.warnings)


def test_preference_heavy_schedule_remains_feasible_and_reports_violations(isolated_db):
	add_residents(["Ada", "Ben", "Cam"], max_shifts=12)
	period_id = create_schedule_period(2026, 2, "Preference draft", 1, None)
	preferences = [
		{
			"resident": f"{name} · resident #{resident_id}",
			"start_date": f"2026-02-{day:02d}",
			"end_date": f"2026-02-{day:02d}",
			"request_type": "prefer_off",
			"priority": "soft",
			"reason": "Preference",
		}
		for day in range(1, 8)
		for name, resident_id in [("Ada", 1), ("Ben", 2)]
	]
	replace_schedule_requests(period_id, pd.DataFrame(preferences))

	result = solve_period(period_id, max_time_seconds=5)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assert len(result.assignments) == 28
	assert set(get_preference_violations(period_id).columns) >= {"work_date", "resident_name", "request_type"}


def test_back_to_back_is_avoided_when_enough_residents_exist(isolated_db):
	add_residents([f"Resident {index}" for index in range(1, 32)], max_shifts=1)
	period_id = create_schedule_period(2026, 7, "Back-to-back draft", 1, None)

	result = solve_period(period_id, max_time_seconds=10)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assignments = get_assignments(period_id).sort_values("work_date")
	ordered_residents = assignments["resident_id"].astype(int).tolist()
	assert len(ordered_residents) == 31
	assert all(first != second for first, second in zip(ordered_residents, ordered_residents[1:]))
	assert Counter(ordered_residents).most_common(1)[0][1] == 1


def test_total_shift_surplus_goes_to_lower_weight_residents(isolated_db):
	low_weight = ["Low 1", "Low 2", "Low 3", "Low 4"]
	high_weight = ["High 1", "High 2", "High 3"]
	names = low_weight + high_weight
	add_residents(names, max_shifts=10, weights={name: 0.5 for name in low_weight} | {name: 1.0 for name in high_weight})
	period_id = create_schedule_period(2026, 1, "Weighted total draft", 1, None)

	result = solve_period(period_id, max_time_seconds=10)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assignments = get_assignments(period_id)
	counts = assignments.groupby("resident_name").size().to_dict()
	assert sorted(counts.values()) == [4, 4, 4, 4, 5, 5, 5]
	assert all(counts[name] == 4 for name in high_weight)
	assert sum(counts[name] == 5 for name in low_weight) == 3


def test_weekend_surplus_goes_to_lower_weight_residents(isolated_db):
	low_weight = ["Low 1", "Low 2", "Low 3", "Low 4"]
	high_weight = ["High 1", "High 2", "High 3"]
	names = low_weight + high_weight
	add_residents(names, max_shifts=10, weights={name: 0.5 for name in low_weight} | {name: 1.0 for name in high_weight})
	period_id = create_schedule_period(2026, 8, "Weighted weekend draft", 1, None)

	result = solve_period(period_id, max_time_seconds=10)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assignments = get_assignments(period_id)
	weekend_assignments = assignments[pd.to_datetime(assignments["work_date"]).dt.weekday >= 5]
	weekend_counts = weekend_assignments.groupby("resident_name").size().to_dict()
	for name in names:
		weekend_counts.setdefault(name, 0)

	assert sorted(weekend_counts.values()) == [1, 1, 1, 1, 2, 2, 2]
	assert all(weekend_counts[name] == 1 for name in high_weight)
	assert sum(weekend_counts[name] == 2 for name in low_weight) == 3


def test_exactly_two_fridays_rule_is_enforced(isolated_db):
	add_residents(["City Hope", "Ben", "Cam", "Dee"], max_shifts=12)
	period_id = create_schedule_period(2026, 7, "Friday rule draft", 1, None)
	replace_schedule_rules(
		period_id,
		pd.DataFrame(
			[
				{
					"resident": "City Hope · resident #1",
					"rule_type": "weekday_count",
					"weekday": "Friday",
					"comparator": "exactly",
					"target_count": 2,
					"priority": "hard",
					"reason": "City of Hope requirement",
				}
			]
		),
	)

	result = solve_period(period_id, max_time_seconds=10)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assignments = get_assignments(period_id)
	fridays = pd.to_datetime(assignments["work_date"]).dt.weekday == 4
	assert int(((assignments["resident_id"] == 1) & fridays).sum()) == 2
