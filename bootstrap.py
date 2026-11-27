"""
First-boot setup from environment variables.

Free hosts (Render free, etc.) wipe the disk on every restart, so the app
re-creates its starting configuration from env vars whenever it finds an
empty database. On a host with a real disk (AWS, VPS) this only runs the
very first time, and anything you later change in the web UI is kept.

  ADMIN_USERNAME / ADMIN_PASSWORD   create the admin if no users exist
  ORG_NAME                          dashboard title
  WEBHOOK_URL_HIGH / _MEDIUM / _LOW, ADMIN_WEBHOOK_URL   Google Chat webhooks
  CAMERAS_DATA                      lines of "name, ip, branch, priority"
                                    separated by new lines or semicolons
"""
import os
import re
import ipaddress

from werkzeug.security import generate_password_hash

import db

PRIORITIES = ("high", "medium", "low")


def _valid_ip(ip):
    try:
        return ipaddress.IPv4Address(ip) is not None
    except ValueError:
        return False


def _parse_cameras(text):
    rows, seen = [], set()
    for line in re.split(r"[\n;]+", text or ""):
        parts = [p.strip() for p in re.split(r"\t|,", line.strip())]
        if len(parts) < 2 or not parts[0] or not _valid_ip(parts[1]) or parts[0] in seen:
            continue
        seen.add(parts[0])
        branch = parts[2] if len(parts) > 2 and parts[2] else "Unassigned"
        priority = parts[3].lower() if len(parts) > 3 and parts[3].lower() in PRIORITIES else "medium"
        rows.append((parts[0], parts[1], branch, priority))
    return rows


def seed_from_env():
    env = os.environ
    user, pwd = env.get("ADMIN_USERNAME", "").strip(), env.get("ADMIN_PASSWORD", "")
    if user and len(pwd) >= 6 and db.user_count() == 0:
        db.create_user(user, generate_password_hash(pwd), role="admin")

    if env.get("ORG_NAME") and db.get_setting("org_name", "Camera Monitor") == "Camera Monitor":
        db.set_setting("org_name", env["ORG_NAME"].strip())
    for key in ("webhook_url_high", "webhook_url_medium", "webhook_url_low", "admin_webhook_url"):
        value = env.get(key.upper(), "").strip()
        if value and not db.get_setting(key, "").strip():
            db.set_setting(key, value)

    if env.get("CAMERAS_DATA") and not db.get_all_cameras():
        rows = _parse_cameras(env["CAMERAS_DATA"])
        if rows:
            db.bulk_add_cameras(rows)
