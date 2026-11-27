import os
import re
import requests
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from functools import wraps

from werkzeug.middleware.proxy_fix import ProxyFix
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from openpyxl import load_workbook, Workbook
from openpyxl.styles import Font
from openpyxl.worksheet.datavalidation import DataValidation
from io import BytesIO

import db
import monitor
import sms
import init_db
import bootstrap

BASE_DIR = Path(__file__).resolve().parent
SECRET_KEY_PATH = init_db.SECRET_KEY_PATH

IP_PATTERN = re.compile(
    r"^(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\."
    r"(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\."
    r"(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\."
    r"(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"
)
PRIORITY_LEVELS = ("high", "medium", "low")
ROLES = ("admin", "user")
RESET_OTP_MINUTES = 10
RESET_WEBHOOK_MINUTES = 30

init_db.init()
bootstrap.seed_from_env()

app = Flask(__name__)
# Behind Render/nginx/AWS load balancer: trust X-Forwarded-* so https links and client IPs are right.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
# On a server set SECRET_KEY in the environment; the key file is only the
# local/Windows fallback.
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or SECRET_KEY_PATH.read_text().strip()
# HTTPS is terminated by nginx in production; set COOKIE_SECURE=0 only for plain-HTTP testing.
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "0") == "1"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Not authenticated"}), 401
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    """Always stack this UNDER @login_required (i.e. write @login_required
    first, then @admin_required, then the function) so an anonymous
    request hits the login check before this one ever runs."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("role") != "admin":
            if request.path.startswith("/api/"):
                return jsonify({"error": "Administrator access required"}), 403
            flash("That page is for administrators only.", "error")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)
    return wrapped


def branch_scope():
    """None = this user may see every branch; otherwise a list of branch names.
    Read from the database on every request, so an admin's change takes
    effect immediately without the user having to log out."""
    if session.get("role") == "admin":
        return None
    return db.get_allowed_branches(session.get("user_id"))


@app.context_processor
def inject_globals():
    return {
        "org_name": db.get_setting("org_name", "Camera Monitor"),
        "current_user": session.get("username"),
        "current_role": session.get("role"),
    }


@app.route("/health")
def health():
    return f"ok (check mode: {monitor.get_check_mode()})", 200


# ---------------------------------------------------------------- auth ----

@app.route("/register", methods=["GET", "POST"])
def register():
    """Open self-registration only bootstraps the very first account (which
    becomes admin automatically). Once any user exists, new accounts are
    created from the admin-only User Management page instead - an internal
    monitoring tool shouldn't let strangers register themselves."""
    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    if db.user_count() > 0:
        flash("New accounts are created by an administrator. Contact yours to get access.", "error")
        return redirect(url_for("login"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if len(username) < 3:
            flash("Username must be at least 3 characters.", "error")
        elif len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
        elif password != confirm:
            flash("Passwords do not match.", "error")
        else:
            db.create_user(username, generate_password_hash(password), role="admin")
            flash("Admin account created. You can log in now.", "success")
            return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = db.get_user_by_username(username)

        if user and not user["is_active"]:
            flash("This account has been disabled. Contact your administrator.", "error")
        elif user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            return redirect(request.args.get("next") or url_for("dashboard"))
        else:
            flash("Incorrect username or password.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# -------------------------------------------------- forgot / reset password ----

def _generic_reset_message():
    # Deliberately the SAME message whether the username exists or not, and
    # regardless of which step failed - telling an attacker "that username
    # doesn't exist" is a real information leak (username enumeration).
    return "If that account exists and the chosen method is available, a reset code has been sent."


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        method = request.form.get("method", "webhook")
        user = db.get_user_by_username(username)

        if user and user["is_active"]:
            if method == "webhook":
                admin_webhook = db.get_setting("admin_webhook_url", "").strip()
                if admin_webhook:
                    token = secrets.token_urlsafe(24)
                    expires_at = (datetime.now() + timedelta(minutes=RESET_WEBHOOK_MINUTES)).isoformat()
                    db.create_password_reset(user["id"], "webhook", generate_password_hash(token), expires_at)
                    reset_link = url_for("reset_password", _external=True)
                    monitor.notify(
                        f"🔑 Password reset requested for user '{user['username']}'\n"
                        f"Reset code: {token}\n"
                        f"Open {reset_link} and enter the username + this code.\n"
                        f"Expires in {RESET_WEBHOOK_MINUTES} minutes.",
                        webhook_url=admin_webhook,
                    )
            elif method == "otp" and user["phone_number"]:
                otp = f"{secrets.randbelow(1000000):06d}"
                expires_at = (datetime.now() + timedelta(minutes=RESET_OTP_MINUTES)).isoformat()
                db.create_password_reset(user["id"], "sms_otp", generate_password_hash(otp), expires_at)
                sms.send_otp(user["phone_number"], otp)

        flash(_generic_reset_message(), "success")
        return redirect(url_for("forgot_password"))

    return render_template("forgot_password.html")


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        code = request.form.get("code", "").strip()
        new_password = request.form.get("new_password", "")
        confirm = request.form.get("confirm", "")

        if len(new_password) < 6:
            flash("New password must be at least 6 characters.", "error")
            return render_template("reset_password.html")
        if new_password != confirm:
            flash("Passwords do not match.", "error")
            return render_template("reset_password.html")

        user = db.get_user_by_username(username)
        success = False
        if user and code:
            for reset in db.get_active_resets_for_user(user["id"]):
                if check_password_hash(reset["code_hash"], code):
                    db.record_reset_attempt(reset["id"], success=True)
                    db.update_user_password(user["id"], generate_password_hash(new_password))
                    success = True
                    break
                else:
                    db.record_reset_attempt(reset["id"], success=False)

        if success:
            flash("Password reset. You can log in with your new password now.", "success")
            return redirect(url_for("login"))

        flash("That code is invalid, expired, or already used. Request a new one.", "error")

    return render_template("reset_password.html")


@app.route("/account/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    """Self-service password change for an already-logged-in user of
    either role - no reset code needed since they're already authenticated."""
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm = request.form.get("confirm", "")

        user = db.get_user_by_id(session["user_id"])
        if not user or not check_password_hash(user["password_hash"], current):
            flash("Current password is incorrect.", "error")
        elif len(new_password) < 6:
            flash("New password must be at least 6 characters.", "error")
        elif new_password != confirm:
            flash("New passwords do not match.", "error")
        else:
            db.update_user_password(user["id"], generate_password_hash(new_password))
            flash("Password changed.", "success")
            return redirect(url_for("dashboard"))

    return render_template("change_password.html")


# ---------------------------------------------------------- dashboard ----

@app.route("/")
@login_required
def dashboard():
    scope = branch_scope()
    cameras = db.get_all_cameras(allowed=scope)
    online_count = sum(1 for c in cameras if c["is_online"])
    return render_template("dashboard.html", cameras=cameras, online_count=online_count,
                            offline_count=len(cameras) - online_count, total_count=len(cameras),
                            branches=db.get_all_branches(scope))


@app.route("/api/status")
@login_required
def api_status():
    branch = request.args.get("branch")
    scope = branch_scope()
    cameras = db.get_all_cameras(branch=branch, allowed=scope)
    now = datetime.now()
    for c in cameras:
        if not c["is_online"] and c["offline_since"]:
            since = datetime.fromisoformat(c["offline_since"])
            c["offline_seconds"] = int((now - since).total_seconds())
        else:
            c["offline_seconds"] = 0
    online_count = sum(1 for c in cameras if c["is_online"])
    return jsonify({"cameras": cameras, "online_count": online_count, "offline_count": len(cameras) - online_count,
                     "total_count": len(cameras), "branches": db.get_all_branches(scope),
                     "server_time": now.strftime("%Y-%m-%d %H:%M:%S")})


@app.route("/api/branches")
@login_required
def api_branches():
    return jsonify({"branches": db.get_all_branches(branch_scope())})


# ------------------------------------------------------------ cameras ----
# Everyone can VIEW cameras; only admins can add/edit/delete/bulk-import.
# The buttons are hidden in the template for non-admins too, but that's
# just UI polish - these server-side checks are what actually enforce it.

@app.route("/cameras")
@login_required
def cameras_page():
    branch = request.args.get("branch") or "All"
    scope = branch_scope()
    cameras = db.get_all_cameras(branch=branch if branch != "All" else None, allowed=scope)
    return render_template("cameras.html", cameras=cameras, branches=db.get_all_branches(scope), selected_branch=branch)


def _validate_camera_form(name, ip, exclude_id=None):
    if not name or len(name.strip()) < 1:
        return "Camera name is required."
    if not IP_PATTERN.match(ip.strip()):
        return "Enter a valid IPv4 address (e.g. 192.168.5.39)."
    if db.camera_name_taken(name, exclude_id):
        return "A camera with that name already exists."
    return None


def _normalize_priority(value):
    value = (value or "").strip().lower()
    return value if value in PRIORITY_LEVELS else "medium"


@app.route("/api/cameras", methods=["POST"])
@login_required
@admin_required
def api_add_camera():
    data = request.get_json(silent=True) or request.form
    name = (data.get("name") or "").strip()
    ip = (data.get("ip") or "").strip()
    branch = (data.get("branch") or "Unassigned").strip()
    priority = _normalize_priority(data.get("priority"))
    error = _validate_camera_form(name, ip)
    if error:
        return jsonify({"error": error}), 400
    db.add_camera(name, ip, branch, priority)
    return jsonify({"success": True})


@app.route("/api/cameras/<int:camera_id>", methods=["PUT", "POST"])
@login_required
@admin_required
def api_edit_camera(camera_id):
    if not db.get_camera(camera_id):
        return jsonify({"error": "Camera not found."}), 404
    data = request.get_json(silent=True) or request.form
    name = (data.get("name") or "").strip()
    ip = (data.get("ip") or "").strip()
    branch = (data.get("branch") or "Unassigned").strip()
    priority = _normalize_priority(data.get("priority"))
    error = _validate_camera_form(name, ip, exclude_id=camera_id)
    if error:
        return jsonify({"error": error}), 400
    db.update_camera(camera_id, name, ip, branch, priority)
    return jsonify({"success": True})


@app.route("/api/cameras/<int:camera_id>", methods=["DELETE"])
@login_required
@admin_required
def api_delete_camera(camera_id):
    if not db.get_camera(camera_id):
        return jsonify({"error": "Camera not found."}), 404
    db.delete_camera(camera_id)
    return jsonify({"success": True})


def _validate_bulk_rows(raw_rows):
    valid_rows = []
    errors = []
    for line_no, name, ip, branch, priority in raw_rows:
        name = (name or "").strip()
        ip = (ip or "").strip()
        branch = (branch or "").strip() or "Unassigned"
        priority = _normalize_priority(priority)
        if not name:
            errors.append(f"Row {line_no}: missing camera name")
            continue
        if not IP_PATTERN.match(ip):
            errors.append(f"Row {line_no}: invalid IP \"{ip}\"")
            continue
        if any(r[0] == name for r in valid_rows):
            errors.append(f"Row {line_no}: duplicate name \"{name}\" within this import")
            continue
        valid_rows.append((name, ip, branch, priority))
    return valid_rows, errors


@app.route("/api/cameras/bulk", methods=["POST"])
@login_required
@admin_required
def api_bulk_add_cameras():
    data = request.get_json(silent=True) or {}
    raw_text = (data.get("text") or "").strip()
    if not raw_text:
        return jsonify({"error": "Paste at least one camera line."}), 400
    raw_rows = []
    parse_errors = []
    for i, line in enumerate(raw_text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in re.split(r"\t|,", line)]
        if len(parts) < 2:
            parse_errors.append(f"Line {i}: expected 'name, ip, branch, priority'")
            continue
        raw_rows.append((i, parts[0], parts[1], parts[2] if len(parts) > 2 else "", parts[3] if len(parts) > 3 else ""))
    valid_rows, errors = _validate_bulk_rows(raw_rows)
    errors = parse_errors + errors
    added, skipped_existing = db.bulk_add_cameras(valid_rows)
    return jsonify({"success": True, "added": added, "skipped_existing": skipped_existing, "errors": errors})


@app.route("/cameras/template.xlsx")
@login_required
@admin_required
def download_camera_template():
    wb = Workbook()
    sheet = wb.active
    sheet.title = "Cameras"
    headers = ["Name", "IP", "Branch", "Priority"]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True, name="Arial")
    sheet.append(["Indore_OFFICE", "192.168.1.188", "Indore", "medium"])
    for cell in sheet[2]:
        cell.font = Font(name="Arial")
    validation = DataValidation(type="list", formula1='"high,medium,low"', allow_blank=True)
    sheet.add_data_validation(validation)
    validation.add("D2:D500")
    widths = {"A": 24, "B": 16, "C": 16, "D": 12}
    for col, width in widths.items():
        sheet.column_dimensions[col].width = width
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name="camera_import_template.xlsx",
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/api/cameras/bulk-excel", methods=["POST"])
@login_required
@admin_required
def api_bulk_add_cameras_excel():
    if "file" not in request.files or not request.files["file"].filename:
        return jsonify({"error": "Choose an .xlsx file first."}), 400
    file = request.files["file"]
    if not file.filename.lower().endswith(".xlsx"):
        return jsonify({"error": "Please upload an .xlsx file (Excel 2007 or newer)."}), 400
    try:
        workbook = load_workbook(file, read_only=True, data_only=True)
        sheet = workbook.active
    except Exception:
        return jsonify({"error": "Could not read that file - is it a valid, unprotected .xlsx?"}), 400
    raw_rows = []
    for i, row in enumerate(sheet.iter_rows(values_only=True), start=1):
        if not row or all(cell is None for cell in row):
            continue
        cells = [str(c).strip() if c is not None else "" for c in row[:4]]
        if i == 1 and {c.lower() for c in cells} & {"name", "ip"}:
            continue
        name = cells[0] if len(cells) > 0 else ""
        ip = cells[1] if len(cells) > 1 else ""
        branch = cells[2] if len(cells) > 2 else ""
        priority = cells[3] if len(cells) > 3 else ""
        raw_rows.append((i, name, ip, branch, priority))
    valid_rows, errors = _validate_bulk_rows(raw_rows)
    added, skipped_existing = db.bulk_add_cameras(valid_rows)
    return jsonify({"success": True, "added": added, "skipped_existing": skipped_existing, "errors": errors})


# ------------------------------------------------------------- alerts ----

@app.route("/alerts")
@login_required
def alerts_page():
    return render_template("alerts.html", alerts=db.get_recent_alerts(200, allowed=branch_scope()))


# ------------------------------------------------------------ settings ----

@app.route("/settings", methods=["GET", "POST"])
@login_required
@admin_required
def settings_page():
    if request.method == "POST":
        db.set_setting("org_name", request.form.get("org_name", "Camera Monitor").strip() or "Camera Monitor")
        try:
            check_interval = max(5, int(request.form.get("check_interval", 30)))
        except (TypeError, ValueError):
            check_interval = 30
        db.set_setting("check_interval", check_interval)
        try:
            summary_interval = max(60, int(request.form.get("summary_interval", 1800)))
        except (TypeError, ValueError):
            summary_interval = 1800
        db.set_setting("summary_interval", summary_interval)

        tier_minimums = {"high": 10, "medium": 10, "low": 10}
        for level in PRIORITY_LEVELS:
            db.set_setting(f"webhook_url_{level}", request.form.get(f"webhook_url_{level}", "").strip())
            try:
                threshold = max(tier_minimums[level], int(request.form.get(f"offline_threshold_{level}", 60)))
            except (TypeError, ValueError):
                threshold = tier_minimums[level]
            db.set_setting(f"offline_threshold_{level}", threshold)

        db.set_setting("admin_webhook_url", request.form.get("admin_webhook_url", "").strip())
        db.set_setting("sms_gateway_webhook_url", request.form.get("sms_gateway_webhook_url", "").strip())

        flash("Settings saved.", "success")
        return redirect(url_for("settings_page"))

    return render_template("settings.html", settings=db.get_all_settings())


@app.route("/api/settings/test-alert", methods=["POST"])
@login_required
@admin_required
def api_test_alert():
    data = request.get_json(silent=True) or {}
    priority = data.get("priority")
    username = session.get("username")
    timestamp = datetime.now().strftime("%H:%M:%S")

    if priority in PRIORITY_LEVELS:
        webhook_url = db.get_setting(f"webhook_url_{priority}", "").strip()
        if not webhook_url:
            return jsonify({"error": f"No webhook URL saved for {priority.capitalize()} priority yet."}), 400
        try:
            resp = requests.post(webhook_url, json={"text": f"🔔 Test alert ({priority.upper()} priority) - sent by {username} at {timestamp}"}, timeout=8)
        except Exception as e:
            return jsonify({"error": f"Could not reach webhook: {e}"}), 502
        if not resp.ok:
            return jsonify({"error": f"Google Chat rejected it (HTTP {resp.status_code}): {resp.text[:200]}"}), 502
    else:
        monitor.notify(f"🔔 Test alert (all tiers) - sent by {username} at {timestamp}")

    return jsonify({"success": True})


@app.route("/api/settings/test-admin-webhook", methods=["POST"])
@login_required
@admin_required
def api_test_admin_webhook():
    webhook_url = db.get_setting("admin_webhook_url", "").strip()
    if not webhook_url:
        return jsonify({"error": "No admin/security webhook URL saved yet."}), 400
    username = session.get("username")
    timestamp = datetime.now().strftime("%H:%M:%S")
    monitor.notify(f"🔔 Test alert (admin/security channel) - sent by {username} at {timestamp}", webhook_url=webhook_url)
    return jsonify({"success": True})


# --------------------------------------------------------- user management ----

@app.route("/users")
@login_required
@admin_required
def users_page():
    users = db.get_all_users()
    for u in users:
        u["branch_list"] = db.parse_branches(u.get("allowed_branches")) if u["role"] != "admin" else None
    return render_template("users.html", users=users, branches=db.get_all_branches())


def _normalize_role(value):
    value = (value or "").strip().lower()
    return value if value in ROLES else "user"


def _validate_user_form(username, password, exclude_id=None, require_password=True):
    if not username or len(username.strip()) < 3:
        return "Username must be at least 3 characters."
    if require_password and len(password) < 6:
        return "Password must be at least 6 characters."
    if db.username_taken(username, exclude_id):
        return "That username is already taken."
    return None


def _parse_branch_access(data, role):
    """Returns (allowed_branches, error). allowed_branches None = all branches.
    Admins always have every branch. The form sends all_branches=true OR a
    list in `branches`."""
    if role == "admin":
        return None, None
    raw_all = data.get("all_branches", True)
    if raw_all is True or str(raw_all).lower() in ("1", "true", "yes", "on"):
        return None, None
    chosen = data.get("branches") or []
    if not isinstance(chosen, list):
        chosen = [chosen]
    existing = {b["branch"] for b in db.get_all_branches()}
    clean = []
    for name in chosen:
        name = str(name).strip()
        if name in existing and name not in clean:
            clean.append(name)
    if not clean:
        return None, "Select at least one branch, or choose 'All branches'."
    return clean, None


@app.route("/api/users", methods=["POST"])
@login_required
@admin_required
def api_add_user():
    data = request.get_json(silent=True) or request.form
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    role = _normalize_role(data.get("role"))
    phone_number = (data.get("phone_number") or "").strip()

    error = _validate_user_form(username, password)
    allowed_branches = None
    if not error:
        allowed_branches, error = _parse_branch_access(data, role)
    if error:
        return jsonify({"error": error}), 400

    db.create_user(username, generate_password_hash(password), role, phone_number, allowed_branches)
    return jsonify({"success": True})


@app.route("/api/users/<int:user_id>", methods=["PUT", "POST"])
@login_required
@admin_required
def api_edit_user(user_id):
    target = db.get_user_by_id(user_id)
    if not target:
        return jsonify({"error": "User not found."}), 404

    data = request.get_json(silent=True) or request.form
    username = (data.get("username") or "").strip()
    role = _normalize_role(data.get("role"))
    phone_number = (data.get("phone_number") or "").strip()
    is_active = str(data.get("is_active", "1")) in ("1", "true", "True")

    error = _validate_user_form(username, "", exclude_id=user_id, require_password=False)
    allowed_branches = None
    if not error:
        allowed_branches, error = _parse_branch_access(data, role)
    if error:
        return jsonify({"error": error}), 400

    # Never let the last admin demote or deactivate themselves (or be
    # demoted/deactivated) - that would permanently lock everyone out of
    # Settings and User Management with no way back in.
    would_remove_last_admin = (
        target["role"] == "admin"
        and (role != "admin" or not is_active)
        and db.admin_count(exclude_id=user_id) == 0
    )
    if would_remove_last_admin:
        return jsonify({"error": "This is the only active administrator - promote another user to admin first."}), 400

    db.update_user(user_id, username, role, phone_number, is_active, allowed_branches)
    return jsonify({"success": True})


@app.route("/api/users/<int:user_id>", methods=["DELETE"])
@login_required
@admin_required
def api_delete_user(user_id):
    if user_id == session.get("user_id"):
        return jsonify({"error": "You can't delete your own account while logged in as it."}), 400
    target = db.get_user_by_id(user_id)
    if not target:
        return jsonify({"error": "User not found."}), 404
    if target["role"] == "admin" and db.admin_count(exclude_id=user_id) == 0:
        return jsonify({"error": "This is the only active administrator - promote another user to admin first."}), 400
    db.delete_user(user_id)
    return jsonify({"success": True})


@app.route("/api/users/<int:user_id>/reset-password", methods=["POST"])
@login_required
@admin_required
def api_admin_reset_user_password(user_id):
    """Admin-initiated reset: generates a new password immediately and
    returns it once in the response (never stored or logged in plain text)
    for the admin to relay to that user directly."""
    target = db.get_user_by_id(user_id)
    if not target:
        return jsonify({"error": "User not found."}), 404
    new_password = secrets.token_urlsafe(9)
    db.update_user_password(user_id, generate_password_hash(new_password))
    return jsonify({"success": True, "new_password": new_password})


monitor.start_background_services()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
