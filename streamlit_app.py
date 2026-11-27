"""
Streamlit version of the Camera Monitor Dashboard.

Uses the same database (db.py), monitor thread (monitor.py) and first-boot
seeding (bootstrap.py) as the Flask version - only the web screens differ.

Run locally:   streamlit run streamlit_app.py
Streamlit Cloud: main file = streamlit_app.py ; put settings in the app's Secrets.
"""
import html
import os
import re
import ipaddress
import secrets as pysecrets
from datetime import datetime, timedelta
from io import BytesIO

import requests
import streamlit as st
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from werkzeug.security import generate_password_hash, check_password_hash

st.set_page_config(page_title="Camera Monitor", page_icon="📹", layout="wide")

# ---- Streamlit Cloud keeps settings in st.secrets; bootstrap/monitor read env vars.
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, (str, int, float)) and _k not in os.environ:
            os.environ[_k] = str(_v)
except Exception:
    pass

import init_db   # noqa: E402
import db        # noqa: E402
import monitor   # noqa: E402
import bootstrap  # noqa: E402

PRIORITIES = ("high", "medium", "low")
ROLES = ("admin", "user")


@st.cache_resource
def _start_backend():
    """Runs once per server process: create DB, seed from env, start the monitor thread."""
    init_db.init()
    bootstrap.seed_from_env()
    monitor.start_background_services()
    return True


_start_backend()


# ------------------------------------------------------------------ helpers --

def valid_ip(ip):
    try:
        ipaddress.IPv4Address(ip.strip())
        return True
    except ValueError:
        return False


def scope():
    """None = every branch, else the list of branches this user may see."""
    if st.session_state.get("role") == "admin":
        return None
    return db.get_allowed_branches(st.session_state.get("user_id"))


def is_admin():
    return st.session_state.get("role") == "admin"


def norm_priority(v):
    v = (v or "").strip().lower()
    return v if v in PRIORITIES else "medium"


def check_camera(name, ip, exclude_id=None):
    if not name.strip():
        return "Camera name is required."
    if not valid_ip(ip):
        return "Enter a valid IPv4 address (e.g. 103.10.20.30)."
    if db.camera_name_taken(name, exclude_id):
        return "A camera with that name already exists."
    return None


def parse_bulk_text(text):
    rows, errors, seen = [], [], set()
    for i, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        p = [x.strip() for x in re.split(r"\t|,", line)]
        if len(p) < 2:
            errors.append(f"Line {i}: expected 'name, ip, branch, priority'")
        elif not p[0]:
            errors.append(f"Line {i}: missing camera name")
        elif not valid_ip(p[1]):
            errors.append(f"Line {i}: invalid IP \"{p[1]}\"")
        elif p[0] in seen:
            errors.append(f"Line {i}: duplicate name \"{p[0]}\"")
        else:
            seen.add(p[0])
            rows.append((p[0], p[1], p[2] if len(p) > 2 and p[2] else "Unassigned",
                         norm_priority(p[3] if len(p) > 3 else "")))
    return rows, errors


def template_xlsx():
    wb = Workbook()
    ws = wb.active
    ws.title = "Cameras"
    ws.append(["Name", "IP", "Branch", "Priority"])
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.append(["Indore_OFFICE", "103.10.20.30", "Indore", "medium"])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def flash(kind, msg):
    st.session_state["_flash"] = (kind, msg)


def show_flash():
    f = st.session_state.pop("_flash", None)
    if f:
        {"success": st.success, "error": st.error, "info": st.info}[f[0]](f[1])


# ------------------------------------------------------------------ login ---

def login_screen():
    st.title("📹 " + db.get_setting("org_name", "Camera Monitor"))
    first_run = db.user_count() == 0

    if first_run:
        st.info("No accounts yet. The account you create now becomes the administrator.")
        with st.form("register"):
            u = st.text_input("Username")
            p = st.text_input("Password", type="password")
            c = st.text_input("Confirm password", type="password")
            if st.form_submit_button("Create admin account", type="primary"):
                if len(u.strip()) < 3:
                    st.error("Username must be at least 3 characters.")
                elif len(p) < 6:
                    st.error("Password must be at least 6 characters.")
                elif p != c:
                    st.error("Passwords do not match.")
                else:
                    db.create_user(u.strip(), generate_password_hash(p), role="admin")
                    flash("success", "Admin account created. Log in now.")
                    st.rerun()
        show_flash()
        return

    show_flash()
    with st.form("login"):
        u = st.text_input("Username")
        p = st.text_input("Password", type="password")
        if st.form_submit_button("Log in", type="primary"):
            user = db.get_user_by_username(u)
            if user and not user["is_active"]:
                st.error("This account has been disabled. Contact your administrator.")
            elif user and check_password_hash(user["password_hash"], p):
                st.session_state.update(user_id=user["id"], username=user["username"], role=user["role"])
                st.rerun()
            else:
                st.error("Incorrect username or password.")
    st.caption("Forgot your password? Ask an administrator to reset it.")


