from __future__ import annotations

from collections import Counter

import pandas as pd
import pytest

from residency_scheduler.db import get_connection, init_db
from residency_scheduler.repository import (
	create_recurring_preference,
	create_schedule_request,
	get_or_create_schedule_period,
	get_assignments,
	get_expanded_schedule_requests,
	get_preference_violations,
	save_assignments,
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


def save_counted_assignments(period_id: int, year: int, month: int, resident_counts: dict[int, int]) -> None:
	dates = pd.date_range(f"{year}-{month:02d}-01", periods=sum(resident_counts.values()), freq="D")
	assignments = []
	resident_ids = [resident_id for resident_id, count in resident_counts.items() for _ in range(count)]
	for work_date, resident_id in zip(dates, resident_ids):
		assignments.append({"work_date": work_date.date().isoformat(), "resident_id": resident_id})
	save_assignments(period_id, assignments)


def test_normal_feasible_schedule_covers_every_day(isolated_db):
	add_residents(["Ada", "Ben", "Cam", "Dee"], max_shifts=10)
	period_id = get_or_create_schedule_period(2026, 1, required_count=1)

	result = solve_period(period_id, max_time_seconds=5)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assert len(result.assignments) == 31
	assert all(len(resident_ids) == 1 for resident_ids in assignments_by_date(period_id).values())


def test_date_range_vacation_is_honored(isolated_db):
	add_residents(["Ada", "Ben", "Cam", "Dee"], max_shifts=10)
	period_id = get_or_create_schedule_period(2026, 2, required_count=1)
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


def test_cross_month_vacation_blocks_only_selected_month_dates(isolated_db):
	add_residents(["Ada", "Ben", "Cam", "Dee"], max_shifts=20)
	september_id = get_or_create_schedule_period(2026, 9, required_count=1)
	october_id = get_or_create_schedule_period(2026, 10, required_count=1)
	create_schedule_request(1, "2026-09-25", "2026-10-10", "vacation", "hard", "PTO")

	september_expanded = get_expanded_schedule_requests(september_id)
	october_expanded = get_expanded_schedule_requests(october_id)
	september_vacation = september_expanded[september_expanded["request_type"] == "vacation"]
	october_vacation = october_expanded[october_expanded["request_type"] == "vacation"]

	assert set(september_vacation["work_date"]) == {f"2026-09-{day:02d}" for day in range(25, 31)}
	assert set(october_vacation["work_date"]) == {f"2026-10-{day:02d}" for day in range(1, 11)}

	result = solve_period(october_id, max_time_seconds=5)
	assignments = assignments_by_date(october_id)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	for work_date in [f"2026-10-{day:02d}" for day in range(1, 11)]:
		assert 1 not in assignments[work_date]


def test_vacation_adds_prior_thursday_prefer_work(isolated_db):
	add_residents(["Ada", "Ben", "Cam"], max_shifts=12)
	period_id = get_or_create_schedule_period(2026, 2, required_count=1)
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


def test_cross_month_vacation_derived_thursday_stays_in_selected_month(isolated_db):
	add_residents(["Ada", "Ben", "Cam"], max_shifts=20)
	september_id = get_or_create_schedule_period(2026, 9, required_count=1)
	october_id = get_or_create_schedule_period(2026, 10, required_count=1)
	create_schedule_request(1, "2026-10-05", "2026-10-10", "vacation", "hard", "PTO")

	september_expanded = get_expanded_schedule_requests(september_id)
	october_expanded = get_expanded_schedule_requests(october_id)

	assert "2026-10-01" not in set(september_expanded["work_date"])
	assert not october_expanded[
		(october_expanded["resident_id"] == 1)
		& (october_expanded["work_date"] == "2026-10-01")
		& (october_expanded["request_type"] == "prefer_work")
		& (october_expanded["source"] == "derived")
	].empty


def test_hard_assign_request_is_honored(isolated_db):
	add_residents(["Ada", "Ben", "Cam", "Dee"], max_shifts=10)
	period_id = get_or_create_schedule_period(2026, 3, required_count=1)
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
	period_id = get_or_create_schedule_period(2026, 4, required_count=1)
	with pytest.raises(ValueError, match="Conflicting hard requests"):
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


def test_solver_conflict_warnings_use_resident_names(isolated_db):
	add_residents(["Ada", "Ben", "Cam", "Dee"], max_shifts=10)
	period_id = get_or_create_schedule_period(2026, 8, required_count=1)
	with get_connection() as conn:
		conn.executemany(
			"""
			INSERT INTO schedule_requests (resident_id, start_date, end_date, request_type, priority, reason)
			VALUES (?, ?, ?, ?, ?, ?)
			""",
			[
				(1, "2026-08-14", "2026-08-14", "prefer_work", "hard", "Must work"),
				(1, "2026-08-14", "2026-08-14", "unavailable", "hard", "Unavailable"),
			],
		)

	result = solve_period(period_id, max_time_seconds=5)

	assert result.status == "INVALID_INPUT"
	assert any("Hard request conflict: Ada must work on 2026-08-14" in warning for warning in result.warnings)
	assert all("resident_id" not in warning for warning in result.warnings)


def test_too_many_hard_assign_requests_on_one_date_is_rejected(isolated_db):
	add_residents(["Ada", "Ben", "Cam"], max_shifts=12)
	period_id = get_or_create_schedule_period(2026, 5, required_count=1)
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
	assert any("Ada" in warning and "Ben" in warning for warning in result.warnings)
	assert all("resident_id" not in warning for warning in result.warnings)


def test_max_shifts_can_make_schedule_infeasible(isolated_db):
	add_residents(["Ada", "Ben"], max_shifts=5)
	period_id = get_or_create_schedule_period(2026, 6, required_count=1)

	result = solve_period(period_id, max_time_seconds=5)

	assert result.status == "INVALID_INPUT"
	assert any("Configured max shifts" in warning for warning in result.warnings)
	assert any("Ada" in warning and "Ben" in warning for warning in result.warnings)
	assert all("resident_id" not in warning for warning in result.warnings)


def test_preference_heavy_schedule_remains_feasible_and_reports_violations(isolated_db):
	add_residents(["Ada", "Ben", "Cam"], max_shifts=12)
	period_id = get_or_create_schedule_period(2026, 2, required_count=1)
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


def test_recurring_preferences_expand_only_matching_weekdays_and_bounds(isolated_db):
	add_residents(["Ada", "Ben", "Cam", "Dee"], max_shifts=12)
	period_id = get_or_create_schedule_period(2026, 9, required_count=1)
	create_recurring_preference(1, "prefer_off", 0, "2026-09-08", "2026-09-22", "Monday preference")

	expanded = get_expanded_schedule_requests(period_id)
	ada = expanded[(expanded["resident_id"] == 1) & (expanded["request_type"] == "prefer_off")]

	assert ada["work_date"].tolist() == ["2026-09-14", "2026-09-21"]
	assert set(ada["source"]) == {"recurring"}


def test_dated_preference_overrides_recurring_on_same_date(isolated_db):
	add_residents(["Ada", "Ben", "Cam", "Dee"], max_shifts=12)
	period_id = get_or_create_schedule_period(2026, 9, required_count=1)
	create_recurring_preference(1, "prefer_off", 0, "2026-09-01", None, "Recurring off")
	create_schedule_request(1, "2026-09-14", "2026-09-14", "prefer_work", "soft", "Dated work")

	expanded = get_expanded_schedule_requests(period_id)
	selected = expanded[(expanded["resident_id"] == 1) & (expanded["work_date"] == "2026-09-14")]

	assert len(selected) == 1
	assert selected.iloc[0]["request_type"] == "prefer_work"
	assert selected.iloc[0]["source"] == "dated"


def test_duplicate_dated_preferences_collapse_and_hard_wins(isolated_db):
	add_residents(["Ada", "Ben", "Cam", "Dee"], max_shifts=12)
	period_id = get_or_create_schedule_period(2026, 9, required_count=1)
	create_schedule_request(1, "2026-09-14", "2026-09-14", "prefer_off", "soft", "Soft duplicate")
	create_schedule_request(1, "2026-09-14", "2026-09-14", "prefer_off", "hard", "Hard duplicate")

	expanded = get_expanded_schedule_requests(period_id)
	selected = expanded[
		(expanded["resident_id"] == 1)
		& (expanded["work_date"] == "2026-09-14")
		& (expanded["request_type"] == "prefer_off")
	]

	assert len(selected) == 1
	assert selected.iloc[0]["priority"] == "hard"
	assert selected.iloc[0]["reason"] == "Hard duplicate"


def test_duplicate_recurring_preferences_create_one_effective_penalty_and_violation(isolated_db):
	add_residents(["Ada", "Ben"], max_shifts=31)
	period_id = get_or_create_schedule_period(2026, 9, required_count=2)
	create_recurring_preference(1, "prefer_off", 0, "2026-09-01", None, "First")
	create_recurring_preference(1, "prefer_off", 0, "2026-09-01", None, "Second")

	expanded = get_expanded_schedule_requests(period_id)
	monday = expanded[
		(expanded["resident_id"] == 1)
		& (expanded["work_date"] == "2026-09-07")
		& (expanded["request_type"] == "prefer_off")
	]
	assert len(monday) == 1

	result = solve_period(period_id, max_time_seconds=5)
	violations = get_preference_violations(period_id)
	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assert len(violations[(violations["resident_name"] == "Ada") & (violations["work_date"].astype(str) == "2026-09-07")]) == 1


def test_hard_dated_prefer_work_and_prefer_off_are_enforced(isolated_db):
	add_residents(["Ada", "Ben", "Cam", "Dee"], max_shifts=12)
	period_id = get_or_create_schedule_period(2026, 9, required_count=1)
	create_schedule_request(1, "2026-09-10", "2026-09-10", "prefer_work", "hard", "Must work")
	create_schedule_request(1, "2026-09-11", "2026-09-11", "prefer_off", "hard", "Must be off")

	result = solve_period(period_id, max_time_seconds=5)
	assignments = assignments_by_date(period_id)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assert assignments["2026-09-10"] == [1]
	assert 1 not in assignments["2026-09-11"]
	stored = get_assignments(period_id)
	forced = stored[(stored["work_date"].astype(str) == "2026-09-10") & (stored["resident_id"].astype(int) == 1)].iloc[0]
	assert forced["source"] == "request"
	assert int(forced["is_locked"]) == 1


def test_inactive_resident_preferences_do_not_enter_solver_inputs(isolated_db):
	add_residents(["Ada", "Ben", "Cam", "Dee"], max_shifts=12)
	period_id = get_or_create_schedule_period(2026, 9, required_count=1)
	create_recurring_preference(1, "prefer_off", 0, "2026-09-01", None, "Recurring")
	create_schedule_request(1, "2026-09-14", "2026-09-14", "prefer_off", "soft", "Dated")
	residents = pd.DataFrame(
		[
			{"id": 1, "name": "Ada", "email": "ada@example.com", "max_shifts": 12, "min_shifts": None, "weight": 1, "active": 0},
			{"id": 2, "name": "Ben", "email": "ben@example.com", "max_shifts": 12, "min_shifts": None, "weight": 1, "active": 1},
			{"id": 3, "name": "Cam", "email": "cam@example.com", "max_shifts": 12, "min_shifts": None, "weight": 1, "active": 1},
			{"id": 4, "name": "Dee", "email": "dee@example.com", "max_shifts": 12, "min_shifts": None, "weight": 1, "active": 1},
		]
	)
	save_residents(residents)

	expanded = get_expanded_schedule_requests(period_id)
	assert 1 not in set(expanded["resident_id"].astype(int).tolist())


def test_back_to_back_is_avoided_when_enough_residents_exist(isolated_db):
	add_residents([f"Resident {index}" for index in range(1, 32)], max_shifts=1)
	period_id = get_or_create_schedule_period(2026, 7, required_count=1)

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
	add_residents(names, max_shifts=10, weights={name: 1 for name in low_weight} | {name: 5 for name in high_weight})
	period_id = get_or_create_schedule_period(2026, 1, required_count=1)

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
	add_residents(names, max_shifts=10, weights={name: 1 for name in low_weight} | {name: 5 for name in high_weight})
	period_id = get_or_create_schedule_period(2026, 5, required_count=1)

	result = solve_period(period_id, max_time_seconds=10)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assignments = get_assignments(period_id)
	weekend_assignments = assignments[pd.to_datetime(assignments["work_date"]).dt.weekday >= 4]
	weekend_counts = weekend_assignments.groupby("resident_name").size().to_dict()
	for name in names:
		weekend_counts.setdefault(name, 0)

	assert sorted(weekend_counts.values()) == [2, 2, 2, 2, 2, 2, 3]
	assert all(weekend_counts[name] == 2 for name in high_weight)
	assert sum(weekend_counts[name] == 3 for name in low_weight) == 1


def test_seeded_random_tie_break_can_rotate_equal_cost_leftover_shift(isolated_db):
	add_residents(["Ada", "Ben", "Cam", "Dee", "Eli"], max_shifts=10)
	period_id = get_or_create_schedule_period(2026, 7, required_count=1)
	surplus_residents = set()

	for seed in range(1, 9):
		result = solve_period(period_id, max_time_seconds=5, random_seed=seed)
		assert result.status in {"OPTIMAL", "FEASIBLE"}
		counts = get_assignments(period_id).groupby("resident_id").size().to_dict()
		surplus_residents.update(int(resident_id) for resident_id, count in counts.items() if count == 7)

	assert len(surplus_residents) > 1


def test_prior_total_surplus_discourages_same_current_surplus_resident(isolated_db):
	add_residents(["Ada", "Ben", "Cam", "Dee", "Eli"], max_shifts=10)
	prior_id = get_or_create_schedule_period(2026, 5, required_count=1)
	current_id = get_or_create_schedule_period(2026, 7, required_count=1)
	save_counted_assignments(prior_id, 2026, 5, {1: 7, 2: 6, 3: 6, 4: 6, 5: 6})

	result = solve_period(current_id, max_time_seconds=10, random_seed=1)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	counts = get_assignments(current_id).groupby("resident_id").size().to_dict()
	assert counts[1] == 6
	assert sorted(counts.values()) == [6, 6, 6, 6, 7]


def test_prior_weekend_surplus_discourages_same_current_weekend_surplus_resident(isolated_db):
	add_residents(["Ada", "Ben", "Cam"], max_shifts=14)
	prior_id = get_or_create_schedule_period(2026, 7, required_count=1)
	current_id = get_or_create_schedule_period(2026, 8, required_count=1)
	save_assignments(
		prior_id,
		[
			{"work_date": "2026-07-04", "resident_id": 1},
			{"work_date": "2026-07-05", "resident_id": 1},
			{"work_date": "2026-07-11", "resident_id": 1},
			{"work_date": "2026-07-12", "resident_id": 1},
			{"work_date": "2026-07-18", "resident_id": 2},
			{"work_date": "2026-07-19", "resident_id": 2},
			{"work_date": "2026-07-25", "resident_id": 3},
			{"work_date": "2026-07-26", "resident_id": 3},
			{"work_date": "2026-07-01", "resident_id": 2},
			{"work_date": "2026-07-02", "resident_id": 2},
			{"work_date": "2026-07-03", "resident_id": 2},
			{"work_date": "2026-07-06", "resident_id": 2},
			{"work_date": "2026-07-07", "resident_id": 2},
			{"work_date": "2026-07-08", "resident_id": 2},
			{"work_date": "2026-07-09", "resident_id": 2},
			{"work_date": "2026-07-10", "resident_id": 2},
			{"work_date": "2026-07-13", "resident_id": 2},
			{"work_date": "2026-07-14", "resident_id": 3},
			{"work_date": "2026-07-15", "resident_id": 3},
			{"work_date": "2026-07-16", "resident_id": 3},
			{"work_date": "2026-07-17", "resident_id": 3},
			{"work_date": "2026-07-20", "resident_id": 3},
			{"work_date": "2026-07-21", "resident_id": 3},
			{"work_date": "2026-07-22", "resident_id": 3},
			{"work_date": "2026-07-23", "resident_id": 3},
			{"work_date": "2026-07-24", "resident_id": 3},
			{"work_date": "2026-07-27", "resident_id": 1},
			{"work_date": "2026-07-28", "resident_id": 1},
			{"work_date": "2026-07-29", "resident_id": 1},
			{"work_date": "2026-07-30", "resident_id": 1},
			{"work_date": "2026-07-31", "resident_id": 1},
		],
	)

	result = solve_period(current_id, max_time_seconds=10, random_seed=1)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assignments = get_assignments(current_id)
	weekend_assignments = assignments[pd.to_datetime(assignments["work_date"]).dt.weekday >= 4]
	weekend_counts = weekend_assignments.groupby("resident_id").size().to_dict()
	assert weekend_counts[1] == 4
	assert sorted(weekend_counts.values()) == [4, 5, 5]


def test_exactly_two_fridays_weekday_count_rule_is_enforced(isolated_db):
	add_residents(["Ada", "Ben", "Cam", "Dee"], max_shifts=12)
	period_id = get_or_create_schedule_period(2026, 7, required_count=1)
	replace_schedule_rules(
		period_id,
		pd.DataFrame(
			[
				{
					"resident": "Ada · resident #1",
					"rule_type": "weekday_count",
					"weekday": "Friday",
					"comparator": "exactly",
					"target_count": 2,
					"priority": "hard",
					"reason": "Generic Friday requirement",
				}
			]
		),
	)

	result = solve_period(period_id, max_time_seconds=10)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assignments = get_assignments(period_id)
	fridays = pd.to_datetime(assignments["work_date"]).dt.weekday == 4
	assert int(((assignments["resident_id"] == 1) & fridays).sum()) == 2


def test_city_hope_friday_saturday_pair_rule_is_enforced(isolated_db):
	add_residents(["City Hope", "Ben", "Cam", "Dee"], max_shifts=12)
	period_id = get_or_create_schedule_period(2026, 7, required_count=1)
	replace_schedule_rules(
		period_id,
		pd.DataFrame(
			[
				{
					"resident": "City Hope · resident #1",
					"rule_type": "weekday_pair_count",
					"weekday": "Friday",
					"paired_weekday": "Saturday",
					"comparator": "exactly",
					"target_count": 1,
					"priority": "hard",
					"reason": "City of Hope requirement",
				}
			]
		),
	)

	result = solve_period(period_id, max_time_seconds=10)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assignments = get_assignments(period_id)
	city_hope = assignments[assignments["resident_id"] == 1].copy()
	city_hope["work_date"] = pd.to_datetime(city_hope["work_date"])
	fridays = city_hope[city_hope["work_date"].dt.weekday == 4]["work_date"].tolist()
	saturdays = city_hope[city_hope["work_date"].dt.weekday == 5]["work_date"].tolist()

	assert len(fridays) == 1
	assert len(saturdays) == 1
	assert fridays[0] + pd.Timedelta(days=1) == saturdays[0]


def test_city_hope_pair_rule_blocks_extra_unpaired_fridays_or_saturdays(isolated_db):
	add_residents(["City Hope", "Ben", "Cam", "Dee"], max_shifts=12)
	period_id = get_or_create_schedule_period(2026, 8, required_count=1)
	replace_schedule_rules(
		period_id,
		pd.DataFrame(
			[
				{
					"resident": "City Hope · resident #1",
					"rule_type": "weekday_pair_count",
					"weekday": "Friday",
					"paired_weekday": "Saturday",
					"comparator": "exactly",
					"target_count": 1,
					"priority": "hard",
					"reason": "City of Hope requirement",
				}
			]
		),
	)

	result = solve_period(period_id, max_time_seconds=10)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assignments = get_assignments(period_id)
	city_hope_dates = pd.to_datetime(assignments.loc[assignments["resident_id"] == 1, "work_date"])

	assert int((city_hope_dates.dt.weekday == 4).sum()) == 1
	assert int((city_hope_dates.dt.weekday == 5).sum()) == 1


def test_away_rotation_with_pair_rule_assigns_only_that_pair(isolated_db):
	add_residents(["City Hope", "Ben", "Cam", "Dee"], max_shifts=20)
	period_id = get_or_create_schedule_period(2026, 8, required_count=1)
	replace_schedule_rules(
		period_id,
		pd.DataFrame(
			[
				{
					"resident": "City Hope · resident #1",
					"rule_type": "away_rotation",
					"priority": "hard",
					"reason": "Away rotation",
				},
				{
					"resident": "City Hope · resident #1",
					"rule_type": "weekday_pair_count",
					"weekday": "Friday",
					"paired_weekday": "Saturday",
					"comparator": "exactly",
					"target_count": 1,
					"priority": "hard",
					"reason": "Required weekend pair",
				},
			]
		),
	)

	result = solve_period(period_id, max_time_seconds=10)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assignments = get_assignments(period_id)
	city_hope = assignments[assignments["resident_id"] == 1].copy()
	city_hope_dates = pd.to_datetime(city_hope["work_date"])
	assert len(city_hope) == 2
	assert int((city_hope_dates.dt.weekday == 4).sum()) == 1
	assert int((city_hope_dates.dt.weekday == 5).sum()) == 1
	assert city_hope_dates.min() + pd.Timedelta(days=1) == city_hope_dates.max()


def test_away_rotation_alone_blocks_resident_when_feasible(isolated_db):
	add_residents(["Ada", "Ben", "Cam"], max_shifts=31)
	period_id = get_or_create_schedule_period(2026, 5, required_count=1)
	replace_schedule_rules(
		period_id,
		pd.DataFrame(
			[
				{
					"resident": "Ada · resident #1",
					"rule_type": "away_rotation",
					"priority": "hard",
					"reason": "Away rotation",
				}
			]
		),
	)

	result = solve_period(period_id, max_time_seconds=10)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assignments = get_assignments(period_id)
	assert 1 not in set(assignments["resident_id"].astype(int))


def test_away_rotation_allows_hard_assign_request_only(isolated_db):
	add_residents(["Ada", "Ben", "Cam"], max_shifts=31)
	period_id = get_or_create_schedule_period(2026, 5, required_count=1)
	replace_schedule_rules(
		period_id,
		pd.DataFrame(
			[
				{
					"resident": "Ada · resident #1",
					"rule_type": "away_rotation",
					"priority": "hard",
					"reason": "Away rotation",
				}
			]
		),
	)
	replace_schedule_requests(
		period_id,
		pd.DataFrame(
			[
				{
					"resident": "Ada · resident #1",
					"start_date": "2026-05-12",
					"end_date": "2026-05-12",
					"request_type": "assign",
					"priority": "hard",
					"reason": "Required coverage",
				}
			]
		),
	)

	result = solve_period(period_id, max_time_seconds=10)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assignments = get_assignments(period_id)
	ada_assignments = assignments[assignments["resident_id"] == 1]
	assert ada_assignments["work_date"].tolist() == ["2026-05-12"]


def test_soft_away_rotation_avoids_resident_but_remains_feasible(isolated_db):
	add_residents(["Ada", "Ben", "Cam"], max_shifts=31)
	period_id = get_or_create_schedule_period(2026, 5, required_count=1)
	replace_schedule_rules(
		period_id,
		pd.DataFrame(
			[
				{
					"resident": "Ada · resident #1",
					"rule_type": "away_rotation",
					"priority": "soft",
					"reason": "Away rotation if possible",
				}
			]
		),
	)

	result = solve_period(period_id, max_time_seconds=10)

	assert result.status in {"OPTIMAL", "FEASIBLE"}
	assignments = get_assignments(period_id)
	assert 1 not in set(assignments["resident_id"].astype(int))


def test_away_rotation_can_make_period_invalid_when_too_few_residents_remain(isolated_db):
	add_residents(["Ada", "Ben"], max_shifts=31)
	period_id = get_or_create_schedule_period(2026, 5, required_count=2)
	replace_schedule_rules(
		period_id,
		pd.DataFrame(
			[
				{
					"resident": "Ada · resident #1",
					"rule_type": "away_rotation",
					"priority": "hard",
					"reason": "Away rotation",
				}
			]
		),
	)

	result = solve_period(period_id, max_time_seconds=10)

	assert result.status == "INVALID_INPUT"
	assert any("available resident" in warning for warning in result.warnings)


def test_pair_rule_impossible_target_is_invalid_input(isolated_db):
	add_residents(["City Hope", "Ben", "Cam", "Dee"], max_shifts=20)
	period_id = get_or_create_schedule_period(2026, 7, required_count=1)
	replace_schedule_rules(
		period_id,
		pd.DataFrame(
			[
				{
					"resident": "City Hope · resident #1",
					"rule_type": "weekday_pair_count",
					"weekday": "Friday",
					"paired_weekday": "Saturday",
					"comparator": "exactly",
					"target_count": 6,
					"priority": "hard",
					"reason": "Impossible City of Hope requirement",
				}
			]
		),
	)

	result = solve_period(period_id, max_time_seconds=10)

	assert result.status == "INVALID_INPUT"
	assert any("available adjacent weekday pair" in warning for warning in result.warnings)
	assert any("City Hope" in warning for warning in result.warnings)
	assert all("resident_id" not in warning for warning in result.warnings)
