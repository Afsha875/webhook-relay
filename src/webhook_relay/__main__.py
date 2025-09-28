"""Run the relay as an HTTP service: ``python -m webhook_relay``."""

from __future__ import annotations

import os

import uvicorn

from webhook_relay.api import build_default_app


def main() -> None:
    db_path = os.environ.get("WEBHOOK_RELAY_DB", "webhook_relay.db")
    host = os.environ.get("WEBHOOK_RELAY_HOST", "127.0.0.1")
    port = int(os.environ.get("WEBHOOK_RELAY_PORT", "8000"))
    app = build_default_app(db_path)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