# -------------------------------------------------------------- dashboard ---

def tile_html(c):
    online = bool(c["is_online"])
    color = "#16a34a" if online else "#dc2626"
    bg = "rgba(22,163,74,.08)" if online else "rgba(220,38,38,.10)"
    pr = {"high": "#dc2626", "medium": "#d97706", "low": "#6b7280"}.get(c["priority"], "#6b7280")
    return (
        f"<div style='border:1px solid {color};background:{bg};border-radius:10px;padding:10px 12px;'>"
        f"<div style='display:flex;justify-content:space-between;font-weight:600'>"
        f"<span>{html.escape(c['name'])}</span><span style='color:{color}'>●</span></div>"
        f"<div style='font-family:monospace;font-size:.85em;opacity:.8'>{html.escape(c['ip'])}</div>"
        f"<div style='font-size:.8em;margin-top:4px'>{html.escape(c['branch'])} · "
        f"<span style='color:{pr}'>{c['priority'].capitalize()}</span> · "
        f"<span style='color:{color}'>{'online' if online else 'offline'}</span></div></div>"
    )


@st.fragment(run_every=30)
def live_status():
    sc = scope()
    all_cams = db.get_all_cameras(allowed=sc)
    on = sum(1 for c in all_cams if c["is_online"])
    m1, m2, m3 = st.columns(3)
    m1.metric("Total cameras", len(all_cams))
    m2.metric("Online", on)
    m3.metric("Offline", len(all_cams) - on)
    st.caption(f"Auto-refreshes every 30 s · last update {datetime.now():%H:%M:%S} · check mode: {monitor.get_check_mode()}")

    branches = ["All"] + [b["branch"] for b in db.get_all_branches(sc)]
    f1, f2, f3 = st.columns([2, 2, 1])
    branch = f1.selectbox("Branch", branches, key="dash_branch")
    q = f2.text_input("Search name or IP", key="dash_q").strip().lower()
    only_off = f3.toggle("Offline only", key="dash_off")

    cams = [c for c in all_cams
            if (branch == "All" or c["branch"] == branch)
            and (not q or q in c["name"].lower() or q in c["ip"])
            and (not only_off or not c["is_online"])]
    if not all_cams:
        st.info("No cameras yet." + (" Add some on the Cameras page." if is_admin() else " Ask an administrator."))
        return
    if not cams:
        st.info("No matching cameras.")
        return
    cols = st.columns(4)
    for i, c in enumerate(cams):
        cols[i % 4].markdown(tile_html(c), unsafe_allow_html=True)
        cols[i % 4].write("")


def page_dashboard():
    st.subheader("Live status")
    live_status()


# ---------------------------------------------------------------- cameras ---

