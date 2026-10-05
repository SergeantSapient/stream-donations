"""Command line entry for the collector and the local ledger UI."""

from __future__ import annotations

import argparse
import os
import re
import secrets
import signal
import sys
import threading
from pathlib import Path

from .fx import usd_rates
from .money import parse_membership_price
from .paths import DataPathError, resolve_data_dir
from .server import App, Handler, ThreadingServer
from .service import Service
from .store import Store

_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{16,256}")


def default_data_dir() -> Path:
    return resolve_data_dir()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stream-donations",
        description="Collect YouTube, Rumble, and Kick donations into a local ledger.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the local API, UI, and collectors")
    serve.add_argument("--data-dir", help="Directory for donations.db (default: the home data directory)")
    serve.add_argument("--port", type=int, help="Loopback UI port (default: 47321)")

    export = sub.add_parser("export", help="Write the ledger as CSV or JSONL")
    export.add_argument("fmt", choices=["csv", "jsonl"])
    export.add_argument("--data-dir")
    export.add_argument("-o", "--output", help="Output file. Defaults to stdout.")
    export.add_argument("--platform", default="")
    export.add_argument("--kind", default="")
    export.add_argument("--processed", choices=["", "0", "1"], default="")
    export.add_argument("--q", default="")

    poll = sub.add_parser("poll", help="Poll YouTube and Rumble once, then exit")
    poll.add_argument("--data-dir")

    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            return _serve(args)
        if args.command == "export":
            return _export(args)
        if args.command == "poll":
            return _poll(args)
    except DataPathError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 2


def _data_dir(args) -> Path:
    explicit = getattr(args, "data_dir", None) or ""
    return resolve_data_dir(str(explicit))


def _serve(args) -> int:
    token = os.environ.get("STREAM_DONATIONS_TOKEN", "").strip()
    generated = not token
    if token and not _TOKEN_RE.fullmatch(token):
        print(
            "STREAM_DONATIONS_TOKEN must be 16-256 characters of letters, digits, _ or -.",
            file=sys.stderr,
        )
        return 2
    if generated:
        token = secrets.token_hex(32)
    if args.port is not None:
        port = args.port
    else:
        env_port = os.environ.get("STREAM_DONATIONS_PORT", "").strip()
        port = int(env_port) if env_port else 47321
    if port < 0 or port > 65535:
        print("Port must be between 0 and 65535.", file=sys.stderr)
        return 2
    store = Store(_data_dir(args) / "donations.db")
    store.enable_system_notifications()
    app = App(store, token, port or 47321)
    try:
        server = ThreadingServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        print(f"Could not listen on 127.0.0.1:{port}: {exc}", file=sys.stderr)
        store.close()
        return 1
    server.app = app
    app.ui_port = int(server.server_address[1])
    app.start()
    print(f"STREAM_DONATIONS_READY port={app.ui_port}", flush=True)
    if generated:
        print(f"Database: {store.path}", flush=True)
        print(
            f"Open http://127.0.0.1:{app.ui_port}/boot?token={token}",
            flush=True,
        )

    def _stop(_signum, _frame) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        server.serve_forever()
    finally:
        app.stop()
        server.server_close()
        store.close()
    return 0


def _export(args) -> int:
    store = Store(_data_dir(args) / "donations.db")
    try:
        filters = {
            "platform": args.platform,
            "kind": args.kind,
            "processed": args.processed,
            "query": args.q,
            "exclude_kinds": [],
        }
        if args.fmt == "csv":
            text = store.export_csv(
                rates=usd_rates(store),
                membership_price=parse_membership_price(store.get_setting("membership_price")),
                **filters,
            )
        else:
            text = store.export_jsonl(**filters)
    finally:
        store.close()
    if args.output:
        path = Path(args.output).expanduser()
        path.write_text(text, encoding="utf-8")
        os.chmod(path, 0o600)
    else:
        sys.stdout.write(text)
    return 0


def _poll(args) -> int:
    store = Store(_data_dir(args) / "donations.db")
    store.enable_system_notifications()
    try:
        errors = Service(store).poll_now()
    finally:
        store.close()
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
