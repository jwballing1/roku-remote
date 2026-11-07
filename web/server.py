#!/usr/bin/env python3
"""Local web remote for Roku devices. Run: python server.py [--port 8000]

Browsers can't do SSDP discovery or read ECP responses, so this server serves
the page and relays its requests to the TV on port 8060.
"""
import argparse
import datetime
import email.message
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import re
import secrets
import select
import smtplib
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The packaged Windows app unpacks to a temp folder, so its saved TV goes in AppData.
PAGE = Path(getattr(sys, "_MEIPASS", HERE)) / "index.html"
STATE_FILE = (Path(os.environ["APPDATA"]) / "TV Remote" if getattr(sys, "frozen", False) else HERE) / "tv.json"
CHANNELS_FILE = STATE_FILE.with_name("channels.json")
SETTINGS_FILE = STATE_FILE.with_name("settings.json")
settings_lock = threading.Lock()
GLOBAL_POSTS = {"/api/settings", "/api/lock", "/api/alerts", "/api/alerts/test", "/api/tvs/remove"}
EMAIL_PATTERN = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
ALERT_REPEAT_SECONDS = 30 * 60  # One email per problem per half hour, so a bad day doesn't flood inboxes.
VIDEO_CHECK_SECONDS = 20
recent_alerts = {}
last_alert_status = ""
EXPORT_TYPE = "tv-remote-channels"
MAX_IMPORT = 200
MAX_BODY = 2 * 1024 * 1024
ECP_PORT = 8060
MIN_STEP_MS = 300  # Don't replay presses faster than the app's menus can react.
MAX_STEP_MS = 60000
MAX_OPEN_MS = 120000
MAX_STEPS = 300
channels_lock = threading.Lock()
running = {}  # ip -> threading.Event that stops the sequence playing on that device
IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}

# Ignore any system proxy: the TV is always on the local network.
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class TvError(Exception):
    """A problem to show the user. `unreachable` means no answer from the TV."""

    def __init__(self, message, unreachable=False):
        super().__init__(message)
        self.unreachable = unreachable


def check_ip(ip):
    """Only relay to private IPv4 addresses, so this can't reach the internet."""
    try:
        addr = ipaddress.ip_address(str(ip))
    except ValueError:
        raise TvError("That doesn't look like an IP address.")
    if addr.version != 4 or not addr.is_private:
        raise TvError("The TV's address must be a private IPv4 address, like 192.168.1.50.")
    return str(addr)


def ecp(ip, path, method="GET", timeout=4):
    url = f"http://{check_ip(ip)}:{ECP_PORT}{path}"
    request = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.read(), response.headers.get_content_type()
    except urllib.error.HTTPError as err:
        if err.code == 403:
            raise TvError(
                "The TV refused this command. On the TV, open Settings > System > "
                "Advanced system settings > Control by mobile apps and choose Permissive."
            )
        raise TvError(f"The TV returned an error (HTTP {err.code}).")
    except (OSError, http.client.HTTPException):
        raise TvError(f"Couldn't reach the TV at {ip}.", unreachable=True)


def device_info(ip, timeout=4):
    body, _ = ecp(ip, "/query/device-info", timeout=timeout)
    root = ET.fromstring(body)
    get = lambda tag: (root.findtext(tag) or "").strip()
    model = get("friendly-model-name") or get("model-name")
    # Unnamed devices report a generic name, so the location set during setup
    # (like "Family room") is often the only thing that tells them apart.
    user_name = get("user-device-name")
    generic = {"", model, f"Roku {model}", get("default-device-name")}
    location = get("user-device-location")
    location = location[:1].upper() + location[1:]
    if user_name not in generic:
        name = f"{user_name} ({location})" if location else user_name
    else:
        name = location or user_name or get("friendly-device-name") or model or "Roku"
    macs = [m for m in (get("ethernet-mac"), get("wifi-mac")) if m]
    return {
        "ip": ip,
        "key": macs[0] if macs else ip,  # Identifies the device for its channels, even if its IP changes.
        "name": name,
        "model": model,
        "isTv": get("is-tv") == "true",
        "powerMode": get("power-mode"),
        "macs": macs,
    }


