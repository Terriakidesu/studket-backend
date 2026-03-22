# Studket Backend

> [!IMPORTANT] THIS CODEBASE WAS VIBE-CODED AND HELD TOGETHER BY DREAMS AND HOPES

## What This Is
Backend services for the Studket project.

## Quick Start
1. Ensure you have Python 3.11+ installed.
1. Create and activate a virtual environment.
1. Install dependencies from `requirements.txt`.
1. Run the server (see the section below).

## Run The Server
The FastAPI app is exposed as `app` in `app/main.py`.

### Uvicorn (dev)
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Gunicorn + Uvicorn workers (prod)
```bash
gunicorn -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:8000
```

If you need multiple workers, add `--workers 2` (or higher) to the Gunicorn command.

## Project Layout
- `app/` — application code (API routes, models, realtime, etc.)
- `README.md` — this file

## Main Libraries / Frameworks
- FastAPI (API framework, routing, WebSocket support)
- Starlette (middleware + sessions under FastAPI)
- SQLAlchemy (ORM + database access)
- Pydantic (request/response models)
- Jinja2 (server-side HTML templates via `fastapi.templating`)

## Notes
- The warning above is intentional and should remain visible.
- Expect rough edges; document changes as you discover them.
