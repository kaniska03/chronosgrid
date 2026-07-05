# Deploying ChronosGrid

Live setup: **backend on Render (free)** + **frontend on GitHub Pages (via GitHub Actions)**.

- Frontend live URL: https://renukaramesh2327.github.io/chronosgrid/
- Backend live URL: shown by Render after deploy (e.g. `https://chronosgrid-api.onrender.com`)
- Demo login: `demo@chronosgrid.dev` / `Demo@1234`

---

## Step 0 — One-time setup

Open **PowerShell** and check git is installed:

```powershell
git --version
```

If not installed: `winget install --id Git.Git -e`, then close and reopen PowerShell.

If you've never used git on this PC:

```powershell
git config --global user.name "Renuka Ramesh"
git config --global user.email "renukaramesh.2306@gmail.com"
```

## Step 1 — Create the GitHub repository

1. Go to https://github.com/new (log in as **renukaramesh2327**).
2. Repository name: **chronosgrid**
3. Visibility: **Public** (required for free GitHub Pages).
4. Do **NOT** tick "Add a README" or any other initialization option.
5. Click **Create repository**.

## Step 2 — Push the code

In PowerShell:

```powershell
cd "C:\Users\renuk\OneDrive\Desktop\Intern Assignment Distributed Job Scheduler\chronosgrid"
git init
git add .
git commit -m "ChronosGrid: distributed job scheduler with deployment setup"
git branch -M main
git remote add origin https://github.com/renukaramesh2327/chronosgrid.git
git push -u origin main
```

A browser window opens for GitHub sign-in on the first push — approve it.

## Step 3 — Enable GitHub Pages

1. On GitHub: repo → **Settings** → **Pages**.
2. Under **Build and deployment** → **Source**, choose **GitHub Actions**.
3. Go to the **Actions** tab. The "Deploy frontend to GitHub Pages" workflow runs on every push. If the first run failed (Pages wasn't enabled yet), click it → **Re-run all jobs**.

When it finishes, the frontend is live at:
**https://renukaramesh2327.github.io/chronosgrid/**

## Step 4 — Deploy the backend on Render

1. Go to https://render.com → **Sign up with GitHub**.
2. Click **New +** → **Blueprint**.
3. Select the **chronosgrid** repo (grant access if prompted) → **Apply**.
   Render reads `render.yaml` and builds the backend Docker image (5–10 min).
4. When live, open the service and copy its URL, e.g. `https://chronosgrid-api.onrender.com`.
5. Verify: open `<render-url>/api/v1/ready` in a browser — should respond OK.

## Step 5 — Point the frontend at the backend

Only needed if the Render URL is **not** exactly `https://chronosgrid-api.onrender.com` (Render adds a suffix if the name is taken) — but doing it always is safest:

1. GitHub repo → **Settings** → **Secrets and variables** → **Actions** → **Variables** tab.
2. **New repository variable**: Name `RENDER_API_URL`, Value = your Render URL (no trailing slash, no `/api/v1`).
3. **Actions** tab → "Deploy frontend to GitHub Pages" → **Run workflow**.

## Step 6 — Test

1. Open https://renukaramesh2327.github.io/chronosgrid/
2. Log in with `demo@chronosgrid.dev` / `Demo@1234`.

## Notes & limitations (free tier)

- **Cold start:** Render free services sleep after 15 min idle; the first request takes ~1 min. Open the backend `/api/v1/ready` URL first to wake it before a demo.
- **Data resets:** the backend uses SQLite on ephemeral disk; data resets when Render restarts the service. Demo data is reseeded automatically (`SEED_DEMO_DATA=1`).
- **Architecture on Render:** one container runs migrations, the API with the embedded scheduler, and one worker (`backend/entrypoint-render.sh`). Locally, `docker compose up` still runs the full multi-container setup with Postgres and Redis.
- **Updating the site:** just `git add . && git commit -m "..." && git push` — Actions redeploys the frontend, Render redeploys the backend automatically.
