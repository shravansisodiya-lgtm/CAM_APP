CREATE TABLE IF NOT EXISTS cameras (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT UNIQUE NOT NULL,
    ip             TEXT NOT NULL,
    branch         TEXT NOT NULL DEFAULT 'Unassigned',
    priority       TEXT NOT NULL DEFAULT 'medium' CHECK (priority IN ('high','medium','low')),
    is_online      INTEGER NOT NULL DEFAULT 0 CHECK (is_online IN (0,1)),
    offline_since  TEXT,
    alert_sent     INTEGER NOT NULL DEFAULT 0 CHECK (alert_sent IN (0,1)),
    last_checked   TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cameras_branch ON cameras(branch);
CREATE INDEX IF NOT EXISTS idx_cameras_priority ON cameras(priority);

CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    username       TEXT UNIQUE NOT NULL,
    password_hash  TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin','user')),
    phone_number   TEXT,
    allowed_branches TEXT,
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS password_resets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    method       TEXT NOT NULL CHECK (method IN ('webhook','sms_otp')),
    code_hash    TEXT NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    used         INTEGER NOT NULL DEFAULT 0 CHECK (used IN (0,1)),
    expires_at   TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_password_resets_user ON password_resets(user_id, method);

CREATE TABLE IF NOT EXISTS alerts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_name    TEXT,
    camera_ip      TEXT,
    alert_type     TEXT NOT NULL CHECK (alert_type IN ('ONLINE','OFFLINE','SYSTEM')),
    message        TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON alerts(created_at DESC);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
