"""Command-line entry point for the unified development server."""

from __future__ import annotations

import argparse
import logging
from dataclasses import replace
from pathlib import Path

from src.server.application import Application
from src.server.config import ServerConfig
from src.server.http import create_server


def main() -> None:
    defaults = ServerConfig.from_env()
    parser = argparse.ArgumentParser(description="Run the Past Partner backend and Web client")
    parser.add_argument("--host", default=defaults.host)
    parser.add_argument("--port", type=int, default=defaults.port)
    parser.add_argument("--data-dir", type=Path, default=defaults.data_dir)
    parser.add_argument("--web-dir", type=Path, default=defaults.web_dir)
    parser.add_argument("--mode", choices=("development", "test", "production"), default=defaults.mode)
    args = parser.parse_args()
    config = replace(
        defaults,
        host=args.host,
        port=args.port,
        data_dir=args.data_dir,
        web_dir=args.web_dir,
        mode=args.mode,
    ).validated()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    application = Application.from_config(config)
    server = create_server(config, application)
    scheme = "https" if server.is_tls else "http"
    logging.getLogger(__name__).info("Serving on %s://%s:%s", scheme, config.host, server.server_address[1])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        application.close()


if __name__ == "__main__":
    main()
