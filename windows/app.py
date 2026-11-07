"""Windows app: runs the remote's server privately and shows the page in its own window."""
import sys
import threading
from pathlib import Path

import webview

# The server and page live in ..\web. The built .exe bundles them, so this only matters
# when running app.py directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))
import server  # noqa: E402


def main():
    webview.settings["ALLOW_DOWNLOADS"] = True  # Lets Export channels save a file.
    # Port 0 picks any free port. 127.0.0.1 keeps it private to this PC.
    httpd = server.Server(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    webview.create_window("TV Remote", url, width=820, height=880, min_size=(360, 560), background_color="#14151a", maximized=True)
    webview.start()


if __name__ == "__main__":
    main()
