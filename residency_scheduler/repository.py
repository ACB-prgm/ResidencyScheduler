from __future__ import annotations

import calendar
import json
from datetime import date
from typing import Iterable

import pandas as pd

from residency_scheduler.db import get_connection


HARD_UNAVAILABLE_TYPES = {"vacation", "unavailable", "approved_absence", "medical_leave"}
AVAILABILITY_TYPES = HARD_UNAVAILABLE_TYPES | {"prefer_off", "prefer_work"}
PRIORITIES = {"hard", "soft"}


def read_sql(query: str, params: tuple = ()) -> pd.DataFrame:
	with get_connection() as conn:
		return pd.read_sql_query(query, conn, params=params)


def get_schedule_periods() -> pd.DataFrame:
	return read_sql(
		"""
		SELECT id, year, month, required_count, status, google_calendar_id, created_at
		FROM schedule_periods
		ORDER BY year DESC, month DESC
		"""
	)


def create_schedule_period(year: int, month: int, required_count: int, google_calendar_id: str | None) -> int:
	if month < 1 or month > 12:
		raise ValueError("Month must be between 1 and 12.")
	if year < 2024 or year > 2100:
		raise ValueError("Year must be between 2024 and 2100.")
	if required_count < 1:
		raise ValueError("Residents per night must be at least 1.")

	with get_connection() as conn:
		cursor = conn.execute(
			"""
			INSERT INTO schedule_periods (year, month, required_count, google_calendar_id)
			VALUES (?, ?, ?, ?)
			ON CONFLICT(year, month) DO UPDATE SET
				required_count = excluded.required_count,
				google_calendar_id = excluded.google_calendar_id
			RETURNING id
			""",
			(year, month, required_count, google_calendar_id),
		)
		return int(cursor.fetchone()[0])


def get_period(period_id: int) -> dict:
	with get_connection() as conn:
		row = conn.execute(
			"SELECT * FROM schedule_periods WHERE id = ?",
			(period_id,),
		).fetchone()
		if row is None:
			raise ValueError(f"Schedule period #{period_id} was not found.")
		return dict(row)


def period_dates(period_id: int) -> list[str]:
	period = get_period(period_id)
	last_day = calendar.monthrange(int(period["year"]), int(period["month"]))[1]
	return [date(int(period["year"]), int(period["month"]), day).isoformat() for day in range(1, last_day + 1)]


def get_residents(active_only: bool = False) -> pd.DataFrame:
	where = "WHERE active = 1" if active_only else ""
	return read_sql(
		f"""
		SELECT id, name, email, max_shifts, min_shifts, weight, active
		FROM residents
		{where}
		ORDER BY name
		"""
	)


def save_residents(df: pd.DataFrame) -> None:
	"""Synchronize roster edits while preserving existing resident IDs.

	Rows with an id are updated in place. Rows without an id are inserted.
	Existing residents omitted from the editor are marked inactive instead of
	deleted so historical availability, locks, and assignments remain intact.
	"""
	required_columns = ["id", "name", "email", "max_shifts", "min_shifts", "weight", "active"]
	clean = df.copy()

	for column in required_columns:
		if column not in clean.columns:
			clean[column] = None

	clean = clean[required_columns]
	clean = clean[clean["name"].notna() & (clean["name"].astype(str).str.strip() != "")]
	clean["name"] = clean["name"].astype(str).str.strip()
	clean["email"] = clean["email"].fillna("").astype(str).str.strip()
	clean["id"] = pd.to_numeric(clean["id"], errors="coerce").astype("Int64")
	clean["max_shifts"] = pd.to_numeric(clean["max_shifts"], errors="coerce").astype("Int64")
	clean["min_shifts"] = pd.to_numeric(clean["min_shifts"], errors="coerce").astype("Int64")
	clean["weight"] = pd.to_numeric(clean["weight"], errors="coerce").fillna(1.0)
	clean["active"] = clean["active"].fillna(1).astype(int)

	with get_connection() as conn:
		existing_ids = {int(row[0]) for row in conn.execute("SELECT id FROM residents").fetchall()}
		seen_ids: set[int] = set()
		for row in clean.where(pd.notnull(clean), None).itertuples(index=False):
			resident_id_value = None if pd.isna(row.id) else int(row.id)
			max_shifts = None if pd.isna(row.max_shifts) else int(row.max_shifts)
			min_shifts = None if pd.isna(row.min_shifts) else int(row.min_shifts)
			if resident_id_value is None:
				conn.execute(
					"""
					INSERT INTO residents (name, email, max_shifts, min_shifts, weight, active)
					VALUES (?, ?, ?, ?, ?, ?)
					""",
					(row.name, row.email, max_shifts, min_shifts, float(row.weight), int(row.active)),
				)
				continue

			resident_id = resident_id_value
			seen_ids.add(resident_id)
			if resident_id not in existing_ids:
				raise ValueError(f"Resident ID {resident_id} does not exist.")
			conn.execute(
				"""
				UPDATE residents
				SET name = ?, email = ?, max_shifts = ?, min_shifts = ?, weight = ?,
					active = ?, updated_at = CURRENT_TIMESTAMP
				WHERE id = ?
				""",
				(row.name, row.email, max_shifts, min_shifts, float(row.weight), int(row.active), resident_id),
			)

		removed_ids = existing_ids - seen_ids
		for resident_id in removed_ids:
			conn.execute(
				"UPDATE residents SET active = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
				(resident_id,),
			)


