from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from residency_scheduler.auth import AUTH_SESSION_KEY, GOOGLE_CALENDAR_SCOPES
from residency_scheduler.db import init_db
from residency_scheduler.repository import (
	create_recurring_preference,
	create_schedule_rule,
	create_schedule_request,
	get_or_create_schedule_period,
	get_recurring_preferences_for_editor,
	get_assignments,
	get_schedule_requests_for_editor,
	get_schedule_rules,
	save_assignments,
	save_residents,
)

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
		"email": "ada@example.com",
		"name": "Test User",
		"picture": "",
		"profile": {"sub": "test-google-sub", "email": "ada@example.com"},
		"token": {},
		"scopes": ["openid", "email", "profile", *GOOGLE_CALENDAR_SCOPES],
		"expires_at": "2099-01-01T00:00:00+00:00",
	}


def unauthorized_session() -> dict:
	session = authenticated_session()
	session["email"] = "outsider@example.com"
	session["profile"] = {"sub": "test-google-sub", "email": "outsider@example.com"}
	return session


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
	assert len(app.sidebar.children) >= 1
	assert not app.error
	assert len(app.selectbox) == 0
	assert "User Guide: Google Sign-In" in [item.label for item in app.expander]
	assert any("Only the administrator" in item.value for item in app.markdown)


def test_authenticated_non_resident_email_is_blocked(isolated_db):
	app = AppTest.from_file(str(ROOT / "app.py"))
	app.session_state[AUTH_SESSION_KEY] = unauthorized_session()
	app.run(timeout=5)

	assert not app.exception
	assert app.error
	assert "not authorized to access the scheduler" in app.error[0].value
	assert "Save residents" not in [button.label for button in app.button]


def test_authenticated_sidebar_shows_logo_and_sign_out(isolated_db):
	app = AppTest.from_file(str(ROOT / "app.py"))
	app.session_state[AUTH_SESSION_KEY] = authenticated_session()
	app.run(timeout=5)

	assert not app.exception
	assert len(app.sidebar.children) >= 3
	assert "Sign out" in [button.label for button in app.button]


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


def test_generate_schedule_defaults_solver_max_time_to_120_seconds(isolated_db):
	app = AppTest.from_file(str(ROOT / "pages/4_Generate_Schedule.py"))
	app.session_state[AUTH_SESSION_KEY] = authenticated_session()
	app.run(timeout=5)

	assert not app.exception
	max_time = next(item for item in app.slider if item.label == "Solver max time, seconds")
	assert max_time.value == 120


def test_generate_schedule_wipe_requires_confirmation_and_deletes_only_local_assignments(isolated_db):
	period_id = get_or_create_schedule_period(date.today().year, date.today().month, required_count=1)
	save_assignments(period_id, [{"work_date": date.today().replace(day=1).isoformat(), "resident_id": 1}])
	app = AppTest.from_file(str(ROOT / "pages/4_Generate_Schedule.py"))
	auth_session = authenticated_session()
	auth_session["scopes"] = ["openid", "email", "profile"]
	app.session_state[AUTH_SESSION_KEY] = auth_session
	app.run(timeout=5)

	assert not app.exception
	assert "Wipe current schedule" in [item.label for item in app.expander]
	delete_button = next(button for button in app.button if button.label == "Delete local schedule")
	assert delete_button.disabled
	confirmation = next(
		item
		for item in app.checkbox
		if item.label == "I understand that the selected month's local assignments will be permanently deleted."
	)
	confirmation.set_value(True)
	app.run(timeout=5)
	delete_button = next(button for button in app.button if button.label == "Delete local schedule")
	assert not delete_button.disabled
	delete_button.click()
	app.run(timeout=5)

	assert get_assignments(period_id).empty
	assert any("No local assignments exist" in item.value for item in app.info)


def test_generate_schedule_guide_explains_replacement_wipe_and_publish_boundaries(isolated_db):
	app = AppTest.from_file(str(ROOT / "pages/4_Generate_Schedule.py"))
	app.session_state[AUTH_SESSION_KEY] = authenticated_session()
	app.run(timeout=5)

	guide_text = "\n".join(item.value for item in app.markdown)
	assert "Running it again replaces all current local assignments" in guide_text
	assert "Wipe current schedule" in guide_text
	assert "does not remove published Google Calendar events" in guide_text
	assert "Monday-Thursday = 1 point" in guide_text
	assert "Friday = 1.5 points" in guide_text
	assert "Saturday = 2 points" in guide_text
	assert "Sunday = 1.5 points" in guide_text
	assert "ICS export" in guide_text


