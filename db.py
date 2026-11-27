import json
import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR))
DB_PATH = DATA_DIR / "camera_monitor.db"


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def migrate():
    """Adds columns introduced after the first release (safe to run every start)."""
    with get_db() as conn:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(cameras)").fetchall()]
        if cols and "alert_priority" not in cols:
            conn.execute("ALTER TABLE cameras ADD COLUMN alert_priority TEXT")


PRIORITY_ORDER = "CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END"


# ---------- cameras ----------

def parse_branches(value):
    """users.allowed_branches -> None (all branches) or a list of branch names."""
    if value is None or value == "":
        return None
    try:
        data = json.loads(value)
    except Exception:
        return None
    return data if isinstance(data, list) else None


def _in_clause(items):
    return ",".join("?" for _ in items)


def get_all_cameras(branch=None, allowed=None):
    """allowed=None -> every branch; otherwise only cameras in those branches."""
    if allowed is not None:
        if not allowed or (branch and branch != "All" and branch not in allowed):
            return []
    with get_db() as conn:
        where, params = [], []
        if branch and branch != "All":
            where.append("branch=?"); params.append(branch)
        elif allowed is not None:
            where.append(f"branch IN ({_in_clause(allowed)})"); params.extend(allowed)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        order = (f"{PRIORITY_ORDER}, name COLLATE NOCASE" if branch and branch != "All"
                 else f"{PRIORITY_ORDER}, branch COLLATE NOCASE, name COLLATE NOCASE")
        rows = conn.execute(f"SELECT * FROM cameras {clause} ORDER BY {order}", params).fetchall()
        return [dict(r) for r in rows]


def get_camera(camera_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM cameras WHERE id=?", (camera_id,)).fetchone()
        return dict(row) if row else None


def camera_name_taken(name, exclude_id=None):
    with get_db() as conn:
        if exclude_id:
            row = conn.execute("SELECT id FROM cameras WHERE name=? AND id!=?", (name, exclude_id)).fetchone()
        else:
            row = conn.execute("SELECT id FROM cameras WHERE name=?", (name,)).fetchone()
        return row is not None


def add_camera(name, ip, branch="Unassigned", priority="medium"):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO cameras (name, ip, branch, priority, created_at) VALUES (?, ?, ?, ?, ?)",
            (name.strip(), ip.strip(), branch.strip() or "Unassigned", priority, datetime.now().isoformat()),
        )


def update_camera(camera_id, name, ip, branch, priority):
    with get_db() as conn:
        conn.execute(
            # alert_sent is cleared when priority or IP changes, so a camera that is
            # already offline gets re-evaluated against its NEW priority's threshold
            # and webhook instead of being treated as "already alerted".
            """UPDATE cameras SET name=?, ip=?, branch=?, priority=?,
               alert_sent = CASE WHEN priority != ? OR ip != ? THEN 0 ELSE alert_sent END
               WHERE id=?""",
            (name.strip(), ip.strip(), branch.strip() or "Unassigned", priority,
             priority, ip.strip(), camera_id),
        )


def delete_camera(camera_id):
    with get_db() as conn:
        conn.execute("DELETE FROM cameras WHERE id=?", (camera_id,))


def get_all_branches(allowed=None):
    if allowed is not None and not allowed:
        return []
    with get_db() as conn:
        where, params = "", []
        if allowed is not None:
            where = f"WHERE branch IN ({_in_clause(allowed)})"; params = list(allowed)
        rows = conn.execute(f"""
            SELECT branch, COUNT(*) AS total, SUM(CASE WHEN is_online THEN 1 ELSE 0 END) AS online
            FROM cameras {where} GROUP BY branch ORDER BY branch COLLATE NOCASE
        """, params).fetchall()
        return [{"branch": r["branch"], "total": r["total"], "online": r["online"] or 0,
                  "offline": r["total"] - (r["online"] or 0)} for r in rows]


def bulk_add_cameras(rows):
    """rows: list of (name, ip, branch, priority) tuples."""
    added = 0
    skipped = []
    now = datetime.now().isoformat()
    with get_db() as conn:
        for name, ip, branch, priority in rows:
            existing = conn.execute("SELECT 1 FROM cameras WHERE name=?", (name,)).fetchone()
            if existing:
                skipped.append(name)
                continue
            conn.execute(
                "INSERT INTO cameras (name, ip, branch, priority, created_at) VALUES (?, ?, ?, ?, ?)",
                (name, ip, branch or "Unassigned", priority or "medium", now),
            )
            added += 1
    return added, skipped


# ---------- users ----------

def user_count():
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]


