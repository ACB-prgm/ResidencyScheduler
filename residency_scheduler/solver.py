from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

import pandas as pd
from ortools.sat.python import cp_model

from residency_scheduler.repository import (
	HARD_UNAVAILABLE_TYPES,
	get_expanded_schedule_requests,
	get_period,
	get_residents,
	get_schedule_rules,
	record_solver_run,
	save_assignments,
)

FAIR_DISTRIBUTION_PENALTY = 5000
TOTAL_SURPLUS_WEIGHT_PENALTY = 50
WEEKEND_SURPLUS_WEIGHT_PENALTY = 150


@dataclass(frozen=True)
class SolverResult:
	status: str
	assignments: list[dict]
	objective_score: float | None
	warnings: list[str]


def solve_period(period_id: int, max_time_seconds: int = 30) -> SolverResult:
	period = get_period(period_id)
	residents = get_residents(active_only=True)
	requests = get_expanded_schedule_requests(period_id)
	rules = get_schedule_rules(period_id)
	dates = _month_dates(period["year"], period["month"])
	required_count = int(period["required_count"])

	if residents.empty:
		result = SolverResult("NO_RESIDENTS", [], None, ["No active residents are available to schedule."])
		record_solver_run(period_id, result.status, result.objective_score, result.warnings)
		return result

	validation_errors = _validate_inputs(residents, requests, rules, dates, required_count)
	if validation_errors:
		result = SolverResult("INVALID_INPUT", [], None, validation_errors)
		record_solver_run(period_id, result.status, result.objective_score, result.warnings)
		return result

	model = cp_model.CpModel()
	resident_ids = [int(row.id) for row in residents.itertuples()]
	date_keys = [d.isoformat() for d in dates]

	works = {
		(resident_id, work_date): model.NewBoolVar(f"works_{resident_id}_{work_date}")
		for resident_id in resident_ids
		for work_date in date_keys
	}

	for work_date in date_keys:
		model.Add(sum(works[(resident_id, work_date)] for resident_id in resident_ids) == required_count)

	hard_unavailable = requests[
		(requests["priority"].str.lower() == "hard")
		& (requests["request_type"].str.lower().isin(HARD_UNAVAILABLE_TYPES))
	]
	for row in hard_unavailable.itertuples():
		model.Add(works[(int(row.resident_id), str(row.work_date))] == 0)

	hard_assignments = requests[
		(requests["priority"].str.lower() == "hard")
		& (requests["request_type"].str.lower() == "assign")
	]
	for row in hard_assignments.itertuples():
		model.Add(works[(int(row.resident_id), str(row.work_date))] == 1)

	for row in residents.itertuples():
		if pd.notna(row.max_shifts):
			model.Add(sum(works[(int(row.id), work_date)] for work_date in date_keys) <= int(row.max_shifts))

	objective_terms = []

	total_required_shifts = len(date_keys) * required_count
	total_shift_counts = {
		resident_id: sum(works[(resident_id, work_date)] for work_date in date_keys)
		for resident_id in resident_ids
	}
	_add_distribution_objective(
		model=model,
		objective_terms=objective_terms,
		residents=residents,
		counts_by_resident=total_shift_counts,
		total_required=total_required_shifts,
		max_count=len(date_keys),
		prefix="total",
		surplus_weight_penalty=TOTAL_SURPLUS_WEIGHT_PENALTY,
	)

	weekend_dates = [d.isoformat() for d in dates if d.weekday() >= 5]
	if weekend_dates:
		weekend_shift_counts = {
			resident_id: sum(works[(resident_id, work_date)] for work_date in weekend_dates)
			for resident_id in resident_ids
		}
		_add_distribution_objective(
			model=model,
			objective_terms=objective_terms,
			residents=residents,
			counts_by_resident=weekend_shift_counts,
			total_required=len(weekend_dates) * required_count,
			max_count=len(weekend_dates),
			prefix="weekend",
			surplus_weight_penalty=WEEKEND_SURPLUS_WEIGHT_PENALTY,
		)

	for row in requests.itertuples():
		request_type = str(row.request_type).lower()
		priority = str(row.priority).lower()
		resident_id = int(row.resident_id)
		work_date = str(row.work_date)
		if priority != "soft" or (resident_id, work_date) not in works:
			continue
		if request_type == "prefer_off":
			objective_terms.append(works[(resident_id, work_date)] * 100)
		elif request_type in {"prefer_work", "assign"}:
			objective_terms.append((1 - works[(resident_id, work_date)]) * 10)

	for resident_id in resident_ids:
		for first, second in zip(date_keys, date_keys[1:]):
			back_to_back = model.NewBoolVar(f"back_to_back_{resident_id}_{first}_{second}")
			model.AddBoolAnd([works[(resident_id, first)], works[(resident_id, second)]]).OnlyEnforceIf(back_to_back)
			model.AddBoolOr([works[(resident_id, first)].Not(), works[(resident_id, second)].Not()]).OnlyEnforceIf(back_to_back.Not())
			objective_terms.append(back_to_back * 40)

	for row in rules.itertuples():
		if str(row.rule_type).lower() != "weekday_count":
			continue
		rule_dates = [d.isoformat() for d in dates if d.weekday() == int(row.weekday)]
		total = sum(works[(int(row.resident_id), work_date)] for work_date in rule_dates)
		target_count = int(row.target_count)
		if str(row.priority).lower() == "hard":
			model.Add(total == target_count)
		else:
			deviation = model.NewIntVar(0, len(rule_dates), f"rule_deviation_{row.id}")
			model.AddAbsEquality(deviation, total - target_count)
			objective_terms.append(deviation * 60)

	model.Minimize(sum(objective_terms))

	solver = cp_model.CpSolver()
	solver.parameters.max_time_in_seconds = max_time_seconds
	solver.parameters.num_search_workers = 1
	status = solver.Solve(model)
	status_name = solver.StatusName(status)

	if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
		result = SolverResult(status_name, [], None, ["No feasible schedule found. Review hard requests, rules, and max shift limits."])
		record_solver_run(period_id, result.status, result.objective_score, result.warnings)
		return result

	hard_assignment_pairs = {(int(row.resident_id), str(row.work_date)) for row in hard_assignments.itertuples()}
	assignments: list[dict] = []
	for resident_id in resident_ids:
		for work_date in date_keys:
			if solver.Value(works[(resident_id, work_date)]) == 1:
				is_hard_assigned = (resident_id, work_date) in hard_assignment_pairs
				assignments.append(
					{
						"work_date": work_date,
						"resident_id": resident_id,
						"source": "request" if is_hard_assigned else "solver",
						"is_locked": int(is_hard_assigned),
					}
				)

	save_assignments(period_id, assignments)
	result = SolverResult(status_name, assignments, float(solver.ObjectiveValue()), [])
	record_solver_run(period_id, result.status, result.objective_score, result.warnings)
	return result


