"""Command line entry point: ``python -m steam_mfran``."""

from __future__ import annotations

import argparse
import logging
import os
import threading
import webbrowser
from pathlib import Path

import uvicorn

from . import __version__
from .server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="steam-mfran",
        description="Steam Multi Friend Requests Analyzer - triage incoming friend requests fast.",
    )
    parser.add_argument("--host", default=os.environ.get("MFRAN_HOST", "127.0.0.1"),
                        help="interface to listen on (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MFRAN_PORT", "8777")))
    parser.add_argument("--data-dir", default=os.environ.get("MFRAN_DATA", "data"),
                        help="where the session, settings and caches are stored (default ./data)")
    parser.add_argument("--demo", action="store_true", help="run with generated data, no Steam account needed")
    parser.add_argument("--proxy", default=os.environ.get("MFRAN_PROXY"),
                        help="HTTP proxy for Steam requests, e.g. http://user:pass@host:port")
    parser.add_argument("--token", default=os.environ.get("MFRAN_TOKEN"),
                        help="require this access token for the web UI (open /?token=...) - "
                             "recommended when listening on a non-local interface")
    parser.add_argument("--allow-host", action="append", default=[],
                        help="extra Host header value to accept (e.g. your LAN hostname); repeatable")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser on start")
    parser.add_argument("--log-level", default=os.environ.get("MFRAN_LOG", "INFO"))
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    allowed = set(args.allow_host)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        allowed.add(args.host)
        if not args.token:
            logging.getLogger("steam_mfran").warning(
                "Listening on %s without --token: anyone who can reach this port controls your Steam account.",
                args.host,
            )
    data_dir = Path(args.data_dir).expanduser().resolve()
    if args.demo:
        data_dir = data_dir / "demo"
    app = create_app(data_dir, demo=args.demo, proxy=args.proxy, token=args.token, allowed_hosts=allowed)

    shown_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{shown_host}:{args.port}/" + (f"?token={args.token}" if args.token else "")
    print(f"\n  Steam MFRAN {__version__} {'(demo) ' if args.demo else ''}→ {url}\n")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
