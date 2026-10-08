from __future__ import annotations

import logging
import sys

from aiohttp import web

from app.api import build_web_app
from app.config import load_config, parse_args
from app.smtp_receiver import create_smtp_controller
from app.storage import MessageStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("smtp-code-service")


def main() -> int:
    args = parse_args()
    try:
        config = load_config(args)
    except ValueError as exc:
        logger.error(str(exc))
        return 1

    store = MessageStore(config.db_path)
    store.init()

    smtp = create_smtp_controller(config, store)
    smtp.start()
    logger.info("SMTP service started on %s:%d (domains: %s)", config.smtp_host, config.smtp_port, ", ".join(config.domains))

    app = build_web_app(config, store)
    logger.info("HTTP service listening on http://%s:%d", config.http_host, config.http_port)

    try:
        web.run_app(app, host=config.http_host, port=config.http_port, print=None)
    finally:
        logger.info("Stopping SMTP service...")
        smtp.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