def _month_dates(year: int, month: int) -> list[date]:
	last_day = calendar.monthrange(int(year), int(month))[1]
	return [date(int(year), int(month), day) for day in range(1, last_day + 1)]


def _add_distribution_objective(
	model: cp_model.CpModel,
	objective_terms: list,
	residents: pd.DataFrame,
	counts_by_resident: dict[int, cp_model.LinearExpr],
	total_required: int,
	max_count: int,
	prefix: str,
	surplus_weight_penalty: int,
) -> None:
	resident_count = len(counts_by_resident)
	base = total_required // resident_count
	ceiling = base + (1 if total_required % resident_count else 0)

	for row in residents.itertuples():
		resident_id = int(row.id)
		count = counts_by_resident[resident_id]
		under_base = model.NewIntVar(0, max_count, f"{prefix}_under_base_{resident_id}")
		over_ceiling = model.NewIntVar(0, max_count, f"{prefix}_over_ceiling_{resident_id}")
		surplus = model.NewIntVar(0, max_count, f"{prefix}_surplus_{resident_id}")

		model.Add(under_base >= base - count)
		model.Add(over_ceiling >= count - ceiling)
		model.Add(surplus >= count - base)

		objective_terms.append(under_base * FAIR_DISTRIBUTION_PENALTY)
		objective_terms.append(over_ceiling * FAIR_DISTRIBUTION_PENALTY)
		objective_terms.append(surplus * _weight_penalty(row.weight, surplus_weight_penalty))