def local_ips():
    """This PC's IPv4 addresses. PCs with VPN or virtual adapters have several."""
    ips = set()
    try:
        ips.update(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))  # No traffic is sent; this just picks a route.
        ips.add(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()
    return sorted(ip for ip in ips if not ip.startswith("127."))


def discover(wait=2.5):
    """Send an SSDP search out of every network adapter and collect the replies."""
    message = (
        "M-SEARCH * HTTP/1.1\r\nHost: 239.255.255.250:1900\r\n"
        'Man: "ssdp:discover"\r\nST: roku:ecp\r\nMX: 2\r\n\r\n'
    ).encode()
    sockets = []
    for local in local_ips():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local))
            sock.bind((local, 0))
            # Sent twice because these network messages can get lost, and a stick can miss one.
            sock.sendto(message, ("239.255.255.250", 1900))
            sock.sendto(message, ("239.255.255.250", 1900))
            sockets.append(sock)
        except OSError:
            sock.close()

    found = set()
    deadline = time.monotonic() + wait
    try:
        while sockets and (remaining := deadline - time.monotonic()) > 0:
            ready, _, _ = select.select(sockets, [], [], remaining)
            for sock in ready:
                try:
                    data = sock.recv(2048).decode("latin-1")
                except OSError:
                    continue
                match = re.search(r"^LOCATION:\s*http://([\d.]+):\d+", data, re.I | re.M)
                if match:
                    found.add(match.group(1))
    finally:
        for sock in sockets:
            sock.close()

    devices = []
    for ip in sorted(found):
        try:
            devices.append(device_info(ip))
        except (TvError, ET.ParseError):
            pass
    return devices


def send_wake_packets(ip, macs):
    """Wake-on-LAN: send magic packets for the TV's MAC addresses on the local network."""
    packets = []
    for mac in macs:
        digits = re.sub(r"[^0-9a-fA-F]", "", mac)
        if len(digits) == 12:
            packets.append(b"\xff" * 6 + bytes.fromhex(digits) * 16)
    subnet_broadcast = ip.rsplit(".", 1)[0] + ".255"
    for local in local_ips() or [""]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind((local, 0))
            for packet in packets:
                for target in ("255.255.255.255", subnet_broadcast):
                    try:
                        sock.sendto(packet, (target, 9))
                    except OSError:
                        pass
        except OSError:
            pass
        finally:
            sock.close()


def press(ip, key, timeout=4):
    if not isinstance(key, str) or not 0 < len(key) <= 64:
        raise TvError("Invalid key.")
    ecp(ip, "/keypress/" + urllib.parse.quote(key, safe=""), "POST", timeout)


def power_on(ip):
    """Try PowerOn. If the TV is in deep standby, wake it and keep retrying."""
    ip = check_ip(ip)
    try:
        press(ip, "PowerOn")
        return {"woke": False}
    except TvError as err:
        if not err.unreachable:
            raise
    saved = load_saved()
    macs = saved.get("macs", []) if saved.get("ip") == ip else []
    if not macs:
        raise TvError("The TV isn't responding, and its MAC address isn't saved, so it can't be woken.")
    for _ in range(6):
        send_wake_packets(ip, macs)
        time.sleep(2)
        try:
            press(ip, "PowerOn", timeout=2)
            return {"woke": True}
        except TvError as err:
            if not err.unreachable:
                raise
    raise TvError(
        "The TV didn't wake up. Check that Fast TV Start is on under Settings > System > Power, "
        "then try again."
    )


def load_saved():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_tv(info):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(info, indent=2), encoding="utf-8")
    os.replace(temp, STATE_FILE)


def load_settings():
    settings = {
        "channelsOnly": False,
        "tvs": [],
        "removedTvs": [],
        "hiddenApps": {},
        "locked": False,
        "pinHash": "",
        "pinSalt": "",
        "alerts": {"provider": "gmail", "emails": [], "host": "", "port": 587, "security": "starttls", "username": "", "password": "", "from": ""},
    }
    try:
        settings.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return settings


def write_settings(settings):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = SETTINGS_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    os.replace(temp, SETTINGS_FILE)


def public_settings():
    """Settings for the page. The PIN hash and email password never leave the server."""
    settings = load_settings()
    settings.pop("pinHash", None)
    settings.pop("pinSalt", None)
    alerts = dict(settings["alerts"])
    alerts["hasPassword"] = bool(alerts.pop("password", ""))
    alerts["lastStatus"] = last_alert_status
    settings["alerts"] = alerts
    return settings


