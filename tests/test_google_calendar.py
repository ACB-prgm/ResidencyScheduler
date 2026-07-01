from __future__ import annotations

import pandas as pd
import pytest

from residency_scheduler.calendar import google
from residency_scheduler.db import init_db
from residency_scheduler.repository import (
	get_assignments,
	get_or_create_schedule_period,
	save_assignments,
	save_residents,
	update_assignment_google_event_id,
)


def auth_session() -> dict:
	return {
		"token": {
			"token": "access-token",
			"refresh_token": "refresh-token",
			"client_id": "client",
			"client_secret": "secret",
			"token_uri": "https://oauth2.googleapis.com/token",
			"scopes": [
				"openid",
				"email",
				"profile",
				"https://www.googleapis.com/auth/calendar.calendarlist.readonly",
				"https://www.googleapis.com/auth/calendar.events",
			],
		},
		"scopes": [
			"openid",
			"email",
			"profile",
			"https://www.googleapis.com/auth/calendar.calendarlist.readonly",
			"https://www.googleapis.com/auth/calendar.events",
		],
	}


def test_has_calendar_scopes_requires_calendar_scopes():
	assert google.has_calendar_scopes(auth_session())
	assert not google.has_calendar_scopes({"scopes": ["openid", "email", "profile"]})


def test_list_writable_calendars_filters_owner_and_writer():
	service = FakeCalendarService(
		calendar_pages=[
			{
				"items": [
					{"id": "primary", "summary": "Primary", "primary": True, "accessRole": "owner"},
					{"id": "team", "summary": "Team", "accessRole": "writer"},
					{"id": "read-only", "summary": "Read Only", "accessRole": "reader"},
				]
			}
		]
	)

	calendars = google.list_writable_calendars(auth_session(), service=service)

	assert calendars == [
		{"id": "primary", "summary": "Primary", "primary": True, "accessRole": "owner"},
		{"id": "team", "summary": "Team", "primary": False, "accessRole": "writer"},
	]


def test_build_assignment_event_contains_schedule_metadata():
	assignment = pd.DataFrame(
		[
			{
				"id": 42,
				"work_date": "2026-08-15",
				"resident_name": "Ada Smith",
			}
		]
	).itertuples().__next__()

	event = google.build_assignment_event({"id": 7, "year": 2026, "month": 8}, assignment)

	assert event["summary"] == "Ada Smith"
	assert event["start"] == {"date": "2026-08-15"}
	assert event["end"] == {"date": "2026-08-16"}
	assert "dateTime" not in event["start"]
	assert "dateTime" not in event["end"]
	assert "DO NOT EDIT OR DELETE ANY OF THIS INFORMATION" in event["description"]
	assert "Schedule Month: 2026-08" in event["description"]
	assert event["extendedProperties"]["private"] == {
		"rs_app": "residency_scheduler",
		"rs_period_id": "7",
		"rs_year_month": "2026-08",
		"rs_assignment_id": "42",
	}


def test_publish_period_wipes_existing_events_and_stores_new_event_ids(isolated_google_db):
	period_id = get_or_create_schedule_period(2026, 8, required_count=1)
	save_assignments(
		period_id,
		[
			{"work_date": "2026-08-01", "resident_id": 1},
			{"work_date": "2026-08-02", "resident_id": 2},
		],
	)
	assignments = get_assignments(period_id)
	for row in assignments.itertuples():
		update_assignment_google_event_id(int(row.id), f"old-{row.id}")

	service = FakeCalendarService(event_pages=[{"items": [{"id": "old-event"}]}])

	result = google.publish_period_to_calendar(period_id, "calendar@example.org", auth_session(), service=service)

	updated = get_assignments(period_id).sort_values("work_date")
	assert result.deleted_count == 1
	assert result.inserted_count == 2
	assert service.deleted == [("calendar@example.org", "old-event")]
	assert updated["google_event_id"].tolist() == ["created-1", "created-2"]
	assert len(service.inserted) == 2
	assert service.event_list_calls[0]["privateExtendedProperty"] == [
		"rs_app=residency_scheduler",
		f"rs_period_id={period_id}",
		"rs_year_month=2026-08",
	]


def test_wipe_period_deletes_only_scheduler_events_and_clears_event_ids(isolated_google_db):
	period_id = get_or_create_schedule_period(2026, 8, required_count=1)
	save_assignments(
		period_id,
		[
			{"work_date": "2026-08-01", "resident_id": 1},
			{"work_date": "2026-08-02", "resident_id": 2},
		],
	)
	for row in get_assignments(period_id).itertuples():
		update_assignment_google_event_id(int(row.id), f"old-{row.id}")
	service = FakeCalendarService(event_pages=[{"items": [{"id": "old-1"}, {"id": "old-2"}]}])

	result = google.wipe_period_from_calendar(period_id, "calendar@example.org", auth_session(), service=service)

	updated = get_assignments(period_id).sort_values("work_date")
	assert result.deleted_count == 2
	assert result.inserted_count == 0
	assert service.deleted == [("calendar@example.org", "old-1"), ("calendar@example.org", "old-2")]
	assert updated["google_event_id"].fillna("").tolist() == ["", ""]
	assert service.event_list_calls[0]["privateExtendedProperty"] == [
		"rs_app=residency_scheduler",
		f"rs_period_id={period_id}",
		"rs_year_month=2026-08",
	]


