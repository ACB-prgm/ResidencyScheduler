from __future__ import annotations

import pandas as pd

from residency_scheduler import cache
from residency_scheduler.db import get_database_url


def test_local_neon_file_is_ignored_by_runtime_config(tmp_path, monkeypatch):
	monkeypatch.chdir(tmp_path)
	(tmp_path / "NeonPostgres").write_text("postgresql://user:pass@example.com/db", encoding="utf-8")
	for key in [
		"RESIDENCY_SCHEDULER_DB",
		"RESIDENCY_SCHEDULER_DATABASE_URL",
		"DATABASE_URL",
		"NEON_DATABASE_URL",
	]:
		monkeypatch.delenv(key, raising=False)

	assert get_database_url().startswith("sqlite:///")


def test_local_sqlite_fallback_is_only_when_no_neon_is_configured(tmp_path, monkeypatch):
	monkeypatch.chdir(tmp_path)
	for key in [
		"RESIDENCY_SCHEDULER_DB",
		"RESIDENCY_SCHEDULER_DATABASE_URL",
		"DATABASE_URL",
		"NEON_DATABASE_URL",
	]:
		monkeypatch.delenv(key, raising=False)

	assert get_database_url().startswith("sqlite:///")


def test_explicit_database_url_wins_over_local_sqlite(tmp_path, monkeypatch):
	monkeypatch.chdir(tmp_path)
	monkeypatch.delenv("RESIDENCY_SCHEDULER_DB", raising=False)
	monkeypatch.setenv("NEON_DATABASE_URL", "postgresql://explicit/db")

	assert get_database_url() == "postgresql://explicit/db"


def test_home_cache_path_does_not_load_month_context(monkeypatch):
	monkeypatch.setenv("RESIDENCY_SCHEDULER_DB", "data/test_cache_path.sqlite")
	calls: list[str] = []
	monkeypatch.setattr(cache.repository, "get_calendar_months", lambda: calls.append("calendar") or pd.DataFrame())
	monkeypatch.setattr(cache.repository, "get_residents", lambda active_only=False: calls.append("residents") or pd.DataFrame())
	monkeypatch.setattr(cache.repository, "get_resident_options", lambda active_only=True: calls.append("options") or {})
	monkeypatch.setattr(cache.repository, "get_period", lambda period_id: calls.append("period") or {"id": period_id})
	monkeypatch.setattr(cache.repository, "get_schedule_requests_for_editor", lambda period_id: calls.append("requests") or pd.DataFrame())
	monkeypatch.setattr(cache.repository, "get_schedule_rules_for_editor", lambda period_id: calls.append("rules") or pd.DataFrame())
	monkeypatch.setattr(cache.repository, "get_assignments", lambda period_id: calls.append("assignments") or pd.DataFrame())
	monkeypatch.setattr(cache.repository, "get_workload_summary", lambda period_id: calls.append("workload") or pd.DataFrame())
	monkeypatch.setattr(cache.repository, "get_preference_violations", lambda period_id: calls.append("violations") or pd.DataFrame())
	monkeypatch.setattr(cache.repository, "get_schedule_runs", lambda period_id, limit=None: calls.append("runs") or pd.DataFrame())
	cache.clear_all_data_caches()

	cache.get_cached_calendar_months()
	cache.get_cached_period(1)

	assert calls == ["calendar", "period"]


def test_availability_cache_path_loads_only_request_dependencies(monkeypatch):
	monkeypatch.setenv("RESIDENCY_SCHEDULER_DB", "data/test_cache_path.sqlite")
	calls: list[str] = []
	monkeypatch.setattr(cache.repository, "get_residents", lambda active_only=False: calls.append(f"residents:{active_only}") or pd.DataFrame())
	monkeypatch.setattr(cache.repository, "get_resident_options", lambda active_only=True: calls.append("options") or {})
	monkeypatch.setattr(cache.repository, "get_period", lambda period_id: calls.append("period") or {"id": period_id})
	monkeypatch.setattr(cache.repository, "get_schedule_requests_for_editor", lambda period_id: calls.append("requests") or pd.DataFrame())
	cache.clear_all_data_caches()

	cache.get_cached_residents(active_only=True)
	cache.get_cached_resident_options(active_only=True)
	cache.get_cached_period(1)
	cache.get_cached_schedule_requests_for_editor(1)

	assert calls == ["residents:True", "options", "period", "requests"]


def test_generate_cache_path_loads_full_review_context(monkeypatch):
	monkeypatch.setenv("RESIDENCY_SCHEDULER_DB", "data/test_cache_path.sqlite")
	calls: list[str] = []
	monkeypatch.setattr(cache.repository, "get_period", lambda period_id: calls.append("period") or {"id": period_id})
	monkeypatch.setattr(cache.repository, "get_schedule_requests_for_editor", lambda period_id: calls.append("requests") or pd.DataFrame())
	monkeypatch.setattr(cache.repository, "get_schedule_rules_for_editor", lambda period_id: calls.append("rules") or pd.DataFrame())
	monkeypatch.setattr(cache.repository, "get_assignments", lambda period_id: calls.append("assignments") or pd.DataFrame())
	monkeypatch.setattr(cache.repository, "get_workload_summary", lambda period_id: calls.append("workload") or pd.DataFrame())
	monkeypatch.setattr(cache.repository, "get_preference_violations", lambda period_id: calls.append("violations") or pd.DataFrame())
	monkeypatch.setattr(cache.repository, "get_schedule_runs", lambda period_id, limit=None: calls.append(f"runs:{limit}") or pd.DataFrame())
	cache.clear_all_data_caches()

	cache.get_cached_month_context(1)

	assert calls == ["period", "requests", "rules", "assignments", "workload", "violations", "runs:1"]


