"""Headless server entry point: `python -m app.serve`.

No pywebview — just the FastAPI backend + the queue supervisor, bound on a
fixed host/port so Tailnet devices (the iOS app) can reach it. The bearer token
is printed once on startup so it can be copied into the app.
"""
from __future__ import annotations

import uvicorn

from . import config
from .db import DB
from .server import create_app
from .supervisor import Supervisor


def main() -> None:
    db = DB()
    supervisor = Supervisor(db)
    supervisor.start()
    supervisor.start_queue()  # on-demand jobs should run as soon as they arrive
    app = create_app(db, supervisor)

    token = config.get_or_create_token()
    print("=" * 60, flush=True)
    print(f"  PocketBooks listening on "
          f"{config.SERVER_HOST}:{config.SERVER_PORT}", flush=True)
    print(f"  library folder: {config.library_dir()}", flush=True)
    print(f"  API token:      {token}", flush=True)
    print("  (reach it over Tailscale; never expose to the open internet)",
          flush=True)
    print("=" * 60, flush=True)

    uvicorn.Server(uvicorn.Config(
        app, host=config.SERVER_HOST, port=config.SERVER_PORT,
        log_level="warning")).run()


if __name__ == "__main__":
    main()