def test_generate_schedule_places_weighted_workload_summary_below_calendar(isolated_db):
	period_id = get_or_create_schedule_period(date.today().year, date.today().month, required_count=1)
	save_assignments(period_id, [{"work_date": date.today().replace(day=1).isoformat(), "resident_id": 1}])
	app = AppTest.from_file(str(ROOT / "pages/4_Generate_Schedule.py"))
	app.session_state[AUTH_SESSION_KEY] = authenticated_session()
	app.run(timeout=5)

	assert not app.exception
	markdown_values = [item.value for item in app.markdown]
	assert markdown_values.index("### Calendar") < markdown_values.index("### Workload summary")
	workload_table = next(item.value for item in app.dataframe if "Workload Points" in item.value.columns)
	assert list(workload_table.columns) == [
		"Resident",
		"Total Shifts",
		"Weekday Shifts",
		"Friday Shifts",
		"Saturday Shifts",
		"Sunday Shifts",
		"Workload Points",
		"Hard Assigned Shifts",
		"Manual Shifts",
	]


def test_residents_guide_mentions_email_access_control(isolated_db):
	app = AppTest.from_file(str(ROOT / "pages/1_Residents.py"))
	app.session_state[AUTH_SESSION_KEY] = authenticated_session()
	app.run(timeout=5)

	assert not app.exception
	assert any("Only residents with email addresses listed here can sign in" in item.value for item in app.markdown)


def test_home_help_is_consolidated_and_month_settings_is_collapsed_section(isolated_db):
	app = AppTest.from_file(str(ROOT / "app.py"))
	app.session_state[AUTH_SESSION_KEY] = authenticated_session()
	app.run(timeout=5)

	expander_labels = [item.label for item in app.expander]
	assert "User Guide: Home" in expander_labels
	assert "Month settings" in expander_labels
	assert "Scheduling assumptions" not in expander_labels
	assert any("Every page has a User Guide at the top" in item.value for item in app.info)


def test_availability_guide_explains_inclusive_shift_start_dates(isolated_db):
	app = AppTest.from_file(str(ROOT / "pages/2_Availability_and_Preferences.py"))
	app.session_state[AUTH_SESSION_KEY] = authenticated_session()
	app.run(timeout=5)

	guide_text = "\n".join(item.value for item in app.markdown)
	assert "start and end dates are inclusive" in guide_text
	assert "August 14 through August 14" in guide_text
	assert "August 14 through August 16" in guide_text


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


def test_availability_end_date_is_not_before_start_date(isolated_db):
	app = AppTest.from_file(str(ROOT / "pages/2_Availability_and_Preferences.py"))
	app.session_state[AUTH_SESSION_KEY] = authenticated_session()
	period_id = get_or_create_schedule_period(date.today().year, date.today().month, required_count=1)
	app.session_state[f"dated_start_{period_id}"] = date(date.today().year, date.today().month, 10)
	app.session_state[f"dated_end_{period_id}"] = date(date.today().year, date.today().month, 5)
	app.run(timeout=5)

	assert not app.exception
	assert app.session_state[f"dated_end_{period_id}"] == app.session_state[f"dated_start_{period_id}"]


def test_availability_live_hard_conflict_disables_save_and_soft_clears_warning(isolated_db):
	period_id = get_or_create_schedule_period(date.today().year, date.today().month, required_count=1)
	month_start = date(date.today().year, date.today().month, 1)
	create_schedule_request(1, month_start, month_start, "vacation", "hard", "PTO")
	app = AppTest.from_file(str(ROOT / "pages/2_Availability_and_Preferences.py"))
	app.session_state[AUTH_SESSION_KEY] = authenticated_session()
	app.run(timeout=5)

	next(item for item in app.selectbox if item.label == "Availability type").select("assign")
	app.run(timeout=5)
	next(item for item in app.selectbox if item.label == "Priority").select("hard")
	app.run(timeout=5)

	save = next(button for button in app.button if button.label == "Add availability or preference")
	assert save.disabled
	assert any("hard Assign conflicts with saved hard Vacation" in item.value for item in app.warning)
	assert any("User Guide: Availability and Preferences" in item.value for item in app.warning)

	next(item for item in app.selectbox if item.label == "Priority").select("soft")
	app.run(timeout=5)

	save = next(button for button in app.button if button.label == "Add availability or preference")
	assert not save.disabled
	assert not any("Conflicting hard availability or preferences" in item.value for item in app.warning)