def replace_residents(df: pd.DataFrame) -> None:
	"""Backward-compatible alias for old UI code."""
	save_residents(df)


def get_availability(period_id: int) -> pd.DataFrame:
	return read_sql(
		"""
		SELECT a.id, a.period_id, a.resident_id, r.name AS resident_name, a.work_date,
			a.availability_type, a.priority, a.reason
		FROM availability a
		JOIN residents r ON r.id = a.resident_id
		WHERE a.period_id = ?
		ORDER BY a.work_date, r.name
		""",
		(period_id,),
	)


def replace_availability(period_id: int, df: pd.DataFrame) -> None:
	columns = ["resident_id", "work_date", "availability_type", "priority", "reason"]
	clean = _clean_period_table(df, columns)
	_validate_period_rows(period_id, clean, require_date=True, require_resident=True)
	for row in clean.itertuples():
		availability_type = str(row.availability_type).lower()
		priority = str(row.priority).lower()
		if availability_type not in AVAILABILITY_TYPES:
			raise ValueError(f"Invalid availability type '{row.availability_type}'.")
		if priority not in PRIORITIES:
			raise ValueError(f"Invalid priority '{row.priority}'.")
		if priority == "hard" and availability_type not in HARD_UNAVAILABLE_TYPES:
			raise ValueError("Only unavailable/vacation/leave types can be marked hard.")

	with get_connection() as conn:
		conn.execute("DELETE FROM availability WHERE period_id = ?", (period_id,))
		conn.executemany(
			"""
			INSERT INTO availability (period_id, resident_id, work_date, availability_type, priority, reason)
			VALUES (?, ?, ?, ?, ?, ?)
			""",
			[(period_id, *row) for row in clean.itertuples(index=False, name=None)],
		)


def get_locked_assignments(period_id: int) -> pd.DataFrame:
	return read_sql(
		"""
		SELECT l.id, l.period_id, l.work_date, l.resident_id, r.name AS resident_name, l.reason
		FROM locked_assignments l
		JOIN residents r ON r.id = l.resident_id
		WHERE l.period_id = ?
		ORDER BY l.work_date, r.name
		""",
		(period_id,),
	)


def replace_locked_assignments(period_id: int, df: pd.DataFrame) -> None:
	columns = ["work_date", "resident_id", "reason"]
	clean = _clean_period_table(df, columns)
	_validate_period_rows(period_id, clean, require_date=True, require_resident=True)
	_validate_locked_assignments(period_id, clean)

	with get_connection() as conn:
		conn.execute("DELETE FROM locked_assignments WHERE period_id = ?", (period_id,))
		conn.executemany(
			"""
			INSERT OR IGNORE INTO locked_assignments (period_id, work_date, resident_id, reason)
			VALUES (?, ?, ?, ?)
			""",
			[(period_id, *row) for row in clean.itertuples(index=False, name=None)],
		)


def get_assignments(period_id: int) -> pd.DataFrame:
	return read_sql(
		"""
		SELECT a.id, a.period_id, a.work_date, a.resident_id, r.name AS resident_name,
			a.source, a.is_locked, a.google_event_id
		FROM assignments a
		JOIN residents r ON r.id = a.resident_id
		WHERE a.period_id = ?
		ORDER BY a.work_date, r.name
		""",
		(period_id,),
	)


