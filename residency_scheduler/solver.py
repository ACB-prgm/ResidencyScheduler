from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

import pandas as pd
from ortools.sat.python import cp_model

from residency_scheduler.repository import (
	get_availability,
	get_locked_assignments,
	get_period,
	get_residents,
	record_solver_run,
	save_assignments,
)


@dataclass(frozen=True)
class SolverResult:
	status: str
	assignments: list[dict]
	objective_score: float | None
	warnings: list[str]


def solve_period(period_id: int, max_time_seconds: int = 30) -> SolverResult:
	period = get_period(period_id)
	residents = get_residents(active_only=True)
	availability = get_availability(period_id)
	locked = get_locked_assignments(period_id)
	dates = _month_dates(period["year"], period["month"])
	required_count = int(period["required_count"])
	warnings: list[str] = []

	if residents.empty:
		result = SolverResult("NO_RESIDENTS", [], None, ["No active residents are available to schedule."])
		record_solver_run(period_id, result.status, result.objective_score, result.warnings)
		return result

	validation_errors = _validate_inputs(residents, availability, locked, dates, required_count)
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

	# Cover every night.
	for work_date in date_keys:
		model.Add(sum(works[(resident_id, work_date)] for resident_id in resident_ids) == required_count)

	# Hard unavailable dates.
	hard_types = {"vacation", "unavailable", "approved_absence", "medical_leave"}
	hard_unavailable = availability[
		(availability["priority"].str.lower() == "hard")
		& (availability["availability_type"].str.lower().isin(hard_types))
	]
	for row in hard_unavailable.itertuples():
		model.Add(works[(int(row.resident_id), str(row.work_date))] == 0)

	# Locked/preassigned shifts.
	for row in locked.itertuples():
		model.Add(works[(int(row.resident_id), str(row.work_date))] == 1)

	# Resident max shifts.
	for row in residents.itertuples():
		if pd.notna(row.max_shifts):
			model.Add(sum(works[(int(row.id), work_date)] for work_date in date_keys) <= int(row.max_shifts))

	objective_terms = []

	# Total workload balance.
	total_required_shifts = len(date_keys) * required_count
	target = round(total_required_shifts / len(resident_ids))
	for resident_id in resident_ids:
		total = sum(works[(resident_id, work_date)] for work_date in date_keys)
		deviation = model.NewIntVar(0, len(date_keys), f"total_deviation_{resident_id}")
		model.AddAbsEquality(deviation, total - target)
		objective_terms.append(deviation * 50)

	# Weekend balance.
	weekend_dates = [d.isoformat() for d in dates if d.weekday() >= 5]
	weekend_target = round((len(weekend_dates) * required_count) / len(resident_ids)) if weekend_dates else 0
	for resident_id in resident_ids:
		weekend_total = sum(works[(resident_id, work_date)] for work_date in weekend_dates)
		weekend_deviation = model.NewIntVar(0, len(weekend_dates), f"weekend_deviation_{resident_id}")
		model.AddAbsEquality(weekend_deviation, weekend_total - weekend_target)
		objective_terms.append(weekend_deviation * 75)

	# Soft preferences.
	for row in availability.itertuples():
		pref_type = str(row.availability_type).lower()
		priority = str(row.priority).lower()
		resident_id = int(row.resident_id)
		work_date = str(row.work_date)
		if priority != "soft" or (resident_id, work_date) not in works:
			continue
		if pref_type == "prefer_off":
			objective_terms.append(works[(resident_id, work_date)] * 100)
		elif pref_type == "prefer_work":
			objective_terms.append((1 - works[(resident_id, work_date)]) * 10)

	# Avoid back-to-back night shifts where possible.
	for resident_id in resident_ids:
		for first, second in zip(date_keys, date_keys[1:]):
			back_to_back = model.NewBoolVar(f"back_to_back_{resident_id}_{first}_{second}")
			model.AddBoolAnd([works[(resident_id, first)], works[(resident_id, second)]]).OnlyEnforceIf(back_to_back)
			model.AddBoolOr([works[(resident_id, first)].Not(), works[(resident_id, second)].Not()]).OnlyEnforceIf(back_to_back.Not())
			objective_terms.append(back_to_back * 40)

	model.Minimize(sum(objective_terms))

	solver = cp_model.CpSolver()
	solver.parameters.max_time_in_seconds = max_time_seconds
	status = solver.Solve(model)
	status_name = solver.StatusName(status)

	if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
		result = SolverResult(status_name, [], None, ["No feasible schedule found. Review hard constraints, locks, and max shift limits."])
		record_solver_run(period_id, result.status, result.objective_score, result.warnings)
		return result

	assignments: list[dict] = []
	locked_pairs = {(int(row.resident_id), str(row.work_date)) for row in locked.itertuples()}
	for resident_id in resident_ids:
		for work_date in date_keys:
			if solver.Value(works[(resident_id, work_date)]) == 1:
				is_locked = (resident_id, work_date) in locked_pairs
				assignments.append(
					{
						"work_date": work_date,
						"resident_id": resident_id,
						"source": "locked" if is_locked else "solver",
						"is_locked": int(is_locked),
					}
				)

	save_assignments(period_id, assignments)
	result = SolverResult(status_name, assignments, float(solver.ObjectiveValue()), warnings)
	record_solver_run(period_id, result.status, result.objective_score, result.warnings)
	return result


def _month_dates(year: int, month: int) -> list[date]:
	last_day = calendar.monthrange(year, month)[1]
	return [date(year, month, day) for day in range(1, last_day + 1)]


def _validate_inputs(
	residents: pd.DataFrame,
	availability: pd.DataFrame,
	locked: pd.DataFrame,
	dates: list[date],
	required_count: int,
) -> list[str]:
	errors: list[str] = []
	valid_residents = set(residents["id"].astype(int).tolist())
	valid_dates = {item.isoformat() for item in dates}

	for row in locked.itertuples():
		if int(row.resident_id) not in valid_residents:
			errors.append(f"Locked assignment references inactive/missing resident_id {row.resident_id}.")
		if str(row.work_date) not in valid_dates:
			errors.append(f"Locked assignment date {row.work_date} is outside the schedule period.")

	for work_date, group in locked.groupby("work_date") if not locked.empty else []:
		if len(group) > required_count:
			errors.append(f"{work_date} has {len(group)} locked assignments but only requires {required_count} resident(s).")

	if not locked.empty and not availability.empty:
		hard_types = {"vacation", "unavailable", "approved_absence", "medical_leave"}
		hard = availability[
			(availability["priority"].str.lower() == "hard")
			& (availability["availability_type"].str.lower().isin(hard_types))
		]
		conflicts = locked.merge(hard, on=["resident_id", "work_date"], suffixes=("_lock", "_availability"))
		for row in conflicts.itertuples():
			errors.append(f"Locked assignment conflict: resident_id {row.resident_id} is locked on {row.work_date} but marked hard unavailable.")

	return errors