def pin_hash(pin, salt):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 200000).hex()


def check_pin_format(pin):
    if not isinstance(pin, str) or not re.fullmatch(r"\d{4,12}", pin):
        raise TvError("The PIN must be 4 to 12 digits.")


def verify_pin(pin):
    settings = load_settings()
    if not isinstance(pin, str) or not settings["pinHash"] or not hmac.compare_digest(pin_hash(pin, settings["pinSalt"]), settings["pinHash"]):
        time.sleep(0.5)  # Slows down repeated guessing.
        raise TvError("That PIN isn't right.")


def lock_action(action, pin):
    if action == "verify":
        verify_pin(pin)
        return
    with settings_lock:
        settings = load_settings()
        if action == "lock":
            check_pin_format(pin)
            if settings["locked"]:
                raise TvError("The lock is already on.")
            salt = secrets.token_hex(16)
            settings.update(locked=True, pinSalt=salt, pinHash=pin_hash(pin, salt))
        elif action == "off":
            verify_pin(pin)
            settings.update(locked=False, pinSalt="", pinHash="")
        else:
            raise TvError("Invalid lock request.")
        write_settings(settings)


def save_alerts(data):
    previous = load_settings()["alerts"]
    emails = data.get("emails")
    if not isinstance(emails, list) or len(emails) > 20:
        raise TvError("Enter up to 20 email addresses.")
    emails = [str(address).strip() for address in emails if str(address).strip()]
    bad = [address for address in emails if not EMAIL_PATTERN.match(address)]
    if bad:
        raise TvError(f"This doesn't look like an email address: {bad[0]}")
    provider = "gmail" if data.get("provider", "gmail") == "gmail" else "other"
    username = str(data.get("username", "")).strip()
    password = str(data.get("password") or "")
    if provider == "gmail":
        if username and not EMAIL_PATTERN.match(username):
            raise TvError("The Gmail address doesn't look like an email address.")
        host, port, security, sender = "smtp.gmail.com", 587, "starttls", username
        password = re.sub(r"\s+", "", password)  # Google displays app passwords with spaces.
    else:
        host = str(data.get("host", "")).strip()
        security = data.get("security", "starttls")
        # Unencrypted sending is only for a mail server on this same machine.
        if security not in ("starttls", "ssl") and not (security == "none" and host in ("localhost", "127.0.0.1")):
            raise TvError("Choose a secure connection type.")
        try:
            port = int(data.get("port", 587))
        except (TypeError, ValueError):
            raise TvError("The mail server port must be a number.")
        if not 0 < port < 65536:
            raise TvError("The mail server port must be a number.")
        sender = str(data.get("from", "")).strip() or username
        if sender and not EMAIL_PATTERN.match(sender):
            raise TvError("The sending address doesn't look like an email address.")
    # A blank password keeps the saved one, unless the sending account changed.
    if not password and username == previous.get("username") and provider == previous.get("provider"):
        password = previous.get("password", "")
    with settings_lock:
        settings = load_settings()
        settings["alerts"] = {
            "provider": provider, "emails": emails, "host": host, "port": port, "security": security,
            "username": username, "password": password, "from": sender,
        }
        write_settings(settings)


def send_email(alerts, subject, body):
    if not alerts["emails"] or not alerts["host"] or not alerts["from"]:
        raise TvError("Add at least one email address, a mail server and a sending address first.")
    message = email.message.EmailMessage()
    message["Subject"] = re.sub(r"[\r\n]+", " ", subject)[:200]
    message["From"] = alerts["from"]
    message["To"] = ", ".join(alerts["emails"])
    message.set_content(body)
    try:
        if alerts["security"] == "ssl":
            client = smtplib.SMTP_SSL(alerts["host"], alerts["port"], timeout=20, context=ssl.create_default_context())
        else:
            client = smtplib.SMTP(alerts["host"], alerts["port"], timeout=20)
        with client:
            if alerts["security"] == "starttls":
                client.starttls(context=ssl.create_default_context())
            if alerts["username"] and alerts["security"] != "none":
                client.login(alerts["username"], alerts["password"])
            client.send_message(message)
    except (OSError, smtplib.SMTPException) as err:
        raise TvError(f"The email couldn't be sent: {err}")


