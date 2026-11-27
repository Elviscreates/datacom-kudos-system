# Datacom Kudos

A lightweight, local-first employee recognition demo built with FastAPI, SQLite, HTML, Tailwind utility classes, and vanilla JavaScript. SQLite initializes itself and seeds demo employees and kudos the first time the app starts. No external database service or frontend build step is required.

## Important demo authentication notice

The profile switcher is a **local demo authentication stub**, not a secure login system. Anyone who can reach the app can select the Admin profile and use moderation features. Run it on your own machine at `127.0.0.1`; do not expose it to a network or use it for real employee data. Replace profile switching with the organization’s identity provider and role-based authorization before any shared or production deployment.

## Requirements

- Python 3.10 or newer
- Internet access for the optional Tailwind CDN and Google Fonts enhancements. The app includes local CSS so the dashboard remains usable if those assets cannot load.

## Setup and run

From the project directory:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). On first run, the app creates `data/kudos.db`, creates the schema, and inserts an Admin profile, four employee profiles, and sample kudos.

If PowerShell prevents environment activation, run the environment's Python directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## Demo profiles

Use the profile selector in the dashboard to switch between:

- Morgan Admin (administrator; can hide and soft-delete kudos)
- Alex Chen
- Sam Rivera
- Jordan Lee
- Taylor Kim

The selection is stored in a signed, HTTP-only local cookie. It is intentionally not protected by a password because profile switching is for local demonstration only.

## Configuration

Set environment variables before starting the server to customize local behavior:

| Variable | Default | Description |
|---|---:|---|
| `KUDOS_DATABASE_PATH` | `data/kudos.db` | SQLite database file |
| `KUDOS_SESSION_SECRET` | Random per process | HMAC key for the demo session cookie; set a long random value to keep cookies valid across restarts |
| `KUDOS_COOLDOWN_SECONDS` | `15` | Minimum time between submissions by the same sender |
| `KUDOS_DUPLICATE_WINDOW_SECONDS` | `120` | Window for blocking the same sender/recipient/message combination |
| `KUDOS_BLOCKED_TERMS` | Built-in demo list | Comma-separated case-insensitive terms blocked by the local content filter; set an empty value to disable it |

The built-in content filter is a basic local demo safeguard, not a comprehensive moderation or safety system.

## API overview

- `GET /api/health` — local health check
- `GET /api/auth/profiles` — list demo profiles
- `POST /api/auth/switch` — select a demo profile (`{"userId":"alex"}`)
- `GET /api/auth/me` / `POST /api/auth/logout` — current demo identity / end session
- `GET /api/users?query=sa` — search active colleagues (requires a selected profile)
- `POST /api/kudos` — submit kudos (requires authentication)
- `GET /api/kudos?limit=20&cursor=...` — list visible kudos (requires authentication)
- `PATCH /api/kudos/{id}/hide` — admin-only hide; requires `{"reason":"..."}`
- `DELETE /api/kudos/{id}` — admin-only soft delete; requires `{"reason":"..."}`
- `GET /docs` — interactive OpenAPI documentation

Admin moderation records the latest action, moderator, timestamp, and required reason on the kudos row. Hiding and deleting both remove an item immediately from the public feed; delete retains the row for audit.

## Tests

Run the API and database integration tests with the standard library:

```powershell
python -m unittest discover -s tests -v
```

Tests use temporary SQLite files and do not touch the local demo database.