def test_local_sqlite_cache_avoids_repeated_remote_loader_calls(tmp_path, monkeypatch):
	cache_path = tmp_path / "read_through_cache.sqlite"
	monkeypatch.setattr(cache, "primary_database_is_remote", lambda: True)
	monkeypatch.setattr(cache, "get_cache_db_path", lambda: cache_path)
	calls = {"count": 0}

	def loader():
		calls["count"] += 1
		return pd.DataFrame([{"value": 1}])

	first = cache._read_through_local_cache("test:key", loader)
	second = cache._read_through_local_cache("test:key", loader)

	assert calls["count"] == 1
	assert first.equals(second)


def test_month_cache_clear_removes_versioned_remote_cache_entries(tmp_path, monkeypatch):
	cache_path = tmp_path / "read_through_cache.sqlite"
	monkeypatch.setattr(cache, "primary_database_is_remote", lambda: True)
	monkeypatch.setattr(cache, "get_cache_db_path", lambda: cache_path)
	calls = {"count": 0}

	def loader():
		calls["count"] += 1
		return pd.DataFrame([{"value": calls["count"]}])

	first = cache._read_through_local_cache("month:1:requests_editor", loader)
	cache.clear_month_data_cache()
	second = cache._read_through_local_cache("month:1:requests_editor", loader)

	assert calls["count"] == 2
	assert int(first.iloc[0]["value"]) == 1
	assert int(second.iloc[0]["value"]) == 2


def test_resident_access_snapshot_reuses_cached_roster(tmp_path, monkeypatch):
	monkeypatch.setenv("RESIDENCY_SCHEDULER_DB", str(tmp_path / "access-cache.sqlite"))
	monkeypatch.setattr(cache, "primary_database_is_remote", lambda: False)
	calls = {"count": 0}

	def load_residents(active_only=False):
		assert active_only is False
		calls["count"] += 1
		return pd.DataFrame(
			[
				{"email": " Ada@Example.com "},
				{"email": "ada@example.com"},
				{"email": ""},
			]
		)

	monkeypatch.setattr(cache.repository, "get_residents", load_residents)
	cache.clear_all_data_caches()

	first = cache.get_cached_resident_access_snapshot()
	second = cache.get_cached_resident_access_snapshot()

	assert calls["count"] == 1
	assert first == second
	assert first["emails"] == ("ada@example.com",)
	assert first["fingerprint"]


def test_reference_cache_clear_refreshes_resident_access_snapshot(tmp_path, monkeypatch):
	monkeypatch.setenv("RESIDENCY_SCHEDULER_DB", str(tmp_path / "access-refresh.sqlite"))
	monkeypatch.setattr(cache, "primary_database_is_remote", lambda: False)
	emails = ["first@example.com"]

	monkeypatch.setattr(
		cache.repository,
		"get_residents",
		lambda active_only=False: pd.DataFrame([{"email": value} for value in emails]),
	)
	cache.clear_all_data_caches()

	first = cache.get_cached_resident_access_snapshot()
	emails[:] = ["second@example.com"]
	still_cached = cache.get_cached_resident_access_snapshot()
	cache.clear_reference_data_cache()
	refreshed = cache.get_cached_resident_access_snapshot()

	assert still_cached == first
	assert refreshed["emails"] == ("second@example.com",)
	assert refreshed["fingerprint"] != first["fingerprint"]


def test_request_cache_clear_keeps_unrelated_month_cache_entries(tmp_path, monkeypatch):
	cache_path = tmp_path / "request-cache.sqlite"
	monkeypatch.setattr(cache, "primary_database_is_remote", lambda: True)
	monkeypatch.setattr(cache, "get_cache_db_path", lambda: cache_path)
	calls = {"requests": 0, "context": 0, "assignments": 0}

	def load(name):
		calls[name] += 1
		return pd.DataFrame([{"value": calls[name]}])

	cache._read_through_local_cache("month:1:requests_editor", lambda: load("requests"))
	cache._read_through_local_cache("month:1:context", lambda: load("context"))
	cache._read_through_local_cache("month:1:assignments", lambda: load("assignments"))

	cache.clear_schedule_request_cache()

	cache._read_through_local_cache("month:1:requests_editor", lambda: load("requests"))
	cache._read_through_local_cache("month:1:context", lambda: load("context"))
	cache._read_through_local_cache("month:1:assignments", lambda: load("assignments"))

	assert calls == {"requests": 2, "context": 2, "assignments": 1}