def page_cameras():
    st.subheader("Cameras")
    show_flash()
    sc = scope()
    branches = ["All"] + [b["branch"] for b in db.get_all_branches(sc)]
    branch = st.selectbox("Branch", branches)
    cams = db.get_all_cameras(branch=None if branch == "All" else branch, allowed=sc)
    st.dataframe(
        [{"Name": c["name"], "IP": c["ip"], "Branch": c["branch"], "Priority": c["priority"],
          "Status": "online" if c["is_online"] else "OFFLINE", "Last checked": (c["last_checked"] or "")[:19]}
         for c in cams],
        width="stretch", hide_index=True)

    if not is_admin():
        return

    t_add, t_edit, t_bulk = st.tabs(["➕ Add camera", "✏️ Edit / delete", "📥 Bulk import"])

    with t_add:
        with st.form("add_cam", clear_on_submit=True):
            c1, c2 = st.columns(2)
            name = c1.text_input("Name")
            ip = c2.text_input("IP address")
            branch_in = c1.text_input("Branch", value="Unassigned")
            pr = c2.selectbox("Priority", PRIORITIES, index=1)
            if st.form_submit_button("Add camera", type="primary"):
                err = check_camera(name, ip)
                if err:
                    st.error(err)
                else:
                    db.add_camera(name.strip(), ip.strip(), branch_in.strip() or "Unassigned", pr)
                    flash("success", f"Added {name.strip()}.")
                    st.rerun()

    with t_edit:
        all_cams = db.get_all_cameras()
        if not all_cams:
            st.info("No cameras to edit.")
        else:
            labels = {f"{c['name']} ({c['ip']})": c for c in all_cams}
            cam = labels[st.selectbox("Choose a camera", list(labels))]
            with st.form(f"edit_cam_{cam['id']}"):
                c1, c2 = st.columns(2)
                name = c1.text_input("Name", cam["name"])
                ip = c2.text_input("IP address", cam["ip"])
                br = c1.text_input("Branch", cam["branch"])
                pr = c2.selectbox("Priority", PRIORITIES,
                                  index=PRIORITIES.index(cam["priority"]) if cam["priority"] in PRIORITIES else 1)
                if st.form_submit_button("Save changes", type="primary"):
                    err = check_camera(name, ip, exclude_id=cam["id"])
                    if err:
                        st.error(err)
                    else:
                        db.update_camera(cam["id"], name.strip(), ip.strip(), br.strip() or "Unassigned", pr)
                        flash("success", "Camera updated.")
                        st.rerun()
            confirm = st.checkbox(f"Yes, delete {cam['name']}", key=f"del_ok_{cam['id']}")
            if st.button("Delete camera", disabled=not confirm):
                db.delete_camera(cam["id"])
                flash("success", f"Deleted {cam['name']}.")
                st.rerun()

    with t_bulk:
        st.caption("One camera per line: `name, ip, branch, priority`  (priority = high / medium / low)")
        text = st.text_area("Paste cameras", height=160, key="bulk_text")
        if st.button("Import pasted cameras"):
            rows, errs = parse_bulk_text(text)
            added, skipped = db.bulk_add_cameras(rows)
            msg = f"Added {added}, skipped {len(skipped)} existing."
            if errs:
                msg += " Problems: " + " | ".join(errs[:5])
            flash("success" if added else "info", msg)
            st.rerun()
        st.divider()
        st.download_button("Download Excel template", template_xlsx(), "camera_import_template.xlsx")
        up = st.file_uploader("Upload filled .xlsx", type=["xlsx"])
        if up and st.button("Import Excel file"):
            try:
                ws = load_workbook(up, read_only=True, data_only=True).active
            except Exception:
                st.error("Could not read that file - is it a valid .xlsx?")
            else:
                lines = []
                for i, row in enumerate(ws.iter_rows(values_only=True), start=1):
                    cells = [str(c).strip() if c is not None else "" for c in (row or [])[:4]]
                    if not any(cells) or (i == 1 and {c.lower() for c in cells} & {"name", "ip"}):
                        continue
                    lines.append(",".join(cells))
                rows, errs = parse_bulk_text("\n".join(lines))
                added, skipped = db.bulk_add_cameras(rows)
                msg = f"Added {added}, skipped {len(skipped)} existing."
                if errs:
                    msg += " Problems: " + " | ".join(errs[:5])
                flash("success" if added else "info", msg)
                st.rerun()


# ----------------------------------------------------------------- alerts ---

def page_alerts():
    st.subheader("Recent alerts")
    alerts = db.get_recent_alerts(200, allowed=scope())
    if not alerts:
        st.info("No alerts yet.")
        return
    icon = {"OFFLINE": "🔴 OFFLINE", "ONLINE": "🟢 ONLINE", "SYSTEM": "ℹ️ SYSTEM"}
    st.dataframe(
        [{"Time": (a["created_at"] or "")[:19].replace("T", " "), "Type": icon.get(a["alert_type"], a["alert_type"]),
          "Camera": a["camera_name"], "IP": a["camera_ip"], "Message": a["message"]} for a in alerts],
        width="stretch", hide_index=True)


# --------------------------------------------------------------- settings ---