def test_availability_live_conflict_check_excludes_row_being_edited(isolated_db):
	period_id = get_or_create_schedule_period(date.today().year, date.today().month, required_count=1)
	month_start = date(date.today().year, date.today().month, 1)
	create_schedule_request(1, month_start, month_start, "assign", "hard", "Assigned")
	app = AppTest.from_file(str(ROOT / "pages/2_Availability_and_Preferences.py"))
	app.session_state[AUTH_SESSION_KEY] = authenticated_session()
	app.run(timeout=5)
	next(button for button in app.button if button.label == "Edit").click()
	app.run(timeout=5)

	save = next(button for button in app.button if button.label == "Save changes")
	assert not save.disabled
	assert not any("Conflicting hard availability or preferences" in item.value for item in app.warning)


def test_availability_page_edits_existing_row(isolated_db):
	period_id = get_or_create_schedule_period(date.today().year, date.today().month, required_count=1)
	create_schedule_request(1, date(date.today().year, date.today().month, 10), date(date.today().year, date.today().month, 10), "vacation", "hard", "Original")
	app = AppTest.from_file(str(ROOT / "pages/2_Availability_and_Preferences.py"))
	app.session_state[AUTH_SESSION_KEY] = authenticated_session()
	app.run(timeout=5)

	assert not app.exception
	assert "Edit" in [button.label for button in app.button]
	next(button for button in app.button if button.label == "Edit").click()
	app.run(timeout=5)

	assert not app.exception
	assert next(item for item in app.selectbox if item.label == "Resident").value == "Ada"
	next(item for item in app.selectbox if item.label == "Resident").select("Ben")
	app.run(timeout=5)
	assert "Save changes" in [button.label for button in app.button]
	assert next(item for item in app.selectbox if item.label == "Resident").value == "Ben"
	next(item for item in app.selectbox if item.label == "Availability type").select("prefer_off")
	app.run(timeout=5)
	assert "Save changes" in [button.label for button in app.button]
	assert next(item for item in app.selectbox if item.label == "Availability type").value == "prefer_off"
	next(item for item in app.selectbox if item.label == "Priority").select("soft")
	app.run(timeout=5)
	assert "Save changes" in [button.label for button in app.button]
	next(item for item in app.text_input if item.label == "Description").set_value("Updated reason")
	app.run(timeout=5)
	assert "Save changes" in [button.label for button in app.button]
	next(button for button in app.button if button.label == "Save changes").click()
	app.run(timeout=5)

	requests = get_schedule_requests_for_editor(period_id)
	assert len(requests) == 1
	assert requests.iloc[0]["resident"] == "Ben"
	assert requests.iloc[0]["request_type"] == "prefer_off"
	assert requests.iloc[0]["priority"] == "soft"
	assert requests.iloc[0]["reason"] == "Updated reason"


def test_availability_page_renders_dated_and_recurring_workflows(isolated_db):
	app = AppTest.from_file(str(ROOT / "pages/2_Availability_and_Preferences.py"))
	app.session_state[AUTH_SESSION_KEY] = authenticated_session()
	app.run(timeout=5)

	assert not app.exception
	assert [tab.label for tab in app.tabs] == ["Dated", "Recurring"]
	assert app.session_state["availability_preference_tabs"] == "Dated"
	assert "Add recurring preference" in [button.label for button in app.button]
	assert "Description" in [item.label for item in app.text_input]
	assert "Reason" not in [item.label for item in app.text_input]


