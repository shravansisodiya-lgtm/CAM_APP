import os
import sqlite3
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "camera_monitor.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"
SECRET_KEY_PATH = DATA_DIR / "secret.key"

DEFAULT_SETTINGS = {
    "check_interval": "30",
    "summary_interval": "1800",
    "org_name": "Camera Monitor",
    "webhook_url_high": "",
    "webhook_url_medium": "",
    "webhook_url_low": "",
    "offline_threshold_high": "30",
    "offline_threshold_medium": "120",
    "offline_threshold_low": "300",
    # Used for password-reset notifications and admin/security messages -
    # deliberately separate from the camera-priority webhooks above, so a
    # password reset never shows up in a camera alert channel.
    "admin_webhook_url": "",
    # Optional: a real SMS provider's HTTP endpoint. Left blank, OTPs are
    # relayed through admin_webhook_url instead, so phone-based reset is
    # still testable before you've picked/paid for an SMS provider.
    "sms_gateway_webhook_url": "",
}


def _column_exists(conn, table, column):
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def _migrate_add_priority_column(conn):
    if not _column_exists(conn, "cameras", "priority"):
        conn.execute(
            "ALTER TABLE cameras ADD COLUMN priority TEXT NOT NULL DEFAULT 'medium' "
            "CHECK (priority IN ('high','medium','low'))"
        )


def _migrate_settings_to_priority_webhooks(conn):
    old_webhook = conn.execute("SELECT value FROM settings WHERE key='webhook_url'").fetchone()
    old_threshold = conn.execute("SELECT value FROM settings WHERE key='offline_threshold'").fetchone()
    if old_webhook and old_webhook[0]:
        for level in ("high", "medium", "low"):
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                         (f"webhook_url_{level}", old_webhook[0]))
    if old_threshold and old_threshold[0]:
        for level in ("high", "medium", "low"):
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                         (f"offline_threshold_{level}", old_threshold[0]))


def _migrate_add_user_columns(conn):
    """Accounts created before roles existed need role/phone_number/is_active
    added. Every existing user becomes 'admin' automatically - they already
    had full access before this upgrade, so downgrading them to 'user' here
    would silently lock them out of Settings and User Management, which
    would be a much worse surprise than starting everyone as admin and
    letting the real admin demote extra accounts afterward if needed."""
    if not _column_exists(conn, "users", "role"):
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin' "
                     "CHECK (role IN ('admin','user'))")
        conn.execute("UPDATE users SET role='admin'")
    if not _column_exists(conn, "users", "phone_number"):
        conn.execute("ALTER TABLE users ADD COLUMN phone_number TEXT")
    # NULL = access to ALL branches (so every existing user keeps what they had).
    # Otherwise a JSON list of branch names, e.g. ["Indore","Ujjain"].
    if not _column_exists(conn, "users", "allowed_branches"):
        conn.execute("ALTER TABLE users ADD COLUMN allowed_branches TEXT")
    if not _column_exists(conn, "users", "is_active"):
        conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1 "
                     "CHECK (is_active IN (0,1))")


def init():
    first_time = not DB_PATH.exists()
    conn = sqlite3.connect(DB_PATH)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())

    _migrate_add_priority_column(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cameras_priority ON cameras(priority)")
    _migrate_settings_to_priority_webhooks(conn)
    _migrate_add_user_columns(conn)

    for key, value in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))

    conn.commit()
    conn.close()
    if not SECRET_KEY_PATH.exists():
        SECRET_KEY_PATH.write_text(secrets.token_hex(32))
    print("[OK] Database created." if first_time else "[OK] Database already existed - left untouched.")


if __name__ == "__main__":
    init()