def send_test(url, text):
    try:
        r = requests.post(url, json={"text": text}, timeout=8)
        return None if r.ok else f"Google Chat rejected it (HTTP {r.status_code}): {r.text[:150]}"
    except Exception as e:
        return f"Could not reach webhook: {e}"


def page_settings():
    st.subheader("Settings")
    show_flash()
    s = db.get_all_settings()

    def num(key, default, minimum):
        try:
            return max(minimum, int(s.get(key) or default))
        except ValueError:
            return default

    with st.form("settings"):
        org = st.text_input("Organisation name", s.get("org_name", "Camera Monitor"))
        c1, c2 = st.columns(2)
        check = c1.number_input("Check interval (seconds)", min_value=5, value=num("check_interval", 30, 5))
        summ = c2.number_input("Summary interval (seconds)", min_value=60, value=num("summary_interval", 1800, 60))
        st.markdown("**Alerts per priority** - Google Chat webhook and how long a camera must be offline before alerting")
        hooks, thr = {}, {}
        for lvl, dflt in (("high", 30), ("medium", 120), ("low", 300)):
            a, b = st.columns([3, 1])
            hooks[lvl] = a.text_input(f"{lvl.capitalize()} priority webhook URL", s.get(f"webhook_url_{lvl}", ""), type="password")
            thr[lvl] = b.number_input(f"{lvl.capitalize()} threshold (s)", min_value=10,
                                       value=num(f"offline_threshold_{lvl}", dflt, 10))
        admin_hook = st.text_input("Admin / security webhook URL", s.get("admin_webhook_url", ""), type="password")
        sms_url = st.text_input("SMS gateway URL (optional)", s.get("sms_gateway_webhook_url", ""))
        if st.form_submit_button("Save settings", type="primary"):
            db.set_setting("org_name", org.strip() or "Camera Monitor")
            db.set_setting("check_interval", int(check))
            db.set_setting("summary_interval", int(summ))
            for lvl in PRIORITIES:
                db.set_setting(f"webhook_url_{lvl}", hooks[lvl].strip())
                db.set_setting(f"offline_threshold_{lvl}", int(thr[lvl]))
            db.set_setting("admin_webhook_url", admin_hook.strip())
            db.set_setting("sms_gateway_webhook_url", sms_url.strip())
            flash("success", "Settings saved.")
            st.rerun()

    st.markdown("**Send a test alert**")
    cols = st.columns(4)
    stamp = f"sent by {st.session_state['username']} at {datetime.now():%H:%M:%S}"
    for col, lvl in zip(cols, PRIORITIES):
        if col.button(f"Test {lvl}"):
            url = db.get_setting(f"webhook_url_{lvl}", "").strip()
            err = send_test(url, f"🔔 Test alert ({lvl.upper()} priority) - {stamp}") if url else "No webhook saved for that priority."
            st.error(err) if err else st.success(f"{lvl.capitalize()} test sent.")
    if cols[3].button("Test admin"):
        url = db.get_setting("admin_webhook_url", "").strip()
        err = send_test(url, f"🔔 Test alert (admin channel) - {stamp}") if url else "No admin webhook saved."
        st.error(err) if err else st.success("Admin test sent.")


# ------------------------------------------------------------------ users ---

