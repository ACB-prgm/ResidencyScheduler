from __future__ import annotations

import json
from typing import Iterable

import pandas as pd

from residency_scheduler.db import get_connection


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


def replace_residents(df: pd.DataFrame) -> None:
	required_columns = ["name", "email", "max_shifts", "min_shifts", "weight", "active"]
	clean = df.copy()

	for column in required_columns:
		if column not in clean.columns:
			clean[column] = None

	clean = clean[required_columns]
	clean = clean[clean["name"].notna() & (clean["name"].astype(str).str.strip() != "")]
	clean["name"] = clean["name"].astype(str).str.strip()
	clean["email"] = clean["email"].fillna("").astype(str).str.strip()
	clean["weight"] = pd.to_numeric(clean["weight"], errors="coerce").fillna(1.0)
	clean["active"] = clean["active"].fillna(1).astype(int)

	with get_connection() as conn:
		conn.execute("DELETE FROM residents")
		conn.executemany(
			"""
			INSERT INTO residents (name, email, max_shifts, min_shifts, weight, active)
			VALUES (?, ?, ?, ?, ?, ?)
			""",
			clean.where(pd.notnull(clean), None).itertuples(index=False, name=None),
		)


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