def test_availability_page_adds_recurring_preference(isolated_db):
	app = AppTest.from_file(str(ROOT / "pages/2_Availability_and_Preferences.py"))
	app.session_state[AUTH_SESSION_KEY] = authenticated_session()
	app.run(timeout=5)

	resident_inputs = [item for item in app.selectbox if item.label == "Resident"]
	assert len(resident_inputs) == 2
	resident_inputs[1].select("Ben")
	next(item for item in app.selectbox if item.label == "Preference type").select("prefer_work")
	next(item for item in app.selectbox if item.label == "Weekday").select("Wednesday")
	next(button for button in app.button if button.label == "Add recurring preference").click()
	app.run(timeout=5)

	preferences = get_recurring_preferences_for_editor()
	assert not app.exception
	assert len(preferences) == 1
	assert preferences.iloc[0]["resident"] == "Ben"
	assert preferences.iloc[0]["request_type"] == "prefer_work"
	assert int(preferences.iloc[0]["weekday"]) == 2
	assert preferences.iloc[0]["priority"] == "soft"


def test_recurring_edit_mode_persists_across_field_reruns(isolated_db):
	create_recurring_preference(1, "prefer_off", 0, date.today(), None, "Original")
	app = AppTest.from_file(str(ROOT / "pages/2_Availability_and_Preferences.py"))
	app.session_state[AUTH_SESSION_KEY] = authenticated_session()
	app.run(timeout=5)

	recurring_edit_buttons = [button for button in app.button if button.label == "Edit"]
	assert recurring_edit_buttons
	recurring_edit_buttons[-1].click()
	app.run(timeout=5)

	next(item for item in app.selectbox if item.label == "Preference type").select("prefer_work")
	app.run(timeout=5)

	assert "Save changes" in [button.label for button in app.button]
	assert next(item for item in app.selectbox if item.label == "Preference type").value == "prefer_work"


def test_availability_page_cancel_exits_edit_mode(isolated_db):
	period_id = get_or_create_schedule_period(date.today().year, date.today().month, required_count=1)
	create_schedule_request(1, date(date.today().year, date.today().month, 10), date(date.today().year, date.today().month, 10), "vacation", "hard", "Original")
	app = AppTest.from_file(str(ROOT / "pages/2_Availability_and_Preferences.py"))
	app.session_state[AUTH_SESSION_KEY] = authenticated_session()
	app.run(timeout=5)
	next(button for button in app.button if button.label == "Edit").click()
	app.run(timeout=5)
	next(button for button in app.button if button.label == "Cancel").click()
	app.run(timeout=5)

	assert not app.exception
	assert "Add availability or preference" in [button.label for button in app.button]


def test_scheduling_rules_page_edits_existing_rule(isolated_db):
	period_id = get_or_create_schedule_period(date.today().year, date.today().month, required_count=1)
	create_schedule_rule(period_id, 2, "weekday_count", weekday=4, target_count=1, priority="hard", reason="Original")
	app = AppTest.from_file(str(ROOT / "pages/3_Scheduling_Rules.py"))
	app.session_state[AUTH_SESSION_KEY] = authenticated_session()
	app.run(timeout=5)

	assert not app.exception
	assert "Edit" in [button.label for button in app.button]
	next(button for button in app.button if button.label == "Edit").click()
	app.run(timeout=5)

	assert not app.exception
	assert next(item for item in app.selectbox if item.label == "Rule type").value == "Weekday count"
	assert next(item for item in app.selectbox if item.label == "Resident").value == "Ben"
	next(item for item in app.selectbox if item.label == "Rule type").select("Away rotation")
	app.run(timeout=5)
	next(button for button in app.button if button.label == "Save changes").click()
	app.run(timeout=5)

	rules = get_schedule_rules(period_id)
	assert len(rules) == 1
	assert rules.iloc[0]["rule_type"] == "away_rotation"
	assert int(rules.iloc[0]["resident_id"]) == 2


def test_scheduling_rules_page_cancel_exits_edit_mode(isolated_db):
	period_id = get_or_create_schedule_period(date.today().year, date.today().month, required_count=1)
	create_schedule_rule(period_id, 1, "weekday_count", weekday=4, target_count=1, priority="hard", reason="Original")
	app = AppTest.from_file(str(ROOT / "pages/3_Scheduling_Rules.py"))
	app.session_state[AUTH_SESSION_KEY] = authenticated_session()
	app.run(timeout=5)
	next(button for button in app.button if button.label == "Edit").click()
	app.run(timeout=5)
	next(button for button in app.button if button.label == "Cancel").click()
	app.run(timeout=5)

	assert not app.exception
	assert "Add rule" in [button.label for button in app.button]