def report_issue(key, tv_name, problem, details):
    """Email the family about a problem, in the background, without holding up the remote."""
    alerts = load_settings()["alerts"]
    if not alerts["emails"] or not alerts["host"]:
        return
    now = time.time()
    alert_key = (key, problem)
    if now - recent_alerts.get(alert_key, 0) < ALERT_REPEAT_SECONDS:
        return
    recent_alerts[alert_key] = now
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    def send():
        global last_alert_status
        body = f"TV: {tv_name}\nTime: {stamp}\nProblem: {problem}\n\n{details}\n"
        try:
            send_email(alerts, f"TV Remote problem: {problem} ({tv_name})", body)
            last_alert_status = f"Last alert sent {stamp}: {problem} ({tv_name})."
        except TvError as err:
            last_alert_status = f"Last alert failed {stamp}: {err}"

    threading.Thread(target=send, daemon=True).start()


def set_channels_only(enabled):
    with settings_lock:
        settings = load_settings()
        settings["channelsOnly"] = bool(enabled)
        write_settings(settings)


def hidden_apps(ip):
    return load_settings()["hiddenApps"].get(device_key(ip), [])


def set_hidden_apps(ip, hidden):
    if not isinstance(hidden, list) or len(hidden) > 500 or not all(isinstance(app_id, str) for app_id in hidden):
        raise TvError("Invalid list of apps.")
    key = device_key(ip)
    with settings_lock:
        settings = load_settings()
        settings["hiddenApps"][key] = hidden
        write_settings(settings)


def remove_tv(key):
    """Forget a TV, its channels and app choices. Searches won't bring it back, only choosing it again."""
    with settings_lock:
        settings = load_settings()
        settings["tvs"] = [tv for tv in settings["tvs"] if tv["key"] != key]
        if key not in settings["removedTvs"]:
            settings["removedTvs"].append(key)
        settings["hiddenApps"].pop(key, None)
        write_settings(settings)
        remaining = settings["tvs"]
    with channels_lock:
        channels = load_channels()
        if channels.pop(key, None) is not None:
            write_channels(channels)
    saved = load_saved()
    if saved.get("key") == key or (saved.get("macs") and saved["macs"][0] == key):
        if remaining:
            save_tv({"ip": remaining[0]["ip"], "key": remaining[0]["key"], "name": remaining[0]["name"], "macs": []})
        else:
            try:
                STATE_FILE.unlink()
            except OSError:
                pass


def remember_tvs(devices, chosen=False):
    """Keep every TV seen so far, so its button still shows when a search misses it.

    A removed TV is only remembered again when someone chooses it (chosen=True).
    """
    with settings_lock:
        settings = load_settings()
        if chosen:
            settings["removedTvs"] = [k for k in settings["removedTvs"] if k not in {d["key"] for d in devices}]
        devices = [d for d in devices if d["key"] not in settings["removedTvs"]]
        known = {tv["key"]: tv for tv in settings["tvs"]}
        new_devices = [device for device in devices if device["key"] not in known]
        for device in devices:
            known[device["key"]] = {"key": device["key"], "ip": device["ip"], "name": device["name"]}
        settings["tvs"] = list(known.values())
        write_settings(settings)
    for device in new_devices:
        add_all_tv_channels(device)


def device_key(ip):
    return device_info(check_ip(ip))["key"]


def channels_for(ip):
    """A device's channels, each listing every device that shares it."""
    channels = load_channels()
    return [
        {**channel, "devices": [key for key, items in channels.items() if any(c["id"] == channel["id"] for c in items)]}
        for channel in channels.get(device_key(ip), [])
    ]


def load_channels():
    try:
        return json.loads(CHANNELS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_channels(channels):
    CHANNELS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = CHANNELS_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(channels, indent=2), encoding="utf-8")
    os.replace(temp, CHANNELS_FILE)