def save_assignments(period_id: int, assignments: Iterable[dict]) -> None:
	rows = [
		(
			period_id,
			item["work_date"],
			item["resident_id"],
			item.get("source", "solver"),
			int(item.get("is_locked", 0)),
		)
		for item in assignments
	]

	with get_connection() as conn:
		conn.execute("DELETE FROM assignments WHERE period_id = ?", (period_id,))
		conn.executemany(
			"""
			INSERT INTO assignments (period_id, work_date, resident_id, source, is_locked)
			VALUES (?, ?, ?, ?, ?)
			""",
			rows,
		)


def update_assignment_resident(
	assignment_id: int,
	resident_id: int,
	make_locked: bool = False,
	lock_reason: str | None = None,
) -> None:
	with get_connection() as conn:
		current = conn.execute(
			"SELECT * FROM assignments WHERE id = ?",
			(assignment_id,),
		).fetchone()
		if current is None:
			raise ValueError(f"Assignment #{assignment_id} was not found.")
		if int(current["is_locked"]) == 1:
			raise ValueError("Locked assignments cannot be reassigned from the review page.")

		_validate_manual_assignment(
			int(current["period_id"]),
			str(current["work_date"]),
			int(resident_id),
			exclude_assignment_id=assignment_id,
		)
		conn.execute(
			"""
			UPDATE assignments
			SET resident_id = ?, source = 'manual', is_locked = ?, updated_at = CURRENT_TIMESTAMP
			WHERE id = ?
			""",
			(int(resident_id), int(make_locked), assignment_id),
		)
		if make_locked:
			conn.execute(
				"""
				INSERT OR IGNORE INTO locked_assignments (period_id, work_date, resident_id, reason)
				VALUES (?, ?, ?, ?)
				""",
				(int(current["period_id"]), str(current["work_date"]), int(resident_id), lock_reason or "Manual review edit"),
			)


def update_assignment_google_event_id(assignment_id: int, google_event_id: str) -> None:
	with get_connection() as conn:
		conn.execute(
			"""
			UPDATE assignments
			SET google_event_id = ?, updated_at = CURRENT_TIMESTAMP
			WHERE id = ?
			""",
			(google_event_id, assignment_id),
		)


def get_schedule_runs(period_id: int) -> pd.DataFrame:
	return read_sql(
		"""
		SELECT id, period_id, run_at, solver_status, objective_score, warnings_json
		FROM schedule_runs
		WHERE period_id = ?
		ORDER BY run_at DESC, id DESC
		""",
		(period_id,),
	)


def get_workload_summary(period_id: int) -> pd.DataFrame:
	assignments = get_assignments(period_id)
	if assignments.empty:
		return pd.DataFrame(columns=["resident_name", "total_shifts", "weekend_shifts", "locked_shifts", "manual_shifts"])

	work_dates = pd.to_datetime(assignments["work_date"])
	assignments = assignments.assign(is_weekend=work_dates.dt.weekday >= 5)
	return (
		assignments.groupby("resident_name")
		.agg(
			total_shifts=("id", "count"),
			weekend_shifts=("is_weekend", "sum"),
			locked_shifts=("is_locked", "sum"),
			manual_shifts=("source", lambda values: int((values == "manual").sum())),
		)
		.reset_index()
		.sort_values(["total_shifts", "weekend_shifts", "resident_name"], ascending=[False, False, True])
	)


def get_preference_violations(period_id: int) -> pd.DataFrame:
	assignments = get_assignments(period_id)
	availability = get_availability(period_id)
	if assignments.empty or availability.empty:
		return pd.DataFrame(columns=["work_date", "resident_name", "availability_type", "priority", "reason"])

	soft = availability[availability["priority"].str.lower() == "soft"].copy()
	if soft.empty:
		return pd.DataFrame(columns=["work_date", "resident_name", "availability_type", "priority", "reason"])

	violations = assignments.merge(
		soft,
		on=["resident_id", "work_date"],
		suffixes=("_assignment", "_availability"),
	)
	violations = violations[violations["availability_type"].str.lower() == "prefer_off"]
	return violations[["work_date", "resident_name_assignment", "availability_type", "priority", "reason"]].rename(
		columns={"resident_name_assignment": "resident_name"}
	)


def record_solver_run(period_id: int, solver_status: str, objective_score: float | None, warnings: list[str]) -> None:
	with get_connection() as conn:
		conn.execute(
			"""
			INSERT INTO schedule_runs (period_id, solver_status, objective_score, warnings_json)
			VALUES (?, ?, ?, ?)
			""",
			(period_id, solver_status, objective_score, json.dumps(warnings)),
		)