def _weight_penalty(weight, multiplier: int) -> int:
	if pd.isna(weight):
		return multiplier
	return max(1, int(round(float(weight) * multiplier)))


def _validate_inputs(
	residents: pd.DataFrame,
	requests: pd.DataFrame,
	rules: pd.DataFrame,
	dates: list[date],
	required_count: int,
) -> list[str]:
	errors: list[str] = []
	valid_residents = set(residents["id"].astype(int).tolist())
	valid_dates = {item.isoformat() for item in dates}

	if required_count > len(valid_residents):
		errors.append(
			f"Each date requires {required_count} resident(s), but only {len(valid_residents)} active resident(s) exist."
		)

	total_required = len(dates) * required_count
	configured_capacity = 0
	has_unbounded_capacity = False
	for row in residents.itertuples():
		if pd.notna(row.max_shifts):
			configured_capacity += int(row.max_shifts)
		else:
			has_unbounded_capacity = True
	if not has_unbounded_capacity and configured_capacity < total_required:
		errors.append(
			f"Configured max shifts allow only {configured_capacity} total assignment(s), but the period requires {total_required}."
		)

	for row in requests.itertuples():
		if int(row.resident_id) not in valid_residents:
			errors.append(f"Request references inactive/missing resident_id {row.resident_id}.")
		if str(row.work_date) not in valid_dates:
			errors.append(f"Request date {row.work_date} is outside the draft month.")

	for row in rules.itertuples():
		if int(row.resident_id) not in valid_residents:
			errors.append(f"Rule references inactive/missing resident_id {row.resident_id}.")
		if str(row.comparator).lower() != "exactly":
			errors.append("Only exactly weekday count rules are supported.")

	hard_assignments = requests[
		(requests["priority"].str.lower() == "hard")
		& (requests["request_type"].str.lower() == "assign")
	]
	if not hard_assignments.empty:
		for work_date, group in hard_assignments.groupby("work_date"):
			if len(group) > required_count:
				errors.append(f"{work_date} has {len(group)} hard assign request(s) but only requires {required_count} resident(s).")

		for resident_id, group in hard_assignments.groupby("resident_id"):
			matches = residents.loc[residents["id"].astype(int) == int(resident_id), "max_shifts"]
			if not matches.empty and pd.notna(matches.iloc[0]) and len(group) > int(matches.iloc[0]):
				errors.append(
					f"resident_id {resident_id} has {len(group)} hard assign request(s), exceeding max_shifts {int(matches.iloc[0])}."
				)

	hard_unavailable = requests[
		(requests["priority"].str.lower() == "hard")
		& (requests["request_type"].str.lower().isin(HARD_UNAVAILABLE_TYPES))
	]
	if not hard_assignments.empty and not hard_unavailable.empty:
		conflicts = hard_assignments.merge(hard_unavailable, on=["resident_id", "work_date"], suffixes=("_assign", "_unavailable"))
		for row in conflicts.itertuples():
			errors.append(
				f"Hard request conflict: resident_id {row.resident_id} is assigned on {row.work_date} but marked hard unavailable."
			)

	for work_date in valid_dates:
		unavailable_residents = set(
			hard_unavailable.loc[hard_unavailable["work_date"].astype(str) == work_date, "resident_id"].astype(int).tolist()
		)
		available_count = len(valid_residents - unavailable_residents)
		if available_count < required_count:
			errors.append(f"{work_date} has only {available_count} available resident(s), but requires {required_count}.")

	return errors
