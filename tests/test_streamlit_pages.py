from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from residency_scheduler.auth import AUTH_SESSION_KEY, GOOGLE_CALENDAR_SCOPES
from residency_scheduler.db import init_db
from residency_scheduler.repository import get_or_create_schedule_period, save_residents

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
	monkeypatch.setenv("RESIDENCY_SCHEDULER_DB", str(tmp_path / "test.sqlite"))
	monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
	monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
	monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:8501")
	init_db()
	save_residents(
		pd.DataFrame(
			[
				{"name": "Ada", "email": "ada@example.com", "max_shifts": 10, "min_shifts": None, "weight": 1.0, "active": 1},
				{"name": "Ben", "email": "ben@example.com", "max_shifts": 10, "min_shifts": None, "weight": 1.0, "active": 1},
			]
		)
	)
	get_or_create_schedule_period(2026, 9, required_count=1)


def authenticated_session() -> dict:
	return {
		"google_sub": "test-google-sub",
		"email": "tester@example.org",
		"name": "Test User",
		"picture": "",
		"profile": {"sub": "test-google-sub", "email": "tester@example.org"},
		"token": {},
		"scopes": ["openid", "email", "profile", *GOOGLE_CALENDAR_SCOPES],
		"expires_at": "2099-01-01T00:00:00+00:00",
	}


@pytest.mark.parametrize(
	"script_path",
	[
		"app.py",
		"pages/1_Residents.py",
		"pages/2_Availability_and_Preferences.py",
		"pages/3_Scheduling_Rules.py",
		"pages/4_Generate_Schedule.py",
	],
)
def test_streamlit_script_loads_authenticated_without_exceptions(isolated_db, script_path: str):
	app = AppTest.from_file(str(ROOT / script_path))
	app.session_state[AUTH_SESSION_KEY] = authenticated_session()
	app.run(timeout=5)

	assert not app.exception


def test_unauthenticated_home_shows_sign_in_gate(isolated_db):
	app = AppTest.from_file(str(ROOT / "app.py"))
	app.run(timeout=5)

	assert not app.exception
	assert app.title[0].value == "Residency Scheduler"
	assert not app.error
	assert len(app.selectbox) == 0


def test_unauthenticated_direct_page_shows_sign_in_gate(isolated_db):
	app = AppTest.from_file(str(ROOT / "pages/4_Generate_Schedule.py"))
	app.run(timeout=5)

	assert not app.exception
	assert not app.error
	assert len(app.slider) == 0


def test_home_defaults_to_current_year_month(isolated_db):
	app = AppTest.from_file(str(ROOT / "app.py"))
	app.session_state[AUTH_SESSION_KEY] = authenticated_session()
	app.run(timeout=5)

	assert not app.exception
	assert app.selectbox[0].value.startswith(f"{date.today().year}-{date.today().month:02d}")


def test_scheduling_rules_page_renders_rule_builder(isolated_db):
	app = AppTest.from_file(str(ROOT / "pages/3_Scheduling_Rules.py"))
	app.session_state[AUTH_SESSION_KEY] = authenticated_session()
	app.run(timeout=5)

	assert not app.exception
	selectbox_labels = [item.label for item in app.selectbox]
	number_labels = [item.label for item in app.number_input]
	button_labels = [item.label for item in app.button]
	assert "Rule type" in selectbox_labels
	assert "Resident" in selectbox_labels
	assert "Priority" in selectbox_labels
	assert "Target count" in number_labels
	assert "Save scheduling rules" not in button_labels