def clean_channel(data):
    """Validate a channel sent by the page, keeping only the expected fields."""
    try:
        name = str(data["name"]).strip()[:40]
        app_id = str(data["appId"])[:64]
        raw_steps = data["steps"]
        if not name or not app_id or not isinstance(raw_steps, list) or len(raw_steps) > MAX_STEPS:
            raise ValueError
        steps = []
        for step in raw_steps:
            key = step["key"]
            if not isinstance(key, str) or not 0 < len(key) <= 64:
                raise ValueError
            steps.append({"key": key, "delayMs": max(0, min(int(step["delayMs"]), MAX_STEP_MS))})
        open_delay = max(0, min(int(data.get("openDelayMs", 0)), MAX_OPEN_MS))
    except (KeyError, TypeError, ValueError):
        raise TvError("That channel couldn't be saved because its details are invalid.")
    return {
        "id": str(data.get("id") or uuid.uuid4().hex)[:64],
        "name": name,
        "appId": app_id,
        "appName": str(data.get("appName", ""))[:80],
        "openDelayMs": open_delay,
        "resetFirst": bool(data.get("resetFirst")),
        "allTvs": bool(data.get("allTvs")),
        "expectVideo": bool(data.get("expectVideo", True)),
        "isShortcut": bool(data.get("isShortcut")),
        "steps": steps,
    }


def tv_has_app(ip, app_id):
    """False only when the TV answers and doesn't list the app. A TV that's off gets the benefit of the doubt."""
    try:
        return app_id in {app["id"] for app in apps(ip)}
    except (TvError, ET.ParseError):
        return True


def save_channel(ip, data, tvs=None):
    """Save a channel. tvs is "all", a list of device keys, or None to leave its TVs as they are."""
    key, channel = device_key(ip), clean_channel(data)
    keep, skipped = None, []
    if tvs is not None:
        if tvs != "all" and not (isinstance(tvs, list) and all(isinstance(k, str) for k in tvs)):
            raise TvError("Invalid list of TVs.")
        keep = {key}
        for tv in load_settings()["tvs"]:
            if tv["key"] != key and (tvs == "all" or tv["key"] in tvs):
                if tv_has_app(tv["ip"], channel["appId"]):
                    keep.add(tv["key"])
                else:
                    skipped.append(tv["name"])
    with channels_lock:
        channels = load_channels()
        previous = next((c for items in channels.values() for c in items if c["id"] == channel["id"]), None)
        channel["allTvs"] = tvs == "all" if tvs is not None else bool(previous and previous.get("allTvs"))
        # A channel shared between devices is updated on all of them.
        for items in channels.values():
            for index, existing in enumerate(items):
                if existing["id"] == channel["id"]:
                    items[index] = channel
        for target in keep or {key}:
            device = channels.setdefault(target, [])
            if not any(c["id"] == channel["id"] for c in device):
                device.append(channel)
        if keep is not None:
            for target in channels:
                if target not in keep:
                    channels[target] = [c for c in channels[target] if c["id"] != channel["id"]]
        write_channels(channels)
    return {**channel, "skipped": skipped}


def add_all_tv_channels(device):
    """Give a newly found TV the channels marked for all TVs, if it has their apps."""
    pending = [c for c in {c["id"]: c for items in load_channels().values() for c in items}.values() if c.get("allTvs")]
    pending = [c for c in pending if tv_has_app(device["ip"], c["appId"])]
    if not pending:
        return
    with channels_lock:
        channels = load_channels()
        items = channels.setdefault(device["key"], [])
        items.extend(c for c in pending if not any(existing["id"] == c["id"] for existing in items))
        write_channels(channels)


def export_channels(ip):
    info = device_info(check_ip(ip))
    channels = [clean_channel(c) for c in load_channels().get(info["key"], [])]
    data = {"type": EXPORT_TYPE, "version": 1, "exportedFrom": info["name"], "channels": channels}
    name = re.sub(r"[^A-Za-z0-9]+", "-", info["name"]).strip("-") or "TV"
    return json.dumps(data, indent=2).encode(), f"tv-remote-channels-{name}.json"


def import_channels(ip, data):
    """Merge an exported file into one device: add new channels and update matching ones."""
    if not isinstance(data, dict) or data.get("type") != EXPORT_TYPE or not isinstance(data.get("channels"), list):
        raise TvError("That file isn't a channel export from this app, so nothing was changed.")
    if len(data["channels"]) > MAX_IMPORT:
        raise TvError(f"That file has more than {MAX_IMPORT} channels, so nothing was changed.")
    incoming = []
    for item in data["channels"]:
        if not isinstance(item, dict) or not item.get("id"):
            raise TvError("A channel in that file is invalid, so nothing was changed.")
        incoming.append(clean_channel(item))
    key = device_key(ip)
    try:
        installed = {app["id"] for app in apps(ip)}
    except (TvError, ET.ParseError):
        installed = None
    added = updated = 0
    with channels_lock:
        channels = load_channels()
        device = channels.setdefault(key, [])
        for channel in incoming:
            if any(c["id"] == channel["id"] for c in device):
                updated += 1
            else:
                device.append(channel)
                added += 1
            # A channel shared between devices is updated on all of them.
            for items in channels.values():
                for index, existing in enumerate(items):
                    if existing["id"] == channel["id"]:
                        items[index] = channel
        write_channels(channels)
    for tv in load_settings()["tvs"]:
        if tv["key"] != key:
            add_all_tv_channels(tv)
    missing = sorted({c["appName"] or c["appId"] for c in incoming if installed is not None and c["appId"] not in installed})
    return {"added": added, "updated": updated, "missingApps": missing}


