"""What family members may do through the family hub, by role.

Both the hub and TV Remote at home check these rules, so a request needs both to agree.
Anything not listed, like searching the home network or exporting files, never works remotely.
"""
HELPER = {
    ("GET", "/api/saved"), ("GET", "/api/settings"), ("GET", "/api/status"), ("GET", "/api/apps"),
    ("GET", "/api/channels"), ("GET", "/api/icon"), ("GET", "/api/schedules"),
    ("POST", "/api/select"), ("POST", "/api/key"), ("POST", "/api/launch"), ("POST", "/api/power-on"),
    ("POST", "/api/start-over"), ("POST", "/api/settings"), ("POST", "/api/apps/hidden"), ("POST", "/api/tv-options"),
    ("POST", "/api/channels/open"), ("POST", "/api/channels/save"), ("POST", "/api/channels/reorder"),
    ("POST", "/api/channels/delete"), ("POST", "/api/channels/play"), ("POST", "/api/channels/stop"),
    ("POST", "/api/schedules/save"), ("POST", "/api/schedules/delete"),
}
OWNER = HELPER | {
    ("POST", "/api/tvs/remove"), ("POST", "/api/lock"), ("POST", "/api/alerts"), ("POST", "/api/alerts/test"),
}
ROLES = ("helper", "owner")


def allowed(role, method, path):
    rules = OWNER if role == "owner" else HELPER if role == "helper" else set()
    return (method, path.split("?", 1)[0]) in rules
