from __future__ import annotations

import logging
import sys

from .app import EwstuiApp
from .config import config_from_args


def main(argv: list[str] | None = None) -> int:
    cfg = config_from_args(argv)

    # exchangelib is chatty at INFO; keep it quiet unless the user wants
    # verbose diagnostics. Logging goes to a file, never stdout/stderr,
    # since Textual owns the terminal once the app starts.
    logging.basicConfig(filename="ewstui.log", level=logging.WARNING)

    if cfg.demo:
        from .demo_backend import DemoCalendarClient, DemoMailClient

        mail_client = DemoMailClient(page_size=cfg.page_size)
        calendar_client = DemoCalendarClient()
    else:
        from . import auth
        from .ews_client import CalendarClient, MailClient

        try:
            account = auth.get_account(cfg)
        except auth.AuthError as e:
            print(f"Authentication failed: {e}", file=sys.stderr)
            return 1
        mail_client = MailClient(account, page_size=cfg.page_size)
        calendar_client = CalendarClient(account)

    app = EwstuiApp(mail_client, calendar_client, cfg)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