def delete_channel(ip, channel_id):
    key = device_key(ip)
    with channels_lock:
        channels = load_channels()
        channels[key] = [c for c in channels.get(key, []) if c["id"] != channel_id]
        # Taken off one TV, it's no longer on all of them.
        for items in channels.values():
            for channel in items:
                if channel["id"] == channel_id:
                    channel["allTvs"] = False
        write_channels(channels)


def reorder_channels(ip, ids):
    """Put one device's channels in the given order. Any it didn't mention stay at the end."""
    if not isinstance(ids, list) or len(ids) > 500:
        raise TvError("Invalid channel order.")
    key = device_key(ip)
    with channels_lock:
        channels = load_channels()
        position = {channel_id: index for index, channel_id in enumerate(ids)}
        channels[key] = sorted(channels.get(key, []), key=lambda c: position.get(c["id"], len(ids)))
        write_channels(channels)


def open_app_fresh(ip, app_id, cancel, reset=False, wait_ms=0):
    """Go home first, so recording and playback both start from the same place.

    Apps resume where they were left. With reset on, a placeholder link makes an
    app like Frndly go to its home screen and show a "not available" pop-up. OK
    clears the pop-up, and Up (which stops at the top) puts the highlight on the
    app's top menu, wherever it landed.
    """
    press(ip, "Home")
    if cancel.wait(1.5):
        return False
    path = "/launch/" + urllib.parse.quote(str(app_id), safe="")
    if not reset:
        ecp(ip, path, "POST")
        return True
    ecp(ip, path + "?contentId=reset&mediaType=live", "POST")
    if cancel.wait(wait_ms / 1000):
        return False
    press(ip, "Select")
    if cancel.wait(1):
        return False
    for _ in range(4):
        press(ip, "Up")
        if cancel.wait(0.5):
            return False
    return True


def start_sequence(ip):
    cancel = threading.Event()
    previous = running.get(ip)
    if previous:
        previous.set()
    running[ip] = cancel
    return cancel


def finish_sequence(ip, cancel):
    if running.get(ip) is cancel:
        del running[ip]


def play_channel(ip, channel_id):
    """Open the app, then replay the recorded presses with their recorded timing."""
    ip = check_ip(ip)
    info = device_info(ip)
    channel = next((c for c in load_channels().get(info["key"], []) if c["id"] == channel_id), None)
    if not channel:
        raise TvError("That channel no longer exists.")
    cancel = start_sequence(ip)
    try:
        result = run_channel(ip, channel, cancel)
    except TvError as err:
        report_issue(info["key"], info["name"], f"{channel['name']} failed", f"The button couldn't finish.\n{err}")
        raise
    finally:
        finish_sequence(ip, cancel)
    if result.get("verified") is False:
        report_issue(
            info["key"], info["name"], f"{channel['name']} didn't start",
            f"The presses were sent, but no video started within {VIDEO_CHECK_SECONDS} seconds.\n"
            f"The app ({channel['appName']}) may have changed, or a prompt may be on screen.",
        )
    return result


def video_playing(ip, cancel):
    """True once video plays, False if it never does, None if stopped or the TV can't say."""
    deadline = time.monotonic() + VIDEO_CHECK_SECONDS
    while True:
        try:
            body, _ = ecp(ip, "/query/media-player", timeout=4)
            player = ET.fromstring(body)
        except (TvError, ET.ParseError):
            return None
        if player.get("state") in ("play", "buffer"):
            return True
        if time.monotonic() >= deadline:
            return False
        if cancel.wait(2):
            return None


