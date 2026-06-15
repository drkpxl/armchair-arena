"""Entrypoint: `python -m app` — serves the app on BIND_HOST:PORT from .env."""
from __future__ import annotations

import uvicorn

from .config import BIND_HOST, PORT


def main() -> None:
    uvicorn.run("app.main:app", host=BIND_HOST, port=PORT)


if __name__ == "__main__":
    main()
