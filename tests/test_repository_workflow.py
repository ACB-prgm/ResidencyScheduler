from __future__ import annotations

import pandas as pd
import pytest

from residency_scheduler.db import init_db
from residency_scheduler.repository import (
	create_schedule_period,
	get_assignments,
	get_residents,
	save_residents,
	update_assignment_resident,
)
from residency_scheduler.solver import solve_period


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
	monkeypatch.setenv("RESIDENCY_SCHEDULER_DB", str(tmp_path / "test.sqlite"))
	init_db()


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


def test_manual_reassignment_marks_assignment_manual(isolated_db):
	save_residents(
		pd.DataFrame(
			[
				{"name": "Ada", "email": "ada@example.com", "max_shifts": 20, "min_shifts": None, "weight": 1.0, "active": 1},
				{"name": "Ben", "email": "ben@example.com", "max_shifts": 20, "min_shifts": None, "weight": 1.0, "active": 1},
			]
		)
	)
	period_id = create_schedule_period(2026, 8, 1, None)
	result = solve_period(period_id, max_time_seconds=5)
	assert result.status in {"OPTIMAL", "FEASIBLE"}

	assignments = get_assignments(period_id)
	target = assignments.iloc[0]
	new_resident_id = 1 if int(target.resident_id) == 2 else 2

	update_assignment_resident(int(target.id), new_resident_id, make_locked=True, lock_reason="Test lock")

	updated = get_assignments(period_id)
	changed = updated.loc[updated["id"] == int(target.id)].iloc[0]
	assert int(changed.resident_id) == new_resident_id
	assert changed.source == "manual"
	assert int(changed.is_locked) == 1
