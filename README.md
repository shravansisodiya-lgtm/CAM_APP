# Camera Monitor Dashboard

This project contains TWO front-ends that share the same database and monitor code:
- `streamlit_app.py`  - **Streamlit version**
- `app.py`            - Flask version (Docker / AWS / Oracle, see below)

## Streamlit version
Run locally:
```
pip install -r requirements.txt
streamlit run streamlit_app.py        # http://localhost:8501
```
Deploy on Streamlit Community Cloud (free):
1. Upload this folder to GitHub (private repo is fine).
2. share.streamlit.io > Create app > pick the repo, branch `main`, main file `streamlit_app.py`.
3. Advanced settings > **Secrets**: paste the contents of `.streamlit/secrets.toml.example`, filled in.
4. Deploy, then log in with ADMIN_USERNAME / ADMIN_PASSWORD.

**Streamlit Cloud limits - read before relying on it for alerts**
- The app goes to sleep when nobody has it open, and the monitor thread sleeps with it, so cameras are
  not checked and no alerts are sent while it sleeps. Opening the page wakes it.
- The disk is temporary: the database is rebuilt from your Secrets on every restart (admin, webhooks,
  cameras). Alert history and edits made inside the app are lost then - change the Secrets instead.
- Ping is usually blocked, so port checks are used (TCP_PORTS must be open on the camera/NVR).
- You can log in again after a browser refresh (sessions are not kept across refreshes).
For 24/7 alerts use the Flask version on Oracle/AWS, or run `streamlit run` on any always-on server.

---

Flask app that checks cameras/NVRs, shows live status, and sends Google Chat alerts.
Cameras must be reachable from the internet by **public IP**.

## How cameras are checked
- **ICMP ping** when the host allows it (AWS, Oracle, VPS, Docker on your PC).
- **TCP port check** automatically when the host blocks ping (some free hosts). A camera counts as
  online if any of `TCP_PORTS` (default 80,443,554,8000,37777) accepts a connection, so make sure
  at least one of those ports is reachable from the internet - or set `TCP_PORTS` to yours.
- Open `https://<your-app>/health` to see which mode is active. Force one with `CHECK_MODE=icmp|tcp`.

## Environment variables
| Name | Purpose |
|---|---|
| SECRET_KEY | Session signing key (required in production) |
| ADMIN_USERNAME, ADMIN_PASSWORD | Admin account created on first boot (password 6+ chars) |
| ORG_NAME | Dashboard title |
| WEBHOOK_URL_HIGH / MEDIUM / LOW, ADMIN_WEBHOOK_URL | Google Chat webhooks |
| CAMERAS_DATA | `name, ip, branch, priority` lines, separated by new lines or `;` |
| DATA_DIR | Folder for database + log (default: app folder; Docker uses /data) |
| COOKIE_SECURE | `1` when served over HTTPS |
| CHECK_MODE, TCP_PORTS | See above |
| TZ | Timezone, e.g. Asia/Kolkata |

The ADMIN_*, ORG_NAME, WEBHOOK_* and CAMERAS_DATA variables only fill in what is **empty**. On a host
with a real disk this runs once; edits you make in the web UI afterwards are kept.

## FREE option 1: Render (no server to manage)
Limits of Render's free plan: it sleeps after 15 min without visits, and its disk is wiped on every
restart - so this app rebuilds itself from the variables above, and you keep it awake with a free pinger.
1. Upload this folder to a **private** GitHub repo.
2. render.com > New > Blueprint > pick the repo (uses `render.yaml`). Fill the variables it asks for.
   Paste all cameras into `CAMERAS_DATA`.
3. Open the app URL, log in with ADMIN_USERNAME/ADMIN_PASSWORD. Check `/health`.
4. Keep it awake: create a free monitor at uptimerobot.com (or cron-job.org) that opens
   `https://<your-app>.onrender.com/health` every 5 minutes. Without it, monitoring pauses while asleep.
Things lost on each restart: alert history and edits made in the web UI (change the env vars instead).

## FREE option 2: AWS free plan (best match, real disk, ping works)
New AWS accounts get free credits for 6 months. On an EC2 t3.micro:
1. Ubuntu 24.04, region ap-south-1, security group 22 (your IP) / 80 / 443, attach an Elastic IP.
2. `sudo apt update && sudo apt install -y docker.io docker-compose-v2 git && sudo usermod -aG docker ubuntu` (re-login)
3. `git clone <repo> && cd camera_monitor_app && cp .env.example .env` and edit `.env`
4. `docker compose up -d --build` -> open `http://<elastic-ip>`
5. Before the 6 months end, stop the instance or move to Oracle (below).

## FREE option 3: Oracle Cloud "Always Free" VM
Free forever, always on, real disk. Create an Always Free VM, then follow the AWS steps 2-4.

## Not possible: Streamlit Cloud
It only runs Streamlit apps (this is Flask), has no permanent disk and no ping.

## Run locally
```
python -m venv venv && venv\Scripts\activate      (Linux/Mac: source venv/bin/activate)
pip install -r requirements.txt
python app.py        # http://localhost:5000
```
