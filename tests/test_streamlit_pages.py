from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from residency_scheduler.auth import AUTH_SESSION_KEY, GOOGLE_CALENDAR_SCOPES
from residency_scheduler.db import init_db
from residency_scheduler.repository import get_or_create_schedule_period, get_schedule_requests_for_editor, save_residents

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
		"pages/0_Home.py",
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
	assert app.title[0].value == "Residency Call Scheduler"
	assert app.selectbox[0].value.startswith(f"{date.today().year}-{date.today().month:02d}")


@pytest.mark.parametrize(
	("script_path", "guide_label"),
	[
		("app.py", "User Guide: Home"),
		("pages/0_Home.py", "User Guide: Home"),
		("pages/1_Residents.py", "User Guide: Residents"),
		("pages/2_Availability_and_Preferences.py", "User Guide: Availability and Preferences"),
		("pages/3_Scheduling_Rules.py", "User Guide: Scheduling Rules"),
		("pages/4_Generate_Schedule.py", "User Guide: Generate Schedule"),
	],
)
def test_each_page_renders_user_guide(isolated_db, script_path: str, guide_label: str):
	app = AppTest.from_file(str(ROOT / script_path))
	app.session_state[AUTH_SESSION_KEY] = authenticated_session()
	app.run(timeout=5)

	assert not app.exception
	assert guide_label in [item.label for item in app.expander]


def test_home_help_is_consolidated_and_month_settings_is_collapsed_section(isolated_db):
	app = AppTest.from_file(str(ROOT / "app.py"))
	app.session_state[AUTH_SESSION_KEY] = authenticated_session()
	app.run(timeout=5)

	expander_labels = [item.label for item in app.expander]
	assert "User Guide: Home" in expander_labels
	assert "Month settings" in expander_labels
	assert "Scheduling assumptions" not in expander_labels


def test_standalone_help_expanders_are_removed(isolated_db):
	availability = AppTest.from_file(str(ROOT / "pages/2_Availability_and_Preferences.py"))
	availability.session_state[AUTH_SESSION_KEY] = authenticated_session()
	availability.run(timeout=5)
	assert "Priority rules" not in [item.label for item in availability.expander]

	rules = AppTest.from_file(str(ROOT / "pages/3_Scheduling_Rules.py"))
	rules.session_state[AUTH_SESSION_KEY] = authenticated_session()
	rules.run(timeout=5)
	assert "Rule examples" not in [item.label for item in rules.expander]


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


def test_availability_resident_selection_persists_and_saves(isolated_db):
	app = AppTest.from_file(str(ROOT / "pages/2_Availability_and_Preferences.py"))
	app.session_state[AUTH_SESSION_KEY] = authenticated_session()
	app.run(timeout=5)

	assert not app.exception
	resident_selectbox = next(item for item in app.selectbox if item.label == "Resident")
	assert "Ben" in resident_selectbox.options

	resident_selectbox.select("Ben")
	app.run(timeout=5)
	assert next(item for item in app.selectbox if item.label == "Resident").value == "Ben"

	next(button for button in app.button if button.label == "Add availability or preference").click()
	app.run(timeout=5)

	period_id = get_or_create_schedule_period(date.today().year, date.today().month, required_count=1)
	requests = get_schedule_requests_for_editor(period_id)
	assert len(requests) == 1
	assert requests.iloc[0]["resident"] == "Ben"
