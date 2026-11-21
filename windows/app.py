"""Windows app: runs the remote's server privately and shows the page in its own window."""
import sys
import threading
from pathlib import Path

import webview

# The server and page live in ..\web. The built .exe bundles them, so this only matters
# when running app.py directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))
import server  # noqa: E402


def allow_camera(window, origin):
    """Video calls: let this app's own page use the camera and microphone without asking each time.

    Answering a call is already the person's choice, so a second "allow camera?" pop-up would only
    confuse. Only this app's own page gets this. Any other page still has to ask.
    """
    def on_permission(sender, args):
        if str(args.Uri).startswith(origin) and str(args.PermissionKind) in ("Camera", "Microphone"):
            from Microsoft.Web.WebView2.Core import CoreWebView2PermissionState
            args.State = CoreWebView2PermissionState.Allow

    def attach():
        browser = window.native.browser.webview
        if browser.CoreWebView2 is not None:
            browser.CoreWebView2.PermissionRequested += on_permission
        else:
            def ready(sender, args):
                if args.IsSuccess:
                    sender.CoreWebView2.PermissionRequested += on_permission
            browser.CoreWebView2InitializationCompleted += ready

    def on_shown():
        try:
            from System import Action
            window.native.Invoke(Action(attach))  # The browser engine must be set up on the window's own thread.
        except Exception as err:  # Calls still work; the browser just asks for the camera itself.
            print(f"Couldn't set camera permission: {err}", file=sys.stderr)

    window.events.shown += on_shown


def already_running():
    """True if another copy of TV Remote is open on this PC. Two copies would both run schedules and
    channel buttons on the same TVs, mixing their presses together."""
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # Reads Windows' error code safely.
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    already_running.handle = kernel32.CreateMutexW(None, False, "Local\\TVRemoteApp")  # Held until this copy closes.
    return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS


def main():
    if already_running():
        return  # The copy that's already open carries on; this one just closes.
    webview.settings["ALLOW_DOWNLOADS"] = True  # Lets Export channels save a file.
    # Port 0 picks any free port. 127.0.0.1 keeps it private to this PC.
    httpd = server.Server(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    window = webview.create_window("TV Remote", url, width=820, height=880, min_size=(360, 560),
                                   background_color="#14151a", maximized=True)
    allow_camera(window, url.rstrip("/"))
    webview.start()


if __name__ == "__main__":
    main()