def test_find_existing_period_events_returns_matching_ids(isolated_google_db):
	period_id = get_or_create_schedule_period(2026, 8, required_count=1)
	service = FakeCalendarService(event_pages=[{"items": [{"id": "one"}, {"id": "two"}]}])

	event_ids = google.find_existing_period_events(period_id, "calendar@example.org", auth_session(), service=service)

	assert event_ids == ["one", "two"]


def test_publish_period_batches_deletes_and_inserts_when_available(isolated_google_db):
	period_id = get_or_create_schedule_period(2026, 8, required_count=1)
	save_assignments(
		period_id,
		[
			{"work_date": "2026-08-01", "resident_id": 1},
			{"work_date": "2026-08-02", "resident_id": 2},
		],
	)
	service = FakeBatchCalendarService(event_pages=[{"items": [{"id": "old-1"}, {"id": "old-2"}]}])

	result = google.publish_period_to_calendar(period_id, "calendar@example.org", auth_session(), service=service)

	updated = get_assignments(period_id).sort_values("work_date")
	assert result.deleted_count == 2
	assert result.inserted_count == 2
	assert service.batch_execute_count == 2
	assert service.deleted == [("calendar@example.org", "old-1"), ("calendar@example.org", "old-2")]
	assert updated["google_event_id"].tolist() == ["batch-created-1", "batch-created-2"]


def test_publish_period_uses_provided_existing_event_ids_without_listing(isolated_google_db):
	period_id = get_or_create_schedule_period(2026, 8, required_count=1)
	save_assignments(
		period_id,
		[
			{"work_date": "2026-08-01", "resident_id": 1},
			{"work_date": "2026-08-02", "resident_id": 2},
		],
	)
	service = FakeCalendarService()

	result = google.publish_period_to_calendar(
		period_id,
		"calendar@example.org",
		auth_session(),
		service=service,
		existing_event_ids=["old-1", "old-2"],
	)

	assert result.deleted_count == 2
	assert result.inserted_count == 2
	assert service.event_list_calls == []
	assert service.deleted == [("calendar@example.org", "old-1"), ("calendar@example.org", "old-2")]


class FakeCalendarService:
	def __init__(self, calendar_pages=None, event_pages=None):
		self.calendar_pages = calendar_pages or []
		self.event_pages = event_pages or []
		self.deleted = []
		self.inserted = []
		self.event_list_calls = []

	def calendarList(self):
		return FakeCalendarListResource(self)

	def events(self):
		return FakeEventsResource(self)


class FakeBatchCalendarService(FakeCalendarService):
	def __init__(self, calendar_pages=None, event_pages=None):
		super().__init__(calendar_pages=calendar_pages, event_pages=event_pages)
		self.batch_execute_count = 0

	def new_batch_http_request(self, callback):
		return FakeBatchRequest(self, callback)


class FakeCalendarListResource:
	def __init__(self, service):
		self.service = service

	def list(self, **_kwargs):
		return FakeRequest(self.service.calendar_pages.pop(0) if self.service.calendar_pages else {"items": []})


class FakeEventsResource:
	def __init__(self, service):
		self.service = service

	def list(self, **kwargs):
		self.service.event_list_calls.append(kwargs)
		return FakeRequest(self.service.event_pages.pop(0) if self.service.event_pages else {"items": []})

	def delete(self, calendarId, eventId):
		return FakeGoogleRequest(self.service, "delete", calendarId=calendarId, eventId=eventId)

	def insert(self, calendarId, body):
		return FakeGoogleRequest(self.service, "insert", calendarId=calendarId, body=body)


class FakeRequest:
	def __init__(self, response):
		self.response = response

	def execute(self):
		return self.response


class FakeGoogleRequest:
	def __init__(self, service, action, **kwargs):
		self.service = service
		self.action = action
		self.kwargs = kwargs

	def execute(self):
		if self.action == "delete":
			self.service.deleted.append((self.kwargs["calendarId"], self.kwargs["eventId"]))
			return {}
		if self.action == "insert":
			self.service.inserted.append((self.kwargs["calendarId"], self.kwargs["body"]))
			return {"id": f"created-{len(self.service.inserted)}"}
		return {}

	def batch_execute(self):
		if self.action == "delete":
			self.service.deleted.append((self.kwargs["calendarId"], self.kwargs["eventId"]))
			return {}
		if self.action == "insert":
			self.service.inserted.append((self.kwargs["calendarId"], self.kwargs["body"]))
			return {"id": f"batch-created-{len(self.service.inserted)}"}
		return {}


class FakeBatchRequest:
	def __init__(self, service, callback):
		self.service = service
		self.callback = callback
		self.requests = []

	def add(self, request, request_id=None, callback=None):
		self.requests.append((request, request_id, callback))

	def execute(self):
		self.service.batch_execute_count += 1
		for request, request_id, per_request_callback in self.requests:
			response = request.batch_execute()
			active_callback = per_request_callback or self.callback
			if active_callback:
				active_callback(request_id, response, None)


@pytest.fixture
def isolated_google_db(tmp_path, monkeypatch):
	monkeypatch.setenv("RESIDENCY_SCHEDULER_DB", str(tmp_path / "google.sqlite"))
	init_db()
	save_residents(
		pd.DataFrame(
			[
				{"name": "Ada", "email": "ada@example.com", "max_shifts": 10, "min_shifts": None, "weight": 1, "active": 1},
				{"name": "Ben", "email": "ben@example.com", "max_shifts": 10, "min_shifts": None, "weight": 1, "active": 1},
			]
		)
	)
