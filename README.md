# ConsMan

Education consultancy CRM for The Blessing Edu, built from [PRD v1.5](docs/ConsMan_PRD_v1.5.pdf) using Next.js, TypeScript, Tailwind, Django and DRF.

Phase 1 is implemented and locally verified: staff CRM, permissions/MFA, Person 360, work queues, duplicate review/merge, intake/OTP, QR Studio, imports/exports, settings, reports and audit history. See [build status](docs/BUILD_STATUS.md) for acceptance evidence and production prerequisites.

## Local setup (PowerShell)

Requires Node.js 22+ and Python 3.13+. SQLite needs no database server; PostgreSQL uses `DATABASE_URL`.

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
backend/.venv/Scripts/python.exe backend/manage.py migrate
backend/.venv/Scripts/python.exe backend/manage.py seed_defaults
$env:DEMO_PASSWORD = 'choose-a-local-password-at-least-12-characters'
backend/.venv/Scripts/python.exe backend/manage.py seed_demo
backend/.venv/Scripts/python.exe backend/manage.py runserver 127.0.0.1:8000
```

In a second terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Open [the local workspace](http://127.0.0.1:3000/). Demo usernames: `manager`, `counselor`, `counselor2`, `admin`, `docs`, `management`, `finance`, using your chosen `DEMO_PASSWORD`. ADMIN/FINANCE enroll an authenticator on first sign-in. Records are fictional. Re-seeding does not reset existing passwords or duplicate demo records; never seed production.

Frontend `/api` requests proxy to Django using `API_ORIGIN`, default `http://127.0.0.1:8000`. The backend reads shell environment variables and does **not** automatically load `.env` files. Environment templates are in each app.

QR Studio creates `/r/{code}` links. Without a provider, details are saved separately from Persons and managers can verify them by call. Connect WhatsApp when ready using [Gateway setup](docs/GATEWAY_SETUP.md).

## Checks

```powershell
backend/.venv/Scripts/python.exe backend/manage.py check
backend/.venv/Scripts/python.exe backend/manage.py makemigrations --check --dry-run
backend/.venv/Scripts/python.exe backend/manage.py test crm intake
backend/.venv/Scripts/python.exe backend/manage.py spectacular --validate --fail-on-warn --file tmp/openapi.yaml
cd frontend
npm run generate:api  # Requires the backend on port 8000
npm run typecheck
npm run build
```

API health: `/api/v1/health/`; session: `/api/v1/auth/session/`; schema: `/api/schema/`; Swagger: `/api/docs/`. Staff APIs require a session and unsafe methods require CSRF. Public registration uses CAPTCHA, limits and resume-token authentication.

## Deployment and operations

Follow [the deployment runbook](docs/DEPLOYMENT.md). Live Gateway/SMS, production PostgreSQL/private storage/Turnstile, real-data migration, backup restoration and staff UAT remain activation requirements. Local tests do not establish production readiness.

Administrator password resets are available in Settings. Self-service “Forgot password” is excluded from scope; administrators handle password resets. Later admissions/visa/finance/student-portal phases are outside this build.
