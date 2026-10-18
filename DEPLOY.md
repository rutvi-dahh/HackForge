# Deploying Receipt Graveyard on Render (free plan, Docker)

Expected time: about 2 hours the first time. Result: a public link like `https://receipt-graveyard.onrender.com`.

## What you are copying into the repo root

| File | Purpose |
|---|---|
| `Dockerfile` | Builds the app image and installs Tesseract |
| `docker-entrypoint.sh` | Seeds the database on boot, then starts gunicorn |
| `deploy_support.py` | Turns debug off, trusts Render's proxy, secure cookies, `/healthz` |
| `render.yaml` | Optional one-click Render setup |
| `.dockerignore` | Keeps the datasets and secrets out of the image |
| `.gitignore` | Keeps `.env`, databases, and datasets out of GitHub |
| `.env.example` | Names of the environment variables |

On a Mac, dotfiles (`.gitignore`, `.dockerignore`, `.env.example`) are hidden in Finder. Press **Cmd + Shift + .** to show them. If you already have a `.gitignore` or `.env.example`, merge the lines instead of replacing the file.

## Step 1. Copy the files
Put all seven files in the same folder as `app.py`.

## Step 2. Two edits to `app.py`
Near the top, with your other imports:
```python
import os
from deploy_support import configure_for_deploy
```
At the **end of the module**, just before the `if __name__ == "__main__":` block:
```python
configure_for_deploy(app)
```
Change the last block so it never runs in debug mode:
```python
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5001)), debug=False)
```
`gunicorn app:app` needs a module-level variable called `app`. If your app is built inside a function, tell Antigravity to expose `app` at module level.

## Step 3. Test the production way locally
```bash
source .venv/bin/activate
pip install gunicorn
python seed.py
gunicorn app:app --bind 127.0.0.1:5001 --workers 1 --threads 4 --timeout 120
```
Open `http://127.0.0.1:5001/healthz` (should print `ok`), then log in as admin and as an employee.

## Step 4. Fresh-clone test (important)
This catches files that only exist on your laptop.
```bash
cd /tmp
git clone https://github.com/rutvi-dahh/HackForge.git hf-test   # after Step 5
cd hf-test
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt gunicorn
python seed.py
```
If `seed.py` fails here, it would also fail on Render. Usually the cause is a seed or cache file that is git-ignored or was never committed. Fix that first.

## Step 5. Push to GitHub safely
```bash
git status                  # check nothing huge or secret is listed
git add .
git commit -m "Add deployment files"
git push
```
Before pushing, confirm:
- `.env` is **not** listed (your Groq key lives there).
- The full datasets (`archive.zip`, `data/`, the invoice folder) are **not** listed. GitHub rejects files over 100 MB. Only the small `sample_data/` folder should be committed.
- If a key was ever committed, create a new one in the Groq console.

## Step 6. Create the Render service
**Option A: Blueprint (fastest).**
1. Sign up at render.com and connect your GitHub account.
2. **New → Blueprint**, select the repo. Render reads `render.yaml`.
3. When asked, enter `GROQ_API_KEY` and `GROQ_MODEL` (use the vision model ID shown in the Groq console).

**Option B: manual.**
1. **New → Web Service**, connect the repo.
2. Runtime: **Docker**. Plan: **Free**.
3. Environment tab, add: `SECRET_KEY` (a long random string), `GROQ_API_KEY`, `GROQ_MODEL`, `SEED_ON_BOOT=1`, `COOKIE_SECURE=1`.
4. Health check path: `/healthz`.

Click **Create**. The first build takes about 5 to 10 minutes.

## Step 7. Verify
Open the URL Render gives you and check:
- Login page loads, with no demo buttons
- Admin login works and the review queue shows seeded claims
- Open a receipt in the popup, approve one claim, and see the confirmation and payment placeholder
- Employee login works and the Help page shows 1223334444
- Upload one receipt live (needs a valid `GROQ_API_KEY` and `GROQ_MODEL`)
- In Render's **Logs**, look for any `seed.py` warning or `TesseractNotFoundError`

## Free-plan behaviour to plan around
- **Cold start:** the service sleeps after 15 minutes without traffic, and the first request can take about a minute. Open the link a few minutes before you present, and click through once.
- **Data resets:** the free plan's disk is temporary. Every restart, redeploy, or wake-up starts from a fresh database and re-seeds it. Claims uploaded during the demo will not survive. A paid plan with a persistent disk removes this, and then set `SEED_ON_BOOT=0` after the first boot.
- Render advises against free instances for real production use.

## Backup plan: run from your laptop with a tunnel
Use this if Render misbehaves on demo day (about 15 minutes).
```bash
brew install cloudflared
python app.py                                   # terminal 1
cloudflared tunnel --url http://localhost:5001  # terminal 2, prints a public https link
```
The link works only while your laptop is on and the commands are running. Check Cloudflare's docs if the command has changed.

## Troubleshooting
| Symptom | Likely cause and fix |
|---|---|
| Build fails during `pip install` | Python version mismatch. Change the first line of the Dockerfile to `python:3.9-slim` (your local version) |
| `TesseractNotFoundError` | The apt line in the Dockerfile did not run. Check build logs |
| "No open ports detected" | App is not binding to `$PORT`. Keep the gunicorn command from `docker-entrypoint.sh` |
| `ModuleNotFoundError` | Package missing from `requirements.txt`. Add it and redeploy |
| Login loops back to the login page | Secure cookie without proxy headers. Confirm `configure_for_deploy(app)` is called and `COOKIE_SECURE=1` |
| `database is locked` | More than one gunicorn worker. Keep `--workers 1` |
| Seeded claims missing | `seed.py` failed. Read the log line starting with `WARNING` |
| Live upload ends as Info needed | Missing or wrong `GROQ_API_KEY` or `GROQ_MODEL` |
| Page is blank for about a minute | The free instance is waking up |
