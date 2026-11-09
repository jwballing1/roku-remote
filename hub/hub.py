#!/usr/bin/env python3
"""TV Remote family hub: lets family members use a relative's TV Remote from anywhere.

Run: python hub.py [--port 8000]. In the Docker image, settings are entered on the setup page
(port 8080, home network only) and kept in the data folder. Otherwise they come from environment
variables, described in README.md.

TV Remote at the relative's home connects out to this hub and asks for waiting requests
(long polling). Family members sign in with an email and password, open the remote page here, and the
hub passes each request to that home and returns its answer. The hub stores no TV or channel
data: only accounts, homes, who can use them, and an activity log.
"""
import argparse
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import queue
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB_DIR = Path(os.environ.get("HUB_WEB_DIR", HERE.parent / "web"))
sys.path.insert(0, str(WEB_DIR))
import relay_rules  # noqa: E402  (shared with TV Remote, so both check the same rules)

DATA_DIR = Path(os.environ.get("HUB_DATA", HERE / "data"))
DB_FILE = DATA_DIR / "hub.sqlite3"
REMOTE_PAGE = WEB_DIR / "index.html"
PORTAL_PAGE = HERE / "portal.html"
SETUP_PAGE = HERE / "setup.html"
SETTINGS_FILE = DATA_DIR / "settings.json"  # Written by the setup page
CADDYFILE = DATA_DIR / "Caddyfile"          # Written from the settings, for Caddy
SETUP_PORT = int(os.environ.get("HUB_SETUP_PORT", "0"))  # 0: no setup page, so settings come from environment variables
MANAGE_CADDY = os.environ.get("HUB_MANAGE_CADDY") == "1"  # In the Docker image, the hub starts Caddy itself

SESSION_DAYS = 90       # How long a phone or computer stays signed in
PASSWORD_MIN = 8
INVITE_DAYS = 7
RESET_DAYS = 3          # How long a password reset link works
PAIR_MINUTES = 10
POLL_SECONDS = 25       # How long a home's request for work waits before an empty answer
OFFLINE_SECONDS = 15    # A connected home asks again straight after each answer, so a gap this long means it's offline
RELAY_SECONDS = 90      # A channel button can take a while, so wait this long for the home's answer
MAX_BODY = 1024 * 1024
MAX_REPLY = 8 * 1024 * 1024
EMAIL_PATTERN = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
COOKIE = "tvremote_session"

db_lock = threading.Lock()
rate_lock = threading.Lock()
rate_hits = {}  # key -> recent times, for slowing down repeated sign-in attempts
pairings = {}   # pair id -> {secret_hash, code, expires, claimed: {...} or None}
pairings_lock = threading.Lock()
links = {}      # home id -> HomeLink
links_lock = threading.Lock()


class HubError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def now():
    return time.time()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


# ----- Settings: from the setup page if it's been used, otherwise from environment variables -----

def saved_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def public_url():
    saved = saved_settings()
    if saved.get("name"):
        return f"https://{saved['name']}:{saved['port']}"
    return os.environ.get("PUBLIC_URL", "http://127.0.0.1:8020").rstrip("/")


# ----- Storage -----

