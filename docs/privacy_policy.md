# Privacy Policy

Effective date: July 3, 2026

Residency Scheduler is an internal scheduling application for creating, reviewing, and publishing residency call schedules. This policy explains what information the app collects, how it is used, and how users can request access or deletion.

## Information We Collect

The app may collect and store:

- Google account information used for sign-in, including name, email address, Google account identifier, profile image URL, OAuth scopes, and OAuth tokens.
- Resident roster information entered by users, including resident names, email addresses, PGY level, shift limits, active status, and calendar display color.
- Scheduling information entered by users, including availability, preferences, scheduling rules, generated assignments, manual edits, solver run metadata, and Google Calendar event IDs created by the app.
- Application settings such as selected calendar month, Google Calendar selection, and deployment/database settings.

The app does not intentionally collect patient information and should not be used to store patient health information.

## How We Use Information

Information is used to:

- Authenticate users with Google.
- Generate and review resident call schedules.
- Store schedule data in the configured database.
- Publish generated call schedules to a selected Google Calendar.
- Add assigned residents as attendees on Google Calendar events when their email address is present.
- Maintain audit and troubleshooting data such as solver run status and app-generated Google Calendar event IDs.

## Google User Data

The app uses Google OAuth for sign-in and Google Calendar publishing. Google user data is used only to provide app functionality requested by the signed-in user.

The app may request these Google scopes:

- `openid`
- `email`
- `profile`
- `https://www.googleapis.com/auth/calendar.calendarlist.readonly`
- `https://www.googleapis.com/auth/calendar.events`

Calendar access is used to list writable calendars selected by the signed-in user and to create, update, or delete Residency Scheduler events in the selected calendar. The app does not sell Google user data and does not use Google user data for advertising.

The app's use and transfer of information received from Google APIs adheres to the Google API Services User Data Policy, including the Limited Use requirements.

## Data Sharing

The app shares data only as needed to provide scheduling functionality:

- With Google Calendar, when a user publishes a schedule or when a resident email is added as an event attendee.
- With the configured database provider for durable app storage.
- With hosting infrastructure used to run the app.

The app does not sell personal information.

## Data Storage and Security

Production data is stored in the configured Postgres database. OAuth token payloads are encrypted before storage when a token encryption secret is configured. Local development files and credentials are excluded from git through `.gitignore`.

No system can guarantee perfect security. Users should avoid entering patient information, passwords, or other unrelated sensitive information into free-text fields.

## Data Retention and Deletion

Schedule and user records are retained while the app remains in use unless deleted by an administrator or removed during database maintenance. Users may request deletion of their stored user profile and OAuth token data. Schedule records may be retained when needed for business, audit, operational, or continuity purposes.

## User Choices

Users can revoke Google access from their Google Account permissions page. Revoking access may prevent the app from publishing to Google Calendar until the user signs in again and grants the required permissions.

## Contact

For privacy questions, access requests, or deletion requests, contact the application administrator for this deployment.

## Changes

This policy may be updated as the app changes. Material changes should be reviewed before deploying a new production version.