def _clean_period_table(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
	clean = df.copy()
	for column in columns:
		if column not in clean.columns:
			clean[column] = None

	clean = clean[columns]
	for column in columns:
		clean[column] = clean[column].where(pd.notnull(clean[column]), None)

	if "work_date" in clean.columns:
		clean = clean[clean["work_date"].notna()]
		clean["work_date"] = clean["work_date"].astype(str)

	if "resident_id" in clean.columns:
		clean = clean[clean["resident_id"].notna()]
		clean["resident_id"] = pd.to_numeric(clean["resident_id"], errors="coerce").astype("Int64")
		clean = clean[clean["resident_id"].notna()]
		clean["resident_id"] = clean["resident_id"].astype(int)

	return clean


def _validate_period_rows(
	period_id: int,
	df: pd.DataFrame,
	require_date: bool,
	require_resident: bool,
) -> None:
	if df.empty:
		return

	valid_dates = set(period_dates(period_id))
	valid_residents = set(get_residents(active_only=True)["id"].astype(int).tolist())

	for row in df.itertuples():
		if require_date and str(row.work_date) not in valid_dates:
			raise ValueError(f"Date {row.work_date} is outside the selected schedule period.")
		if require_resident and int(row.resident_id) not in valid_residents:
			raise ValueError(f"Resident ID {row.resident_id} is not an active resident.")


def _validate_locked_assignments(period_id: int, locked: pd.DataFrame) -> None:
	if locked.empty:
		return

	required_count = int(get_period(period_id)["required_count"])
	for work_date, group in locked.groupby("work_date"):
		if len(group) > required_count:
			raise ValueError(f"{work_date} has {len(group)} locked assignments but only requires {required_count} resident(s).")

	availability = get_availability(period_id)
	if availability.empty:
		return

	hard = availability[
		(availability["priority"].str.lower() == "hard")
		& (availability["availability_type"].str.lower().isin(HARD_UNAVAILABLE_TYPES))
	]
	conflicts = locked.merge(hard, on=["resident_id", "work_date"])
	if not conflicts.empty:
		first = conflicts.iloc[0]
		raise ValueError(
			f"Locked assignment conflict: resident_id {int(first.resident_id)} is locked on {first.work_date} but marked hard unavailable."
		)


def _validate_manual_assignment(
	period_id: int,
	work_date: str,
	resident_id: int,
	exclude_assignment_id: int | None = None,
) -> None:
	_validate_period_rows(
		period_id,
		pd.DataFrame([{"work_date": work_date, "resident_id": resident_id}]),
		require_date=True,
		require_resident=True,
	)

	availability = get_availability(period_id)
	hard = availability[
		(availability["resident_id"].astype(int) == int(resident_id))
		& (availability["work_date"].astype(str) == str(work_date))
		& (availability["priority"].str.lower() == "hard")
		& (availability["availability_type"].str.lower().isin(HARD_UNAVAILABLE_TYPES))
	]
	if not hard.empty:
		raise ValueError("Selected resident is hard unavailable on that date.")

	with get_connection() as conn:
		params: list[object] = [period_id, work_date, resident_id]
		exclude = ""
		if exclude_assignment_id is not None:
			exclude = "AND id <> ?"
			params.append(exclude_assignment_id)
		duplicate = conn.execute(
			f"""
			SELECT id FROM assignments
			WHERE period_id = ? AND work_date = ? AND resident_id = ?
			{exclude}
			""",
			tuple(params),
		).fetchone()
		if duplicate is not None:
			raise ValueError("Selected resident is already assigned on that date.")

		resident = conn.execute("SELECT max_shifts FROM residents WHERE id = ?", (resident_id,)).fetchone()
		if resident is not None and resident["max_shifts"] is not None:
			count_params: list[object] = [period_id, resident_id]
			exclude_count = ""
			if exclude_assignment_id is not None:
				exclude_count = "AND id <> ?"
				count_params.append(exclude_assignment_id)
			current_total = conn.execute(
				f"""
				SELECT COUNT(*) FROM assignments
				WHERE period_id = ? AND resident_id = ?
				{exclude_count}
				""",
				tuple(count_params),
			).fetchone()[0]
			if int(current_total) + 1 > int(resident["max_shifts"]):
				raise ValueError("Selected resident would exceed their configured max monthly shifts.")