def page_users():
    st.subheader("User management")
    show_flash()
    users = db.get_all_users()
    st.dataframe(
        [{"Username": u["username"], "Role": u["role"], "Active": bool(u["is_active"]),
          "Phone": u["phone_number"] or "",
          "Branches": "All" if u["role"] == "admin" or db.parse_branches(u["allowed_branches"]) is None
          else ", ".join(db.parse_branches(u["allowed_branches"]))} for u in users],
        width="stretch", hide_index=True)

    existing = [b["branch"] for b in db.get_all_branches()]
    t_add, t_edit = st.tabs(["➕ Add user", "✏️ Edit / reset / delete"])

    with t_add:
        with st.form("add_user", clear_on_submit=True):
            c1, c2 = st.columns(2)
            name = c1.text_input("Username")
            pw = c2.text_input("Password", type="password")
            role = c1.selectbox("Role", ROLES, index=1)
            phone = c2.text_input("Phone (optional)")
            branches = st.multiselect("Branches (empty = all branches; ignored for admins)", existing)
            if st.form_submit_button("Create user", type="primary"):
                if len(name.strip()) < 3:
                    st.error("Username must be at least 3 characters.")
                elif len(pw) < 6:
                    st.error("Password must be at least 6 characters.")
                elif db.username_taken(name):
                    st.error("That username is already taken.")
                else:
                    db.create_user(name.strip(), generate_password_hash(pw), role, phone,
                                   None if role == "admin" or not branches else branches)
                    flash("success", f"User {name.strip()} created.")
                    st.rerun()

    with t_edit:
        labels = {u["username"]: u for u in users}
        u = labels[st.selectbox("Choose a user", list(labels))]
        cur = db.parse_branches(u["allowed_branches"]) if u["role"] != "admin" else None
        with st.form(f"edit_user_{u['id']}"):
            c1, c2 = st.columns(2)
            name = c1.text_input("Username", u["username"])
            phone = c2.text_input("Phone", u["phone_number"] or "")
            role = c1.selectbox("Role", ROLES, index=ROLES.index(u["role"]))
            active = c2.checkbox("Account active", bool(u["is_active"]))
            branches = st.multiselect("Branches (empty = all)", sorted(set(existing) | set(cur or [])), default=cur or [])
            if st.form_submit_button("Save user", type="primary"):
                last_admin = (u["role"] == "admin" and (role != "admin" or not active)
                              and db.admin_count(exclude_id=u["id"]) == 0)
                if len(name.strip()) < 3:
                    st.error("Username must be at least 3 characters.")
                elif db.username_taken(name, exclude_id=u["id"]):
                    st.error("That username is already taken.")
                elif last_admin:
                    st.error("This is the only active administrator - promote another user first.")
                else:
                    db.update_user(u["id"], name.strip(), role, phone, active,
                                   None if role == "admin" or not branches else branches)
                    flash("success", "User updated.")
                    st.rerun()

        b1, b2 = st.columns(2)
        if b1.button("Reset password"):
            new_pw = pysecrets.token_urlsafe(9)
            db.update_user_password(u["id"], generate_password_hash(new_pw))
            st.success(f"New password for {u['username']} (shown once): `{new_pw}`")
        ok = b2.checkbox(f"Yes, delete {u['username']}", key=f"udel_{u['id']}")
        if b2.button("Delete user", disabled=not ok):
            if u["id"] == st.session_state["user_id"]:
                st.error("You can't delete your own account while logged in as it.")
            elif u["role"] == "admin" and db.admin_count(exclude_id=u["id"]) == 0:
                st.error("This is the only active administrator.")
            else:
                db.delete_user(u["id"])
                flash("success", "User deleted.")
                st.rerun()


# ---------------------------------------------------------------- account ---

def page_account():
    st.subheader("Change my password")
    with st.form("chpw"):
        cur = st.text_input("Current password", type="password")
        new = st.text_input("New password", type="password")
        conf = st.text_input("Confirm new password", type="password")
        if st.form_submit_button("Change password", type="primary"):
            user = db.get_user_by_id(st.session_state["user_id"])
            if not user or not check_password_hash(user["password_hash"], cur):
                st.error("Current password is incorrect.")
            elif len(new) < 6:
                st.error("New password must be at least 6 characters.")
            elif new != conf:
                st.error("New passwords do not match.")
            else:
                db.update_user_password(user["id"], generate_password_hash(new))
                st.success("Password changed.")


# ------------------------------------------------------------------- main ---

def main():
    if not st.session_state.get("user_id"):
        login_screen()
        return
    # Re-check the account on every run so a disabled/deleted user is logged out at once.
    user = db.get_user_by_id(st.session_state["user_id"])
    if not user or not user["is_active"]:
        st.session_state.clear()
        st.rerun()
    st.session_state["role"] = user["role"]

    pages = {"📊 Dashboard": page_dashboard, "📹 Cameras": page_cameras, "🔔 Alerts": page_alerts}
    if is_admin():
        pages.update({"⚙️ Settings": page_settings, "👥 Users": page_users})
    pages["🔑 My password"] = page_account

    with st.sidebar:
        st.title("📹 " + db.get_setting("org_name", "Camera Monitor"))
        choice = st.radio("Menu", list(pages), label_visibility="collapsed")
        st.caption(f"Signed in as **{st.session_state['username']}** ({st.session_state['role']})")
        if st.button("Log out"):
            st.session_state.clear()
            st.rerun()
    pages[choice]()


main()