def run_channel(ip, channel, cancel):
    reset = channel.get("resetFirst", False)
    if not open_app_fresh(ip, channel["appId"], cancel, reset, channel.get("openDelayMs", 0)):
        return {"stopped": True}
    # Channels saved before this setting existed keep their app wait in the first step's delay.
    if not reset and channel["steps"] and cancel.wait(channel.get("openDelayMs", 0) / 1000):
        return {"stopped": True}
    for step in channel["steps"]:
        if cancel.wait(max(step["delayMs"], MIN_STEP_MS) / 1000):
            return {"stopped": True}
        press(ip, step["key"])
    if not channel.get("expectVideo", True):
        return {"stopped": False, "verified": None}
    verified = video_playing(ip, cancel)
    return {"stopped": cancel.is_set(), "verified": verified}


def stop_sequence(ip):
    cancel = running.get(check_ip(ip))
    if cancel:
        cancel.set()


def start_over(ip):
    """Stop anything in progress and go back to the Roku home screen."""
    stop_sequence(ip)
    press(ip, "Home")


def status(ip):
    try:
        info = device_info(ip, timeout=3)
    except TvError as err:
        if err.unreachable:
            return {"ip": ip, "reachable": False}
        raise
    info["reachable"] = True
    info["activeApp"] = None
    try:
        body, _ = ecp(ip, "/query/active-app", timeout=3)
        app = ET.fromstring(body).find("app")
        if app is not None and app.get("id"):
            info["activeApp"] = app.get("id")
    except (TvError, ET.ParseError):
        pass
    return info


def apps(ip):
    body, _ = ecp(ip, "/query/apps")
    return [
        {"id": app.get("id"), "name": (app.text or "").strip()}
        for app in ET.fromstring(body).findall("app")
        if app.get("id") and app.get("type", "appl") == "appl"
    ]


