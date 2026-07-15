# Streamlit Community Cloud Deployment Checklist

## Streamlit App Settings

- App URL: `https://huntingtonhealthresidencyscheduler.streamlit.app`
- Entrypoint file: `app.py`
- Python version: select `3.12` in Streamlit Community Cloud Advanced settings.
- Dependencies: use the root `requirements.txt`.
- System packages: no `packages.txt` is required for the current app.
- Secrets: paste the contents of the ignored local file `secrets/streamlit_cloud_secrets.toml` into the Streamlit Advanced settings Secrets field.

## Required Secrets

```toml
[connections.neon]
url = "postgresql+psycopg://USER:PASSWORD@HOST/DBNAME?sslmode=require"

[auth]
redirect_uri = "https://huntingtonhealthresidencyscheduler.streamlit.app/oauth2callback"
cookie_secret = "strong-random-cookie-signing-secret"
client_id = "..."
client_secret = "..."
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"

[google]
client_id = "..."
client_secret = "..."
redirect_uri = "https://huntingtonhealthresidencyscheduler.streamlit.app"
token_encryption_key = "base64-url-safe-fernet-key"
```

The deployed app can be public, but scheduler access is limited after Google sign-in to emails listed in the Residents table, plus `aaronbastian31@gmail.com`.

## Google Cloud Console

- Use a Web application OAuth client.
- Enable the Google Calendar API in the same Google Cloud project.
- OAuth consent app domain:
  - `huntingtonhealthresidencyscheduler.streamlit.app`
- OAuth consent policy links:
  - Privacy Policy: `https://github.com/ACB-prgm/ResidencyScheduler/blob/main/docs/privacy.md`
  - Terms of Service: `https://github.com/ACB-prgm/ResidencyScheduler/blob/main/docs/terms.md`
- Add authorized JavaScript origin:
  - `https://huntingtonhealthresidencyscheduler.streamlit.app`
- Add authorized redirect URIs:
  - `https://huntingtonhealthresidencyscheduler.streamlit.app/oauth2callback`
  - `https://huntingtonhealthresidencyscheduler.streamlit.app/component/streamlit_oauth.authorize_button`
- OAuth consent screen scopes:
  - `openid`
  - `email`
  - `profile`
  - `https://www.googleapis.com/auth/calendar.calendarlist.readonly`
  - `https://www.googleapis.com/auth/calendar.events`
- If the OAuth app is in Testing mode, add all intended users as test users or publish the OAuth app for broader access.

## Pre-Deploy Checks

Run these before merging deployment prep into `main`:

```bash
python -m compileall app.py pages residency_scheduler scripts tests
python -m pytest -q
git ls-files .streamlit/secrets.toml secrets NeonPostgres NeonPostgres.txt data '*.sqlite' '*.db'
```

The final command should print no real secrets or local database files.

## Post-Deploy Smoke Test

- Open `https://huntingtonhealthresidencyscheduler.streamlit.app`.
- Confirm the logo appears before Google sign-in.
- Sign in with Google.
- Confirm Home, Residents, Availability and Preferences, Scheduling Rules, and Generate Schedule load.
- Generate or open a schedule and confirm Google Calendar publishing can list writable calendars.
- Publish to a test calendar and confirm only Residency Scheduler events for the selected month are wiped/reloaded.
