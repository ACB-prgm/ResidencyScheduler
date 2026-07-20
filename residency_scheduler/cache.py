from __future__ import annotations

import hashlib
import pickle
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TypeVar

import pandas as pd
import streamlit as st

from residency_scheduler.db import get_cache_db_path, get_database_url, init_db, primary_database_is_remote
from residency_scheduler import repository

T = TypeVar("T")
CACHE_KEY_VERSION = "v6"
SCHEMA_INIT_VERSION = "recurring-preferences-v1"


def ensure_database_initialized() -> bool:
	return _ensure_database_initialized(get_database_url(), SCHEMA_INIT_VERSION)


@st.cache_resource(show_spinner=False)
def _ensure_database_initialized(database_url: str, schema_version: str) -> bool:
	init_db()
	return True


def preload_reference_data() -> None:
	get_cached_calendar_months()


def preload_month_data(period_id: int) -> None:
	get_cached_month_context(period_id)


def get_cached_calendar_months() -> pd.DataFrame:
	return _get_cached_calendar_months(get_database_url())


@st.cache_data(show_spinner=False)
def _get_cached_calendar_months(database_url: str) -> pd.DataFrame:
	return _read_through_local_cache("reference:calendar_months", repository.get_calendar_months)


def get_cached_residents(active_only: bool = False) -> pd.DataFrame:
	return _get_cached_residents(get_database_url(), active_only)


@st.cache_data(show_spinner=False)
def _get_cached_residents(database_url: str, active_only: bool = False) -> pd.DataFrame:
	return _read_through_local_cache(
		f"reference:residents:active={active_only}",
		lambda: repository.get_residents(active_only=active_only),
	)


def get_cached_resident_options(active_only: bool = True) -> dict[str, int]:
	return _get_cached_resident_options(get_database_url(), active_only)


@st.cache_data(show_spinner=False)
def _get_cached_resident_options(database_url: str, active_only: bool = True) -> dict[str, int]:
	return _read_through_local_cache(
		f"reference:resident_options:active={active_only}",
		lambda: repository.get_resident_options(active_only=active_only),
	)


def get_cached_resident_access_snapshot() -> dict[str, object]:
	return _get_cached_resident_access_snapshot(get_database_url())


@st.cache_data(show_spinner=False)
def _get_cached_resident_access_snapshot(database_url: str) -> dict[str, object]:
	residents = _get_cached_residents(database_url, active_only=False)
	emails = tuple(
		sorted(
			{
				str(value).strip().casefold()
				for value in residents.get("email", pd.Series(dtype="object")).tolist()
				if str(value or "").strip()
			}
		)
	)
	fingerprint = hashlib.sha256("\n".join(emails).encode("utf-8")).hexdigest()
	return {"emails": emails, "fingerprint": fingerprint}


def get_cached_period(period_id: int) -> dict:
	return _get_cached_period(get_database_url(), period_id)


def get_cached_or_create_schedule_period(year: int, month: int) -> int:
	return _get_cached_or_create_schedule_period(get_database_url(), year, month)


@st.cache_data(show_spinner=False)
def _get_cached_or_create_schedule_period(database_url: str, year: int, month: int) -> int:
	return _read_through_local_cache(
		f"month_lookup:{int(year)}-{int(month):02d}",
		lambda: repository.get_or_create_schedule_period(year, month),
	)


@st.cache_data(show_spinner=False)
def _get_cached_period(database_url: str, period_id: int) -> dict:
	return _read_through_local_cache(f"month:{period_id}:period", lambda: repository.get_period(period_id))


def get_cached_schedule_requests_for_editor(period_id: int) -> pd.DataFrame:
	return _get_cached_schedule_requests_for_editor(get_database_url(), period_id)


@st.cache_data(show_spinner=False)
def _get_cached_schedule_requests_for_editor(database_url: str, period_id: int) -> pd.DataFrame:
	return _read_through_local_cache(
		f"month:{period_id}:requests_editor",
		lambda: repository.get_schedule_requests_for_editor(period_id),
	)


def get_cached_hard_schedule_requests_for_conflict_check() -> pd.DataFrame:
	return _get_cached_hard_schedule_requests_for_conflict_check(get_database_url())


