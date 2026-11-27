"""
Business logic: pings every camera on a timer, decides who's online or
offline, and sends webhook alerts - routed to a different Google Chat
webhook, on a different timer, depending on that camera's priority
(high/medium/low). Knows nothing about Flask or HTML - it only ever talks
to db.py.
"""
import os
import time
import platform
import subprocess
import threading
import socket
import math
import requests
from datetime import datetime
from pathlib import Path
from queue import Queue
from concurrent.futures import ThreadPoolExecutor, as_completed

import db

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = Path(os.environ.get("DATA_DIR", BASE_DIR)) / "monitor.log"
PING_TIMEOUT = 2
NETWORK_TIMEOUT = 5
MAX_WORKERS = 8
PRIORITY_LEVELS = ("high", "medium", "low")

chat_queue = Queue(maxsize=200)
shutdown_event = threading.Event()


def log_message(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[monitor] {message}", flush=True)   # also visible in the host's log viewer
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass


def recent_log(lines=60, only_important=True):
    """Last log lines, newest last. only_important hides the noisy per-camera DOWN lines."""
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            rows = f.read().splitlines()
    except Exception:
        return []
    if only_important:
        rows = [r for r in rows if "] DOWN:" not in r]
    return rows[-lines:]


def _configured_webhooks():
    urls = set()
    for level in PRIORITY_LEVELS:
        url = db.get_setting(f"webhook_url_{level}", "").strip()
        if url:
            urls.add(url)
    return urls


class ChatSender(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.running = True

    def run(self):
        while self.running and not shutdown_event.is_set():
            try:
                item = chat_queue.get(timeout=2)
                if item:
                    webhook_url, message = item
                    self._dispatch(webhook_url, message)
                chat_queue.task_done()
            except Exception:
                pass

    def _dispatch(self, webhook_url, message):
        if webhook_url is None:
            targets = _configured_webhooks()
        elif webhook_url:
            targets = {webhook_url}
        else:
            targets = set()
            log_message("ALERT NOT SENT: no webhook URL saved for this priority level "
                        "(Settings > webhook URL). Message was: " + message.splitlines()[0])
        for url in targets:
            self._send(url, message)

    def _send(self, webhook_url, message):
        if not webhook_url:
            return
        for attempt in range(3):
            try:
                resp = requests.post(webhook_url, json={"text": message}, timeout=NETWORK_TIMEOUT)
                if resp.ok:
                    return
                log_message(f"Webhook returned HTTP {resp.status_code} (attempt {attempt + 1}/3): {resp.text[:200]}")
            except Exception as e:
                log_message(f"Webhook send failed (attempt {attempt + 1}/3): {e}")
            if attempt < 2:
                time.sleep(2)

    def stop(self):
        self.running = False


def notify(message, webhook_url=None):
    try:
        chat_queue.put_nowait((webhook_url, message))
    except Exception:
        pass


TCP_PORTS = [int(p) for p in os.environ.get("TCP_PORTS", "80,443,554,8000,37777").replace(" ", "").split(",") if p]
TCP_TIMEOUT = 1.0
_check_mode = None


def _icmp_works():
    """True if this host is allowed to send ping (some free hosts block it)."""
    try:
        if platform.system() == "Windows":
            return True
        r = subprocess.run(["ping", "-c", "1", "-W", "2", "1.1.1.1"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def get_check_mode():
    """'icmp' (real ping) or 'tcp' (try to open common camera/NVR ports).
    CHECK_MODE=icmp|tcp forces one; default 'auto' picks icmp when ping works."""
    global _check_mode
    if _check_mode is None:
        wanted = os.environ.get("CHECK_MODE", "auto").lower()
        _check_mode = wanted if wanted in ("icmp", "tcp") else ("icmp" if _icmp_works() else "tcp")
        log_message(f"Check mode: {_check_mode}" + (f" (ports {TCP_PORTS})" if _check_mode == "tcp" else ""))
        print(f"[monitor] check mode: {_check_mode}", flush=True)
    return _check_mode


def tcp_check(ip):
    for port in TCP_PORTS:
        try:
            with socket.create_connection((ip, port), timeout=TCP_TIMEOUT):
                return True
        except Exception:
            continue
    return False


def ping_camera(ip):
    if get_check_mode() == "tcp":
        return tcp_check(ip)
    try:
        if platform.system() == "Windows":
            cmd = ["ping", "-n", "1", "-w", "1000", ip]
            kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW}
        else:
            cmd = ["ping", "-c", "1", "-W", "1", ip]
            kwargs = {}
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=PING_TIMEOUT, **kwargs)
        if result.returncode != 0:
            return False
        if platform.system() == "Windows":
            # Windows ping exits 0 even for "Destination host unreachable"
            # (reply comes from a router/this PC, not the camera). A genuine
            # reply always contains "TTL=" in every language.
            return b"TTL=" in result.stdout.upper()
        return True
    except Exception:
        return False


def check_all_cameras(cameras):
    results = {}
    if not cameras:
        return results
    try:
        if get_check_mode() == "tcp":
            workers, per_check = 32, len(TCP_PORTS) * TCP_TIMEOUT + 1
        else:
            workers, per_check = MAX_WORKERS, PING_TIMEOUT
        wait = math.ceil(len(cameras) / workers) * per_check + 3
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(ping_camera, c["ip"]): c["name"] for c in cameras}
            for future in as_completed(futures, timeout=wait):
                name = futures[future]
                try:
                    results[name] = future.result(timeout=1)
                except Exception:
                    results[name] = False
    except Exception:
        results = {c["name"]: False for c in cameras}
    return results


def _format_camera_group(cams):
    by_branch = {}
    for c in cams:
        by_branch.setdefault(c["branch"] or "Unassigned", []).append(c)
    lines = []
    for branch in sorted(by_branch.keys(), key=str.lower):
        lines.append(f"  [{branch}]")
        for c in by_branch[branch]:
            lines.append(f"    • {c['name']} ({c['ip']})")
    return "\n".join(lines)


def build_summary_text(level):
    """Summary for ONE priority level only - contains just that tier's cameras."""
    all_cams = db.get_all_cameras()
    cameras = [c for c in all_cams if (c["priority"] if c["priority"] in PRIORITY_LEVELS else "medium") == level]
    online = [c for c in cameras if c["is_online"]]
    offline = [c for c in cameras if not c["is_online"]]
    org_name = db.get_setting("org_name", "Camera Monitor")

    lines = [
        f"📊 {org_name.upper()} - {level.upper()} PRIORITY SUMMARY",
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 25,
        f"✅ Online: {len(online)}/{len(cameras)}",
        f"⛔ Offline: {len(offline)}/{len(cameras)}",
    ]
    if offline:
        lines.append("\n🔴 OFFLINE CAMERAS (by branch):")
        lines.append(_format_camera_group(offline))
    lines.append("\n" + "=" * 25)
    return "\n".join(lines)


def send_priority_summaries():
    """Each priority's summary goes only to that priority's own webhook."""
    for level in PRIORITY_LEVELS:
        url = db.get_setting(f"webhook_url_{level}", "").strip()
        if not url:
            continue
        has_cams = any((c["priority"] if c["priority"] in PRIORITY_LEVELS else "medium") == level
                       for c in db.get_all_cameras())
        if has_cams:
            notify(build_summary_text(level), webhook_url=url)


class MonitorThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.last_summary = time.time()

    def run(self):
        log_message("=== MONITOR THREAD STARTED ===")
        notify("🚀 Camera Monitor Dashboard started")

        while not shutdown_event.is_set():
            try:
                check_interval = int(db.get_setting("check_interval", 30) or 30)
                summary_interval = int(db.get_setting("summary_interval", 1800) or 1800)
                thresholds = {
                    "high": int(db.get_setting("offline_threshold_high", 30) or 30),
                    "medium": int(db.get_setting("offline_threshold_medium", 120) or 120),
                    "low": int(db.get_setting("offline_threshold_low", 300) or 300),
                }
                webhooks = {level: db.get_setting(f"webhook_url_{level}", "").strip() for level in PRIORITY_LEVELS}

                cameras = db.get_all_cameras()
                statuses = check_all_cameras(cameras)
                now_iso = datetime.now().isoformat()
                now_dt = datetime.now()

                newly_offline = {level: [] for level in PRIORITY_LEVELS}
                newly_online = {level: [] for level in PRIORITY_LEVELS}
                pending_alert_rows = []

                with db.get_db() as conn:
                    for cam in cameras:
                        name = cam["name"]
                        if name not in statuses:
                            continue
                        priority = cam["priority"] if cam["priority"] in thresholds else "medium"
                        threshold = thresholds[priority]
                        online = statuses[name]
                        if not online:
                            _el = ""
                            if cam["offline_since"]:
                                _el = int((now_dt - datetime.fromisoformat(cam["offline_since"])).total_seconds())
                            log_message(f"DOWN: {name} ({cam['ip']}) priority={priority} offline_for={_el or 0}s "
                                        f"threshold={threshold}s alert_already_sent={bool(cam['alert_sent'])}")

                        if online:
                            if cam["offline_since"] and cam["alert_sent"]:
                                newly_online[priority].append(cam)
                                pending_alert_rows.append((name, cam["ip"], "ONLINE", f"{name} back online"))
                            conn.execute(
                                "UPDATE cameras SET is_online=1, offline_since=NULL, alert_sent=0, alert_priority=NULL, last_checked=? WHERE id=?",
                                (now_iso, cam["id"]),
                            )
                        else:
                            if cam["offline_since"] is None:
                                conn.execute(
                                    "UPDATE cameras SET is_online=0, offline_since=?, last_checked=? WHERE id=?",
                                    (now_iso, now_iso, cam["id"]),
                                )
                            else:
                                since = datetime.fromisoformat(cam["offline_since"])
                                elapsed = (now_dt - since).total_seconds()
                                conn.execute(
                                    "UPDATE cameras SET is_online=0, last_checked=? WHERE id=?",
                                    (now_iso, cam["id"]),
                                )
                                # Re-alert if the priority changed since the last alert, so the
                                # alert goes to the NEW priority's webhook.
                                if elapsed >= threshold and (not cam["alert_sent"] or cam.get("alert_priority") != priority):
                                    newly_offline[priority].append(cam)
                                    pending_alert_rows.append((
                                        name, cam["ip"], "OFFLINE",
                                        f"{name} ({priority} priority) offline since {since.strftime('%H:%M:%S')}"
                                    ))
                                    conn.execute("UPDATE cameras SET alert_sent=1, alert_priority=? WHERE id=?", (priority, cam["id"]))
                    conn.commit()

                for row in pending_alert_rows:
                    db.add_alert(*row)

                for level in PRIORITY_LEVELS:
                    off = newly_offline[level]
                    on = newly_online[level]
                    if not off and not on:
                        continue
                    parts = []
                    if off:
                        parts.append(f"🔴 ALERT ({level.upper()} priority): {len(off)} camera(s) went OFFLINE")
                        parts.append(_format_camera_group(off))
                    if on:
                        parts.append(f"🟢 ({level.upper()} priority) {len(on)} camera(s) BACK ONLINE")
                        parts.append(_format_camera_group(on))
                    parts.append(f"Time: {now_dt.strftime('%Y-%m-%d %H:%M:%S')}")
                    log_message(f"Sending {level.upper()} alert to {'webhook ...' + webhooks[level][-12:] if webhooks[level] else 'NO WEBHOOK (not sent)'}")
                    notify("\n".join(parts), webhook_url=webhooks[level])

                if time.time() - self.last_summary >= summary_interval:
                    send_priority_summaries()
                    log_message("Summary report sent")
                    self.last_summary = time.time()

            except Exception as e:
                log_message(f"Monitor loop error: {e}")

            shutdown_event.wait(timeout=check_interval)

        notify("⏹️ Camera Monitor Dashboard has STOPPED")
        log_message("=== MONITOR THREAD STOPPED ===")


_chat_sender = None
_monitor_thread = None
_start_lock = threading.Lock()


def start_background_services():
    global _chat_sender, _monitor_thread
    with _start_lock:
        db.migrate()
        get_check_mode()
        if _chat_sender is None:
            _chat_sender = ChatSender()
            _chat_sender.start()
        if _monitor_thread is None:
            _monitor_thread = MonitorThread()
            _monitor_thread.start()