def db():
    connection = sqlite3.connect(DB_FILE, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with db_lock, db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS homes (id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL, created_by TEXT NOT NULL, created REAL NOT NULL, last_seen REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS members (home_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL, added REAL NOT NULL, PRIMARY KEY (home_id, user_id));
            CREATE TABLE IF NOT EXISTS invites (token_hash TEXT PRIMARY KEY, home_id TEXT NOT NULL, role TEXT NOT NULL, expires REAL NOT NULL, created_by TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS activity (id INTEGER PRIMARY KEY AUTOINCREMENT, home_id TEXT NOT NULL, user_id TEXT NOT NULL, at REAL NOT NULL, text TEXT NOT NULL, ok INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS resets (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL, created_by TEXT NOT NULL);
            DROP TABLE IF EXISTS codes;
        """)
        # Accounts made before passwords existed get password columns, empty until a reset link is used.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "pw_hash" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN pw_hash TEXT NOT NULL DEFAULT ''")
            conn.execute("ALTER TABLE users ADD COLUMN pw_salt TEXT NOT NULL DEFAULT ''")


def query(sql, args=(), one=False):
    with db_lock, db() as conn:
        rows = conn.execute(sql, args).fetchall()
    return (rows[0] if rows else None) if one else rows


def execute(sql, args=()):
    with db_lock, db() as conn:
        conn.execute(sql, args)


# ----- Accounts: email and password. People join with invite links, and owners can make reset links. -----

def rate_limit(key, limit, seconds):
    with rate_lock:
        hits = [t for t in rate_hits.get(key, []) if now() - t < seconds]
        if len(hits) >= limit:
            raise HubError("Too many tries. Wait a few minutes, then try again.", 429)
        hits.append(now())
        rate_hits[key] = hits


def clean_email(value):
    address = str(value or "").strip().lower()
    if not EMAIL_PATTERN.match(address) or len(address) > 200:
        raise HubError("Enter a valid email address.")
    return address


def invite_row(token):
    if not token:
        return None
    row = query("SELECT * FROM invites WHERE token_hash = ?", (digest(str(token)),), one=True)
    return row if row and row["expires"] > now() else None


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200000).hex()


def check_new_password(password):
    if not isinstance(password, str) or len(password) < PASSWORD_MIN or len(password) > 200:
        raise HubError(f"Choose a password of at least {PASSWORD_MIN} characters.")
    return password


def set_password(user_id, password):
    salt = secrets.token_hex(16)
    execute("UPDATE users SET pw_hash = ?, pw_salt = ? WHERE id = ?", (password_hash(password, salt), salt, user_id))


def password_ok(user, password):
    if not user or not user["pw_hash"] or not isinstance(password, str):
        return False
    return hmac.compare_digest(password_hash(password, user["pw_salt"]), user["pw_hash"])


def clean_name(value):
    name = re.sub(r"\s+", " ", str(value or "")).strip()[:40]
    if not name:
        raise HubError("Enter your name. It's shown on the TV screen when you help.")
    return name


def user_by_email(address):
    return query("SELECT * FROM users WHERE email = ?", (address,), one=True)


def create_user(address, name, password):
    user_id = uuid.uuid4().hex
    execute("INSERT INTO users (id, email, name, created) VALUES (?, ?, ?, ?)", (user_id, address, name, now()))
    set_password(user_id, password)
    return query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)


def start_session(user_id):
    token = secrets.token_urlsafe(32)
    execute("INSERT INTO sessions (token_hash, user_id, expires) VALUES (?, ?, ?)",
            (digest(token), user_id, now() + SESSION_DAYS * 86400))
    return token


def sign_in(data, ip):
    """Check an email and password, and start a session. The error never says which one was wrong."""
    address = clean_email(data.get("email"))
    rate_limit(("ip", ip), 30, 900)
    rate_limit(("email", address), 10, 900)
    user = user_by_email(address)
    if not password_ok(user, data.get("password")):
        time.sleep(0.5)  # Slows down guessing.
        raise HubError("That email or password isn't right.")
    return start_session(user["id"])


def accept_invite(data, ip):
    """Join a home with an invite link: make an account, or use an existing one with its password."""
    rate_limit(("ip", ip), 30, 900)
    invite = invite_row(data.get("invite"))
    if not invite:
        raise HubError("This invite has expired or was already used. Ask for a new one.")
    address = clean_email(data.get("email"))
    user = user_by_email(address)
    if user and user["pw_hash"]:
        if not password_ok(user, data.get("password")):
            time.sleep(0.5)
            raise HubError("You already have an account with this email. Enter its password to accept the invite.")
    elif user:
        set_password(user["id"], check_new_password(data.get("password")))  # An account from before passwords
    else:
        user = create_user(address, clean_name(data.get("name")), check_new_password(data.get("password")))
    existing = query("SELECT role FROM members WHERE home_id = ? AND user_id = ?", (invite["home_id"], user["id"]), one=True)
    # An invite never lowers someone's role.
    if not existing or (existing["role"] == "helper" and invite["role"] == "owner"):
        execute("INSERT OR REPLACE INTO members (home_id, user_id, role, added) VALUES (?, ?, ?, ?)",
                (invite["home_id"], user["id"], invite["role"], now()))
    execute("DELETE FROM invites WHERE token_hash = ?", (invite["token_hash"],))
    return start_session(user["id"])


def reset_row(token):
    if not token:
        return None
    row = query("SELECT resets.*, users.email, users.name FROM resets JOIN users ON users.id = resets.user_id "
                "WHERE resets.token_hash = ?", (digest(str(token)),), one=True)
    return row if row and row["expires"] > now() else None


def create_reset(user, home_id, member_id):
    """An owner makes a one-time link for someone in their home to choose a new password."""
    require_role(user, home_id, owner=True)
    if not role_in(home_id, member_id):
        raise HubError("That person isn't in this home.")
    token = secrets.token_urlsafe(24)
    execute("INSERT INTO resets (token_hash, user_id, expires, created_by) VALUES (?, ?, ?, ?)",
            (digest(token), member_id, now() + RESET_DAYS * 86400, user["id"]))
    return {"link": f"{public_url()}/reset/{token}", "days": RESET_DAYS}


def use_reset(data):
    """Set a new password from a reset link. It signs that person out everywhere else."""
    row = reset_row(data.get("reset"))
    if not row:
        raise HubError("This link has expired or was already used. Ask for a new one.")
    set_password(row["user_id"], check_new_password(data.get("password")))
    execute("DELETE FROM resets WHERE user_id = ?", (row["user_id"],))
    execute("DELETE FROM sessions WHERE user_id = ?", (row["user_id"],))
    return start_session(row["user_id"])


def change_password(user, data):
    if not password_ok(user, data.get("current")):
        time.sleep(0.5)
        raise HubError("Your current password isn't right.")
    set_password(user["id"], check_new_password(data.get("password")))


def ensure_owner(address, name, password):
    """The account set up on the setup page (or from environment variables). It can always sign in."""
    address = clean_email(address)
    user = user_by_email(address)
    if not user:
        if not password:
            raise HubError("Choose a password for your account.")
        return create_user(address, clean_name(name), check_new_password(password))
    if name:
        execute("UPDATE users SET name = ? WHERE id = ?", (clean_name(name), user["id"]))
    if password:
        set_password(user["id"], check_new_password(password))
    elif not user["pw_hash"]:
        raise HubError("Choose a password for your account.")
    return user


def session_user(cookie_header):
    token = None
    for part in (cookie_header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE:
            token = value
    if not token:
        return None
    return query("SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id "
                 "WHERE sessions.token_hash = ? AND sessions.expires > ?", (digest(token), now()), one=True)


# ----- Homes, members and invites -----

def role_in(home_id, user_id):
    row = query("SELECT role FROM members WHERE home_id = ? AND user_id = ?", (home_id, user_id), one=True)
    return row["role"] if row else None


def require_role(user, home_id, owner=False):
    role = role_in(home_id, user["id"])
    if not role:
        raise HubError("You don't have access to that home.", 404)
    if owner and role != "owner":
        raise HubError("Only the home's owners can do that.", 403)
    return role


def is_online(home_id, last_seen):
    link = links.get(home_id)
    if link and link.closed:
        return False
    return bool(link and link.waiting) or now() - last_seen < OFFLINE_SECONDS


def me(user):
    homes = query("SELECT homes.id, homes.name, homes.last_seen, members.role FROM members "
                  "JOIN homes ON homes.id = members.home_id WHERE members.user_id = ? ORDER BY homes.name", (user["id"],))
    return {
        "user": {"name": user["name"], "email": user["email"]},
        "homes": [{"id": h["id"], "name": h["name"], "role": h["role"], "online": is_online(h["id"], h["last_seen"]),
                   "lastSeen": h["last_seen"]} for h in homes],
    }


def home_details(user, home_id):
    require_role(user, home_id, owner=True)
    members = query("SELECT users.id, users.name, users.email, members.role, users.pw_hash != '' AS hasPassword "
                    "FROM members JOIN users ON users.id = members.user_id WHERE members.home_id = ? ORDER BY users.name", (home_id,))
    activity = query("SELECT activity.at, activity.text, activity.ok, users.name FROM activity JOIN users ON users.id = activity.user_id "
                     "WHERE activity.home_id = ? ORDER BY activity.id DESC LIMIT 50", (home_id,))
    return {
        "members": [dict(m) for m in members],
        "activity": [dict(a) for a in activity],
    }


def create_invite(user, home_id, role):
    require_role(user, home_id, owner=True)
    if role not in relay_rules.ROLES:
        raise HubError("Choose helper or owner.")
    token = secrets.token_urlsafe(24)
    execute("INSERT INTO invites (token_hash, home_id, role, expires, created_by) VALUES (?, ?, ?, ?, ?)",
            (digest(token), home_id, role, now() + INVITE_DAYS * 86400, user["id"]))
    return {"link": f"{public_url()}/invite/{token}", "days": INVITE_DAYS}


def remove_member(user, home_id, member_id):
    require_role(user, home_id, owner=True)
    owners = query("SELECT user_id FROM members WHERE home_id = ? AND role = 'owner'", (home_id,))
    if member_id in [o["user_id"] for o in owners] and len(owners) == 1:
        raise HubError("A home needs at least one owner. Make someone else an owner first, or remove the home.")
    execute("DELETE FROM members WHERE home_id = ? AND user_id = ?", (home_id, member_id))


def remove_home(user, home_id):
    """Forget a home. Its TV Remote is told it was removed the next time it asks for work."""
    require_role(user, home_id, owner=True)
    for table in ("members", "invites", "activity"):
        execute(f"DELETE FROM {table} WHERE home_id = ?", (home_id,))
    execute("DELETE FROM homes WHERE id = ?", (home_id,))


# ----- Linking a home with a code shown on its screen -----

def start_pairing():
    """Called by TV Remote at home. It gets a code to show, and a secret to collect the result with."""
    with pairings_lock:
        for pair_id in [p for p, item in pairings.items() if item["expires"] < now()]:
            del pairings[pair_id]
        if len(pairings) > 50:
            raise HubError("Too many homes are linking right now. Try again in a few minutes.", 429)
        codes = {item["code"] for item in pairings.values()}
        code = f"{secrets.randbelow(1000000):06d}"
        while code in codes:
            code = f"{secrets.randbelow(1000000):06d}"
        pair_id, secret = uuid.uuid4().hex, secrets.token_urlsafe(24)
        pairings[pair_id] = {"secret_hash": digest(secret), "code": code, "expires": now() + PAIR_MINUTES * 60, "claimed": None}
    return {"pairId": pair_id, "secret": secret, "code": code, "expiresIn": PAIR_MINUTES * 60}


def pairing_status(data):
    with pairings_lock:
        item = pairings.get(str(data.get("pairId")))
        if not item or not hmac.compare_digest(item["secret_hash"], digest(str(data.get("secret")))):
            raise HubError("Unknown linking request.", 404)
        if item["claimed"]:
            del pairings[str(data.get("pairId"))]  # The device token is handed over once.
            return {"status": "claimed", **item["claimed"]}
        if item["expires"] < now():
            del pairings[str(data.get("pairId"))]
            return {"status": "expired"}
    return {"status": "waiting"}


def claim_home(user, data):
    """A signed-in person enters the code from the home's screen and names the home. They become its owner."""
    code = re.sub(r"\D", "", str(data.get("code") or ""))
    name = re.sub(r"\s+", " ", str(data.get("name") or "")).strip()[:40]
    if not name:
        raise HubError("Name this home, like \"Aunt Mary's home\".")
    rate_limit(("claim", user["id"]), 10, 900)
    with pairings_lock:
        item = next((i for i in pairings.values() if i["code"] == code and not i["claimed"] and i["expires"] > now()), None)
        if not item:
            raise HubError("That code isn't right, or it expired. Check the code on the TV Remote screen.")
        home_id, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
        execute("INSERT INTO homes (id, name, token_hash, created_by, created) VALUES (?, ?, ?, ?, ?)",
                (home_id, name, digest(token), user["id"], now()))
        execute("INSERT INTO members (home_id, user_id, role, added) VALUES (?, ?, 'owner', ?)", (home_id, user["id"], now()))
        item["claimed"] = {"homeId": home_id, "homeName": name, "deviceToken": token}
    return {"id": home_id, "name": name}


# ----- Passing requests to homes -----

class HomeLink:
    """Requests waiting for one home, and the answers being waited for."""

    def __init__(self):
        self.queue = queue.Queue()
        self.pending = {}  # request id -> {"event", "reply", "delivered"}
        self.waiting = 0   # homes asking for work right now
        self.closed = False  # The home said it disconnected


def link_for(home_id):
    with links_lock:
        return links.setdefault(home_id, HomeLink())


def device_home(auth_header):
    token = (auth_header or "").removeprefix("Bearer ").strip()
    row = query("SELECT * FROM homes WHERE token_hash = ?", (digest(token),), one=True) if token else None
    if not row:
        raise HubError("This home isn't linked to the family hub.", 401)
    return row


def home_poll(home, wait=True):
    """TV Remote at home asks for work. Waits until a request arrives, or a while passes with none.

    wait=False answers at once, so a home that just connected knows straight away that it's linked.
    """
    execute("UPDATE homes SET last_seen = ? WHERE id = ?", (now(), home["id"]))
    link = link_for(home["id"])
    with links_lock:
        link.waiting += 1
        link.closed = False
    try:
        try:
            first = link.queue.get(timeout=POLL_SECONDS) if wait else link.queue.get_nowait()
        except queue.Empty:
            return {"requests": []}
        requests = [first]
        while len(requests) < 20:
            try:
                requests.append(link.queue.get_nowait())
            except queue.Empty:
                break
        # Drop requests nobody is waiting for any more, so a late button press never reaches the TV.
        requests = [r for r in requests if r and r["id"] in link.pending]
        for r in requests:
            link.pending[r["id"]]["delivered"] = True
        return {"requests": requests}
    finally:
        with links_lock:
            link.waiting -= 1
        execute("UPDATE homes SET last_seen = ? WHERE id = ?", (now(), home["id"]))


def home_goodbye(home):
    """The home turned remote access off. Mark it offline at once, and let anyone waiting know."""
    link = link_for(home["id"])
    with links_lock:
        link.closed = True
    for _ in range(link.waiting):
        link.queue.put(None)  # Ends any request for work still open from that home
    execute("UPDATE homes SET last_seen = 0 WHERE id = ?", (home["id"],))


def home_respond(home, data):
    link = link_for(home["id"])
    entry = link.pending.get(str(data.get("id")))
    if entry:
        entry["reply"] = data
        entry["event"].set()


def relay(user, home_id, method, path, body):
    """Send one remote-page request to a home and wait for its answer."""
    role = require_role(user, home_id)
    if not relay_rules.allowed(role, method, path):
        raise HubError("That can't be done remotely." if role == "owner" else "Only the home's owners can do that.", 403)
    home = query("SELECT * FROM homes WHERE id = ?", (home_id,), one=True)
    if not is_online(home_id, home["last_seen"]):
        raise HubError(f"{home['name']} isn't connected right now. TV Remote there may be closed, "
                       "or its computer or internet may be off.", 503)
    rate_limit(("relay", user["id"]), 300, 60)
    link = link_for(home_id)
    request_id = uuid.uuid4().hex
    entry = {"event": threading.Event(), "reply": None, "delivered": False}
    link.pending[request_id] = entry
    link.queue.put({"id": request_id, "method": method, "path": path, "body": body,
                    "user": user["name"], "role": role})
    offline = HubError(f"{home['name']} isn't connected right now. TV Remote there may be closed, "
                       "or its computer or internet may be off.", 503)
    try:
        deadline = now() + RELAY_SECONDS
        while not entry["event"].wait(1):
            # Don't keep someone waiting for a home that stopped asking for work before it got the request.
            if not entry["delivered"] and not is_online(home_id, query("SELECT last_seen FROM homes WHERE id = ?", (home_id,), one=True)["last_seen"]):
                raise offline
            if now() > deadline:
                raise HubError(f"{home['name']} didn't answer in time. Try again.", 504)
    finally:
        link.pending.pop(request_id, None)
    reply = entry["reply"]
    if method == "POST" and reply.get("activity"):
        execute("INSERT INTO activity (home_id, user_id, at, text, ok) VALUES (?, ?, ?, ?, ?)",
                (home_id, user["id"], now(), str(reply["activity"])[:200], int(200 <= int(reply.get("status", 500)) < 300)))
    try:
        content = base64.b64decode(reply.get("body") or "")
    except ValueError:
        raise HubError("The home sent an answer that couldn't be read.", 502)
    if len(content) > MAX_REPLY:
        raise HubError("The home's answer was too large.", 502)
    status = int(reply.get("status", 502))
    content_type = str(reply.get("contentType") or "application/json")
    if not re.fullmatch(r"(application/json|image/(png|jpeg|gif|webp))(;.*)?", content_type):
        content_type = "application/octet-stream"
    return status, content, content_type


# ----- HTTP -----

class Handler(BaseHTTPRequestHandler):
    server_version = "TVRemoteHub"

    def log_message(self, *args):
        pass

    def client_ip(self):
        # Behind Caddy, the visitor's address arrives in X-Forwarded-For. Only trust it from a private address.
        direct = self.client_address[0]
        forwarded = self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        try:
            return forwarded if forwarded and ipaddress.ip_address(direct).is_private else direct
        except ValueError:
            return direct

    def send_body(self, status, body, content_type, headers=None):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass

    def send_json(self, data, status=200, headers=None):
        self.send_body(status, json.dumps(data).encode(), "application/json", headers)

    def same_origin(self):
        """Block other websites from sending requests with a family member's sign-in."""
        origin = self.headers.get("Origin")
        return not origin or urllib.parse.urlsplit(origin).netloc == self.headers.get("Host")

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise HubError("That request is too large.", 413)
        return self.rfile.read(length)

    def read_json(self):
        try:
            data = json.loads(self.read_body() or b"{}")
        except ValueError:
            raise HubError("Bad request.")
        if not isinstance(data, dict):
            raise HubError("Bad request.")
        return data

    def user(self, required=True):
        user = session_user(self.headers.get("Cookie"))
        if required and not user:
            raise HubError("Sign in first.", 401)
        return user

    def cookie(self, token, days):
        flags = "; Secure" if public_url().startswith("https://") else ""
        return {"Set-Cookie": f"{COOKIE}={token}; Path=/; Max-Age={int(days * 86400)}; HttpOnly; SameSite=Lax{flags}"}

    def handle_error(self, err):
        self.send_json({"error": str(err)}, err.status)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        try:
            if url.path in ("/", "/sign-in") or url.path.startswith(("/invite/", "/reset/")):
                return self.send_body(200, PORTAL_PAGE.read_bytes(), "text/html; charset=utf-8")
            if url.path == "/hub/home/poll":
                return self.send_json(home_poll(device_home(self.headers.get("Authorization")), wait=url.query != "wait=0"))
            if url.path == "/api/me":
                return self.send_json(me(self.user()))
            if url.path.startswith("/api/invite-info/"):
                invite = invite_row(url.path.rsplit("/", 1)[1])
                home = invite and query("SELECT name FROM homes WHERE id = ?", (invite["home_id"],), one=True)
                return self.send_json({"home": home["name"]} if home else {"error": "This invite has expired or was already used."}, 200 if home else 404)
            if url.path.startswith("/api/reset-info/"):
                row = reset_row(url.path.rsplit("/", 1)[1])
                return self.send_json({"email": row["email"], "name": row["name"]} if row else
                                      {"error": "This link has expired or was already used. Ask for a new one."}, 200 if row else 404)
            match = re.fullmatch(r"/api/homes/([0-9a-f]{32})", url.path)
            if match:
                return self.send_json(home_details(self.user(), match.group(1)))
            match = re.fullmatch(r"/home/([0-9a-f]{32})(/.*)?", url.path)
            if match:
                return self.home_request("GET", match, url, b"")
            self.send_json({"error": "Not found"}, 404)
        except HubError as err:
            self.handle_error(err)

    def do_POST(self):
        url = urllib.parse.urlsplit(self.path)
        try:
            if url.path.startswith("/hub/"):
                return self.device_post(url.path)
            if not self.same_origin():
                raise HubError("Forbidden", 403)
            match = re.fullmatch(r"/home/([0-9a-f]{32})(/.*)?", url.path)
            if match:
                return self.home_request("POST", match, url, self.read_body())
            data = self.read_json()
            if url.path == "/api/sign-in":
                return self.send_json({"ok": True}, headers=self.cookie(sign_in(data, self.client_ip()), SESSION_DAYS))
            if url.path == "/api/invite/accept":
                return self.send_json({"ok": True}, headers=self.cookie(accept_invite(data, self.client_ip()), SESSION_DAYS))
            if url.path == "/api/reset":
                rate_limit(("reset", self.client_ip()), 20, 900)
                return self.send_json({"ok": True}, headers=self.cookie(use_reset(data), SESSION_DAYS))
            if url.path == "/api/sign-out":
                execute("DELETE FROM sessions WHERE token_hash = ?", (digest(self.session_token()),))
                return self.send_json({"ok": True}, headers=self.cookie("", 0))
            user = self.user()
            if url.path == "/api/homes/link":
                return self.send_json(claim_home(user, data))
            if url.path == "/api/password":
                rate_limit(("password", user["id"]), 10, 900)
                change_password(user, data)
                return self.send_json({"ok": True})
            match = re.fullmatch(r"/api/homes/([0-9a-f]{32})/(invite|reset-link|remove-member|remove)", url.path)
            if match:
                home_id, action = match.groups()
                if action == "invite":
                    return self.send_json(create_invite(user, home_id, data.get("role")))
                if action == "reset-link":
                    return self.send_json(create_reset(user, home_id, str(data.get("userId"))))
                if action == "remove-member":
                    remove_member(user, home_id, str(data.get("userId")))
                else:
                    remove_home(user, home_id)
                return self.send_json({"ok": True})
            self.send_json({"error": "Not found"}, 404)
        except HubError as err:
            self.handle_error(err)

    def session_token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE:
                return value
        return ""

    def device_post(self, path):
        """Requests from TV Remote at a home. These use the home's own token, not a person's sign-in."""
        data = self.read_json()
        if path == "/hub/pair/start":
            rate_limit(("pair", self.client_ip()), 10, 900)
            return self.send_json(start_pairing())
        if path == "/hub/pair/status":
            return self.send_json(pairing_status(data))
        if path == "/hub/home/respond":
            home_respond(device_home(self.headers.get("Authorization")), data)
            return self.send_json({"ok": True})
        if path == "/hub/home/goodbye":
            home_goodbye(device_home(self.headers.get("Authorization")))
            return self.send_json({"ok": True})
        self.send_json({"error": "Not found"}, 404)

    def home_request(self, method, match, url, body):
        """/home/<id>/ is the remote page for that home, and /home/<id>/api/... goes to the home itself."""
        home_id, rest = match.group(1), match.group(2) or ""
        user = self.user(required=method == "POST" or rest.startswith("/api/"))
        if not rest.startswith("/api/"):
            if not user:
                return self.send_body(303, b"", "text/plain", {"Location": "/sign-in"})
            if rest not in ("", "/"):
                return self.send_json({"error": "Not found"}, 404)
            if rest == "":
                return self.send_body(301, b"", "text/plain", {"Location": f"/home/{home_id}/"})
            require_role(user, home_id)
            return self.send_body(200, REMOTE_PAGE.read_bytes(), "text/html; charset=utf-8")
        path = rest + (f"?{url.query}" if url.query else "")
        status, content, content_type = relay(user, home_id, method, path, body.decode("utf-8", "replace") if body else None)
        self.send_body(status, content, content_type)


# ----- Setup page: the hub's own settings, entered on the home network -----

NAME_PATTERN = re.compile(r"^(?=.{4,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,}$")
setup_lock = threading.Lock()


def setup_password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200000).hex()


def check_setup_password(saved, password):
    if not saved.get("setupHash"):
        return
    if not isinstance(password, str) or not hmac.compare_digest(setup_password_hash(password, saved["setupSalt"]), saved["setupHash"]):
        time.sleep(1)  # Slows down guessing.
        raise HubError("That setup password isn't right.", 403)


def owner_email(saved):
    # Settings saved before passwords listed admin emails. The first one is the owner.
    return saved.get("ownerEmail") or (saved.get("adminEmails") or [""])[0]


def setup_status():
    saved = saved_settings()
    owner = user_by_email(owner_email(saved)) if owner_email(saved) else None
    return {
        "configured": bool(saved.get("name")),
        "name": saved.get("name", ""),
        "port": saved.get("port", 8443),
        "ownerEmail": owner_email(saved),
        "ownerName": owner["name"] if owner else "",
        "ownerHasPassword": bool(owner and owner["pw_hash"]),
        "publicUrl": public_url() if saved.get("name") else "",
        "https": caddy.status(),
    }


def save_setup(data, ip):
    """Check and save the hub's settings, then restart HTTPS for the (maybe new) address."""
    rate_limit(("setup", ip), 10, 900)
    with setup_lock:
        saved = saved_settings()
        check_setup_password(saved, data.get("currentPassword"))
        name = str(data.get("name") or "").strip().lower()
        name = re.sub(r"^https?://", "", name).split("/")[0].split(":")[0]
        if not NAME_PATTERN.match(name):
            raise HubError("Enter your No-IP name, like yourname.ddns.net.")
        try:
            port = int(data.get("port") or 8443)
        except (TypeError, ValueError):
            port = 0
        if not 1 <= port <= 65535 or port == 80:
            raise HubError("Enter the port family members will use, like 8443.")
        new_password = str(data.get("newPassword") or "")
        if not saved.get("setupHash") and len(new_password) < 8:
            raise HubError("Choose a setup password of at least 8 characters. It protects these settings.")
        if new_password and len(new_password) < 8:
            raise HubError("The new setup password needs at least 8 characters.")
        # Your account on the hub. A new password here also resets a forgotten one.
        owner = ensure_owner(data.get("ownerEmail"), data.get("ownerName"), data.get("ownerPassword") or "")
        settings = {k: v for k, v in saved.items() if k not in ("adminEmails", "gmail", "appPassword")}
        settings.update(name=name, port=port, ownerEmail=owner["email"])
        if new_password:
            settings["setupSalt"] = secrets.token_hex(16)
            settings["setupHash"] = setup_password_hash(new_password, settings["setupSalt"])
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        temp = SETTINGS_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        os.chmod(temp, 0o600)
        os.replace(temp, SETTINGS_FILE)
    caddy.restart()
    return setup_status()


class Caddy:
    """Runs Caddy, which handles HTTPS and certificates, for the address in the settings."""

    def __init__(self):
        self.process = None
        self.lock = threading.Lock()
        self.problem = ""
        self.stopping = False

    def write_config(self):
        saved = saved_settings()
        CADDYFILE.write_text("{\n\tadmin off\n}\n\n"
                             f"{saved['name']}:{saved['port']} {{\n\treverse_proxy 127.0.0.1:{HUB_PORT}\n}}\n", encoding="utf-8")

    def restart(self):
        if not MANAGE_CADDY or not saved_settings().get("name"):
            return
        with self.lock:
            self.stop()
            self.write_config()
            self.problem, self.stopping = "", False
            self.process = subprocess.Popen(["caddy", "run", "--config", str(CADDYFILE), "--adapter", "caddyfile"],
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            threading.Thread(target=self.watch, args=(self.process,), daemon=True).start()

    def stop(self):
        if self.process and self.process.poll() is None:
            self.stopping = True
            self.process.terminate()
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def watch(self, process):
        """Pass Caddy's log through to the container log, and note certificate news for the setup page."""
        for line in process.stdout:
            print(line, end="", flush=True)
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if "certificate obtained successfully" in str(entry.get("msg", "")):
                self.problem = ""
            elif entry.get("level") == "error" and process is self.process:
                self.problem = str(entry.get("error") or entry.get("msg", ""))[:400]
        if process is self.process and not self.stopping:
            self.problem = self.problem or "HTTPS stopped unexpectedly. Restarting it."
            time.sleep(10)
            if process is self.process and not self.stopping:
                self.restart()

    def status(self):
        if not MANAGE_CADDY:
            return {"managed": False}
        running = bool(self.process and self.process.poll() is None)
        name = saved_settings().get("name", "")
        # Caddy keeps certificates under the data folder, by the name they're for.
        certificate = bool(name) and any(DATA_DIR.glob(f"caddy/certificates/*/{name}/{name}.crt"))
        return {"managed": True, "running": running, "certificate": certificate, "problem": self.problem}


caddy = Caddy()
HUB_PORT = int(os.environ.get("HUB_PORT", "8000"))


class SetupHandler(BaseHTTPRequestHandler):
    """The setup page. It's for the home network only: its port is never forwarded on the router."""
    server_version = "TVRemoteHubSetup"

    def log_message(self, *args):
        pass

    def send_json(self, data, status=200):
        Handler.send_json(self, data, status)

    def send_body(self, status, body, content_type, headers=None):
        Handler.send_body(self, status, body, content_type, headers)

    def local(self):
        try:
            return ipaddress.ip_address(self.client_address[0]).is_private
        except ValueError:
            return False

    def do_GET(self):
        if not self.local():
            return self.send_json({"error": "The setup page only works on the home network."}, 403)
        if self.path == "/":
            return self.send_body(200, SETUP_PAGE.read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/status":
            return self.send_json(setup_status())
        self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        origin = self.headers.get("Origin")
        if not self.local() or (origin and urllib.parse.urlsplit(origin).netloc != self.headers.get("Host")):
            return self.send_json({"error": "Forbidden"}, 403)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(min(length, MAX_BODY)) or b"{}")
            if not isinstance(data, dict):
                raise HubError("Bad request.")
            if self.path == "/api/save":
                return self.send_json(save_setup(data, self.client_address[0]))
            self.send_json({"error": "Not found"}, 404)
        except ValueError:
            self.send_json({"error": "Bad request."}, 400)
        except HubError as err:
            self.send_json({"error": str(err)}, err.status)


class Server(ThreadingHTTPServer):
    allow_reuse_address = os.name != "nt"
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser(description="TV Remote family hub")
    parser.add_argument("--port", type=int, default=int(os.environ.get("HUB_PORT", "8000")))
    parser.add_argument("--host", default=os.environ.get("HUB_HOST", "0.0.0.0"))
    args = parser.parse_args()
    global HUB_PORT
    HUB_PORT = args.port
    init_db()
    # Without the setup page (Compose or testing), the owner's account comes from environment variables.
    if os.environ.get("OWNER_EMAIL"):
        ensure_owner(os.environ["OWNER_EMAIL"], os.environ.get("OWNER_NAME", "Owner"), os.environ.get("OWNER_PASSWORD", ""))
    server = Server((args.host, args.port), Handler)
    configured = bool(saved_settings().get("name")) or not SETUP_PORT
    if SETUP_PORT:
        setup = Server(("0.0.0.0", SETUP_PORT), SetupHandler)
        threading.Thread(target=setup.serve_forever, daemon=True).start()
    if configured:
        print(f"TV Remote family hub is running. Public address: {public_url()}", flush=True)
    else:
        print(f"TV Remote family hub is waiting to be set up. On your home network, open http://<NAS address>:{SETUP_PORT}",
              flush=True)
    caddy.restart()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        caddy.stop()


if __name__ == "__main__":
    main()