@st.cache_data(show_spinner=False)
def _get_cached_hard_schedule_requests_for_conflict_check(database_url: str) -> pd.DataFrame:
	return _read_through_local_cache(
		"reference:hard_schedule_requests",
		repository.get_hard_schedule_requests_for_conflict_check,
	)


def get_cached_recurring_preferences_for_editor() -> pd.DataFrame:
	return _get_cached_recurring_preferences_for_editor(get_database_url())


@st.cache_data(show_spinner=False)
def _get_cached_recurring_preferences_for_editor(database_url: str) -> pd.DataFrame:
	return _read_through_local_cache(
		"reference:recurring_preferences_editor",
		repository.get_recurring_preferences_for_editor,
	)


def get_cached_schedule_rules_for_editor(period_id: int) -> pd.DataFrame:
	return _get_cached_schedule_rules_for_editor(get_database_url(), period_id)


@st.cache_data(show_spinner=False)
def _get_cached_schedule_rules_for_editor(database_url: str, period_id: int) -> pd.DataFrame:
	return _read_through_local_cache(
		f"month:{period_id}:rules_editor",
		lambda: repository.get_schedule_rules_for_editor(period_id),
	)


def get_cached_assignments(period_id: int) -> pd.DataFrame:
	return _get_cached_assignments(get_database_url(), period_id)


@st.cache_data(show_spinner=False)
def _get_cached_assignments(database_url: str, period_id: int) -> pd.DataFrame:
	return _read_through_local_cache(f"month:{period_id}:assignments", lambda: repository.get_assignments(period_id))


def get_cached_workload_summary(period_id: int) -> pd.DataFrame:
	return _get_cached_workload_summary(get_database_url(), period_id)


@st.cache_data(show_spinner=False)
def _get_cached_workload_summary(database_url: str, period_id: int) -> pd.DataFrame:
	return _read_through_local_cache(f"month:{period_id}:workload_summary", lambda: repository.get_workload_summary(period_id))


def get_cached_workload_summary_for_scope(period_id: int, scope: str) -> pd.DataFrame:
	return _get_cached_workload_summary_for_scope(get_database_url(), period_id, scope)


@st.cache_data(show_spinner=False)
def _get_cached_workload_summary_for_scope(database_url: str, period_id: int, scope: str) -> pd.DataFrame:
	scope_key = str(scope).strip().lower()
	return _read_through_local_cache(
		f"month:{period_id}:workload_summary:{scope_key}",
		lambda: repository.get_workload_summary_for_scope(period_id, scope_key),
	)


def get_cached_preference_violations(period_id: int) -> pd.DataFrame:
	return _get_cached_preference_violations(get_database_url(), period_id)


@st.cache_data(show_spinner=False)
def _get_cached_preference_violations(database_url: str, period_id: int) -> pd.DataFrame:
	return _read_through_local_cache(
		f"month:{period_id}:preference_violations",
		lambda: repository.get_preference_violations(period_id),
	)


def get_cached_latest_schedule_runs(period_id: int) -> pd.DataFrame:
	return _get_cached_latest_schedule_runs(get_database_url(), period_id)


@st.cache_data(show_spinner=False)
def _get_cached_latest_schedule_runs(database_url: str, period_id: int) -> pd.DataFrame:
	return _read_through_local_cache(f"month:{period_id}:latest_runs", lambda: repository.get_schedule_runs(period_id, limit=1))


def get_cached_month_context(period_id: int) -> dict:
	return _get_cached_month_context(get_database_url(), period_id)


@st.cache_data(show_spinner=False)
def _get_cached_month_context(database_url: str, period_id: int) -> dict:
	return _read_through_local_cache(
		f"month:{period_id}:context",
		lambda: {
			"period": repository.get_period(period_id),
			"requests_editor": repository.get_schedule_requests_for_editor(period_id),
			"rules_editor": repository.get_schedule_rules_for_editor(period_id),
			"assignments": repository.get_assignments(period_id),
			"workload_summary": repository.get_workload_summary(period_id),
			"preference_violations": repository.get_preference_violations(period_id),
			"latest_runs": repository.get_schedule_runs(period_id, limit=1),
		},
	)


