from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from residency_scheduler.db import init_db
from residency_scheduler.repository import create_schedule_period, save_residents

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
	monkeypatch.setenv("RESIDENCY_SCHEDULER_DB", str(tmp_path / "test.sqlite"))
	init_db()
	save_residents(
		pd.DataFrame(
			[
				{"name": "Ada", "email": "ada@example.com", "max_shifts": 10, "min_shifts": None, "weight": 1.0, "active": 1},
				{"name": "Ben", "email": "ben@example.com", "max_shifts": 10, "min_shifts": None, "weight": 1.0, "active": 1},
			]
		)
	)
	create_schedule_period(2026, 9, "Smoke draft", 1, None)


@pytest.mark.parametrize(
	"script_path",
	[
		"app.py",
		"pages/1_Residents.py",
		"pages/2_Availability_and_Preferences.py",
		"pages/3_Special_Rules.py",
		"pages/4_Generate_Schedule.py",
	],
)
def test_streamlit_script_loads_without_exceptions(isolated_db, script_path: str):
	app = AppTest.from_file(str(ROOT / script_path))
	app.run(timeout=5)

	assert not app.exception


def test_home_defaults_to_current_year_month(isolated_db):
	app = AppTest.from_file(str(ROOT / "app.py"))
	app.run(timeout=5)

	assert not app.exception
	assert app.selectbox[0].value.startswith(f"{date.today().year}-{date.today().month:02d}")