class Handler(BaseHTTPRequestHandler):
    server_version = "RokuRemote"

    def log_message(self, *args):
        pass

    def send_body(self, code, body, content_type, cache="no-cache", filename=None):
        try:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass  # The page was closed before the reply, e.g. during a long channel sequence.

    def send_json(self, data, code=200):
        self.send_body(code, json.dumps(data).encode(), "application/json")

    def host_allowed(self):
        """Block websites that point their own domain at this server (DNS rebinding)."""
        host = urllib.parse.urlsplit("//" + self.headers.get("Host", "")).hostname or ""
        if host == "localhost" or host.endswith(".local") or host.lower() == socket.gethostname().lower():
            return True
        try:
            return ipaddress.ip_address(host).is_private
        except ValueError:
            return False

    def same_origin(self):
        """Block other websites open in your browser from pressing buttons on your TV."""
        origin = self.headers.get("Origin")
        return not origin or urllib.parse.urlsplit(origin).netloc == self.headers.get("Host")

    def do_GET(self):
        if not self.host_allowed():
            return self.send_json({"error": "Forbidden"}, 403)
        url = urllib.parse.urlsplit(self.path)
        query = {key: values[0] for key, values in urllib.parse.parse_qs(url.query).items()}
        try:
            if url.path == "/":
                self.send_body(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/saved":
                self.send_json(load_saved())
            elif url.path == "/api/discover":
                devices = discover()
                remember_tvs(devices)
                self.send_json({"devices": devices})
            elif url.path == "/api/settings":
                self.send_json(public_settings())
            elif url.path == "/api/status":
                self.send_json(status(check_ip(query.get("ip"))))
            elif url.path == "/api/apps":
                self.send_json({"apps": apps(query.get("ip")), "hidden": hidden_apps(query.get("ip"))})
            elif url.path == "/api/channels":
                self.send_json({"channels": channels_for(query.get("ip"))})
            elif url.path == "/api/channels/export":
                body, filename = export_channels(query.get("ip"))
                self.send_body(200, body, "application/json", filename=filename)
            elif url.path == "/api/icon":
                app_id = urllib.parse.quote(query.get("id", ""), safe="")
                body, content_type = ecp(query.get("ip"), f"/query/icon/{app_id}")
                if content_type not in IMAGE_TYPES:
                    return self.send_json({"error": "Not an image"}, 502)
                self.send_body(200, body, content_type, "max-age=86400")
            else:
                self.send_json({"error": "Not found"}, 404)
        except TvError as err:
            self.send_json({"error": str(err)}, 502)
        except ET.ParseError:
            self.send_json({"error": "The device at that address didn't respond like a Roku."}, 502)

    def handle_global_post(self, data):
        """Settings that aren't tied to one TV."""
        try:
            if self.path == "/api/settings":
                set_channels_only(data["channelsOnly"])
            elif self.path == "/api/lock":
                lock_action(data.get("action"), data.get("pin"))
            elif self.path == "/api/alerts":
                save_alerts(data)
            elif self.path == "/api/tvs/remove":
                key = data.get("key")
                if not isinstance(key, str) or not any(tv["key"] == key for tv in load_settings()["tvs"]):
                    raise TvError("That TV isn't in the list.")
                remove_tv(key)
                return self.send_json({**public_settings(), "saved": load_saved()})
            elif self.path == "/api/alerts/test":
                send_email(
                    load_settings()["alerts"],
                    "TV Remote test email",
                    "This is a test from TV Remote. If you got this, problem alerts can reach you.",
                )
            self.send_json(public_settings())
        except TvError as err:
            self.send_json({"error": str(err)}, 400)

    def do_POST(self):
        if not self.host_allowed() or not self.same_origin():
            return self.send_json({"error": "Forbidden"}, 403)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ValueError
            data = json.loads(self.rfile.read(length) or b"{}")
            if self.path in GLOBAL_POSTS:
                return self.handle_global_post(data)
            ip = data["ip"]
        except (ValueError, KeyError, TypeError):
            return self.send_json({"error": "Bad request"}, 400)
        try:
            if self.path == "/api/select":
                info = device_info(check_ip(ip))
                save_tv({key: info[key] for key in ("ip", "key", "name", "macs")})
                remember_tvs([info], chosen=True)
                self.send_json(info)
            elif self.path == "/api/key":
                press(ip, data.get("key"))
                self.send_json({"ok": True})
            elif self.path == "/api/launch":
                app_id = urllib.parse.quote(str(data.get("id", "")), safe="")
                ecp(ip, f"/launch/{app_id}", "POST")
                self.send_json({"ok": True})
            elif self.path == "/api/power-on":
                self.send_json(power_on(ip))
            elif self.path == "/api/channels/open":
                cancel = start_sequence(check_ip(ip))
                try:
                    wait_ms = max(0, min(int(data.get("openDelayMs") or 0), MAX_OPEN_MS))
                    self.send_json({"opened": open_app_fresh(ip, data.get("id", ""), cancel, bool(data.get("resetFirst")), wait_ms)})
                finally:
                    finish_sequence(ip, cancel)
            elif self.path == "/api/channels/save":
                self.send_json(save_channel(ip, data.get("channel"), data.get("tvs")))
            elif self.path == "/api/apps/hidden":
                set_hidden_apps(ip, data.get("hidden"))
                self.send_json({"ok": True})
            elif self.path == "/api/channels/import":
                self.send_json(import_channels(check_ip(ip), data.get("data")))
            elif self.path == "/api/channels/reorder":
                reorder_channels(ip, data.get("ids"))
                self.send_json({"ok": True})
            elif self.path == "/api/channels/delete":
                delete_channel(ip, data.get("id"))
                self.send_json({"ok": True})
            elif self.path == "/api/channels/play":
                self.send_json(play_channel(ip, data.get("id")))
            elif self.path == "/api/start-over":
                start_over(ip)
                self.send_json({"ok": True})
            elif self.path == "/api/channels/stop":
                stop_sequence(ip)
                self.send_json({"ok": True})
            else:
                self.send_json({"error": "Not found"}, 404)
        except TvError as err:
            self.send_json({"error": str(err)}, 502)
        except ET.ParseError:
            self.send_json({"error": "The device at that address didn't respond like a Roku."}, 502)


class Server(ThreadingHTTPServer):
    # On Windows, address reuse would let two servers silently share a port.
    allow_reuse_address = os.name != "nt"
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser(description="Local web remote for Roku devices")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="0.0.0.0", help="use 127.0.0.1 to allow only this PC")
    args = parser.parse_args()
    try:
        server = Server((args.host, args.port), Handler)
    except OSError:
        raise SystemExit(f"Port {args.port} is in use. Try: python server.py --port {args.port + 1}")
    print(f"Roku remote is running.\n  On this PC:  http://localhost:{args.port}")
    if args.host == "0.0.0.0":
        for ip in local_ips():
            print(f"  On your phone (same Wi-Fi):  http://{ip}:{args.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