def clear_reference_data_cache() -> None:
	_get_cached_calendar_months.clear()
	_get_cached_residents.clear()
	_get_cached_resident_options.clear()
	_get_cached_resident_access_snapshot.clear()
	_get_cached_recurring_preferences_for_editor.clear()
	_get_cached_hard_schedule_requests_for_conflict_check.clear()
	_clear_local_cache_prefix("reference:")


def clear_schedule_request_cache() -> None:
	"""Clear request-derived views without evicting unrelated month data."""
	_get_cached_schedule_requests_for_editor.clear()
	_get_cached_recurring_preferences_for_editor.clear()
	_get_cached_hard_schedule_requests_for_conflict_check.clear()
	_get_cached_preference_violations.clear()
	_get_cached_month_context.clear()
	_clear_local_cache_patterns(
		[
			"%:requests_editor",
			"%:preference_violations",
			"%:context",
			"%:reference:recurring_preferences_editor",
			"%:reference:hard_schedule_requests",
		]
	)


def clear_month_data_cache() -> None:
	_get_cached_or_create_schedule_period.clear()
	_get_cached_period.clear()
	_get_cached_schedule_requests_for_editor.clear()
	_get_cached_schedule_rules_for_editor.clear()
	_get_cached_assignments.clear()
	_get_cached_workload_summary.clear()
	_get_cached_workload_summary_for_scope.clear()
	_get_cached_preference_violations.clear()
	_get_cached_latest_schedule_runs.clear()
	_get_cached_month_context.clear()
	_clear_local_cache_prefix("month_lookup:")
	_clear_local_cache_prefix("month:")


def clear_all_data_caches() -> None:
	clear_reference_data_cache()
	clear_month_data_cache()


def _read_through_local_cache(cache_key: str, loader: Callable[[], T]) -> T:
	if not primary_database_is_remote():
		return loader()

	cache_key = f"{CACHE_KEY_VERSION}:{cache_key}"
	cached = _get_local_cache_value(cache_key)
	if cached is not None:
		return cached

	value = loader()
	_set_local_cache_value(cache_key, value)
	return value


def _get_local_cache_value(cache_key: str) -> T | None:
	with _local_cache_connection() as conn:
		_ensure_local_cache_schema(conn)
		row = conn.execute("SELECT payload FROM local_cache WHERE cache_key = ?", (cache_key,)).fetchone()
		if row is None:
			return None
		return pickle.loads(row[0])


def _set_local_cache_value(cache_key: str, value) -> None:
	with _local_cache_connection() as conn:
		_ensure_local_cache_schema(conn)
		conn.execute(
			"""
			INSERT INTO local_cache (cache_key, payload, updated_at)
			VALUES (?, ?, ?)
			ON CONFLICT(cache_key) DO UPDATE SET
				payload = excluded.payload,
				updated_at = excluded.updated_at
			""",
			(cache_key, sqlite3.Binary(pickle.dumps(value)), datetime.now(timezone.utc).isoformat()),
		)


def _clear_local_cache_prefix(prefix: str) -> None:
	if not primary_database_is_remote():
		return
	with _local_cache_connection() as conn:
		_ensure_local_cache_schema(conn)
		conn.execute(
			"""
			DELETE FROM local_cache
			WHERE cache_key LIKE ? OR cache_key LIKE ?
			""",
			(f"{prefix}%", f"%:{prefix}%"),
		)


def _clear_local_cache_patterns(patterns: list[str]) -> None:
	if not primary_database_is_remote() or not patterns:
		return
	with _local_cache_connection() as conn:
		_ensure_local_cache_schema(conn)
		where = " OR ".join("cache_key LIKE ?" for _ in patterns)
		conn.execute(f"DELETE FROM local_cache WHERE {where}", tuple(patterns))


def _local_cache_connection() -> sqlite3.Connection:
	cache_path = get_cache_db_path()
	cache_path.parent.mkdir(parents=True, exist_ok=True)
	return sqlite3.connect(cache_path)


def _ensure_local_cache_schema(conn: sqlite3.Connection) -> None:
	conn.execute(
		"""
		CREATE TABLE IF NOT EXISTS local_cache (
			cache_key TEXT PRIMARY KEY,
			payload BLOB NOT NULL,
			updated_at TEXT NOT NULL
		)
		"""
	)