def admin_count(exclude_id=None, active_only=True):
    """How many admins exist right now - used to block actions that would
    leave the system with zero administrators."""
    with get_db() as conn:
        query = "SELECT COUNT(*) AS c FROM users WHERE role='admin'"
        params = []
        if active_only:
            query += " AND is_active=1"
        if exclude_id:
            query += " AND id!=?"
            params.append(exclude_id)
        return conn.execute(query, params).fetchone()["c"]


def get_all_users():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, username, role, phone_number, allowed_branches, is_active, created_at FROM users "
            "ORDER BY username COLLATE NOCASE"
        ).fetchall()
        return [dict(r) for r in rows]


def get_user_by_id(user_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_username(username):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
        return dict(row) if row else None


def username_taken(username, exclude_id=None):
    with get_db() as conn:
        if exclude_id:
            row = conn.execute("SELECT id FROM users WHERE username=? AND id!=?", (username.strip(), exclude_id)).fetchone()
        else:
            row = conn.execute("SELECT id FROM users WHERE username=?", (username.strip(),)).fetchone()
        return row is not None


def _branches_to_db(allowed_branches):
    return None if allowed_branches is None else json.dumps(list(allowed_branches))


def get_allowed_branches(user_id):
    """None = all branches, else list of branch names the user may see."""
    user = get_user_by_id(user_id) if user_id else None
    if not user or user.get("role") == "admin":
        return None
    return parse_branches(user.get("allowed_branches"))


def create_user(username, password_hash, role="user", phone_number=None, allowed_branches=None):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, role, phone_number, allowed_branches, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (username.strip(), password_hash, role, (phone_number or "").strip() or None,
             _branches_to_db(allowed_branches), datetime.now().isoformat()),
        )


def update_user(user_id, username, role, phone_number, is_active, allowed_branches=None):
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET username=?, role=?, phone_number=?, is_active=?, allowed_branches=? WHERE id=?",
            (username.strip(), role, (phone_number or "").strip() or None, 1 if is_active else 0,
             _branches_to_db(allowed_branches), user_id),
        )


def update_user_password(user_id, password_hash):
    with get_db() as conn:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))


def delete_user(user_id):
    with get_db() as conn:
        conn.execute("DELETE FROM password_resets WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))


# ---------- password resets (webhook token + SMS OTP) ----------

def create_password_reset(user_id, method, code_hash, expires_at):
    with get_db() as conn:
        # A fresh request invalidates any earlier unused code of the same
        # method, so only the most recent one is ever valid.
        conn.execute(
            "UPDATE password_resets SET used=1 WHERE user_id=? AND method=? AND used=0",
            (user_id, method),
        )
        conn.execute(
            "INSERT INTO password_resets (user_id, method, code_hash, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, method, code_hash, expires_at, datetime.now().isoformat()),
        )


def get_active_resets_for_user(user_id):
    with get_db() as conn:
        now = datetime.now().isoformat()
        rows = conn.execute(
            "SELECT * FROM password_resets WHERE user_id=? AND used=0 AND expires_at > ? "
            "AND attempts < 5 ORDER BY id DESC",
            (user_id, now),
        ).fetchall()
        return [dict(r) for r in rows]


def record_reset_attempt(reset_id, success):
    with get_db() as conn:
        if success:
            conn.execute("UPDATE password_resets SET used=1 WHERE id=?", (reset_id,))
        else:
            conn.execute(
                "UPDATE password_resets SET attempts = attempts + 1, "
                "used = CASE WHEN attempts + 1 >= 5 THEN 1 ELSE used END WHERE id=?",
                (reset_id,),
            )


# ---------- alerts ----------

def add_alert(camera_name, camera_ip, alert_type, message):
    with get_db() as conn:
        conn.execute("INSERT INTO alerts (camera_name, camera_ip, alert_type, message, created_at) VALUES (?, ?, ?, ?, ?)",
                     (camera_name, camera_ip, alert_type, message, datetime.now().isoformat()))
        conn.execute("DELETE FROM alerts WHERE id NOT IN (SELECT id FROM alerts ORDER BY id DESC LIMIT 500)")


def get_recent_alerts(limit=100, allowed=None):
    if allowed is not None and not allowed:
        return []
    with get_db() as conn:
        if allowed is None:
            rows = conn.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM alerts WHERE camera_name IN "
                f"(SELECT name FROM cameras WHERE branch IN ({_in_clause(allowed)})) ORDER BY id DESC LIMIT ?",
                (*allowed, limit)).fetchall()
        return [dict(r) for r in rows]


# ---------- settings ----------

def get_setting(key, default=None):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def get_all_settings():
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


def set_setting(key, value):
    with get_db() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, str(value)))
