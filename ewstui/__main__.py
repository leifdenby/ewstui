from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

from .app import EwstuiApp
from .config import config_from_args

LOG_FILE = Path("ewstui.log")


def main(argv: list[str] | None = None) -> int:
    cfg = config_from_args(argv)

    # exchangelib is chatty at INFO; keep it quiet unless the user wants
    # verbose diagnostics. Logging goes to a file, never stdout/stderr,
    # since Textual owns the terminal once the app starts.
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.DEBUG if cfg.debug else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if cfg.demo:
        from .demo_backend import DemoCalendarClient, DemoMailClient

        mail_client = DemoMailClient(page_size=cfg.page_size)
        calendar_client = DemoCalendarClient()
    else:
        from . import auth
        from .ews_client import CalendarClient, MailClient

        if cfg.debug:
            print(f"Debug logging to {LOG_FILE.resolve()}", file=sys.stderr)
        try:
            if cfg.ews_url:
                auth.probe_endpoint(cfg.ews_url, verify_ssl=cfg.verify_ssl)
            account = auth.get_account(cfg)
            auth.verify_account(account)
        except Exception as e:  # noqa: BLE001 - report anything, never start a blank TUI
            reason = str(e) if isinstance(e, auth.AuthError) else auth.explain_error(e)
            print(f"\nConnection failed: {reason}", file=sys.stderr)
            if cfg.debug:
                traceback.print_exc()
            else:
                print("Re-run with --debug for a traceback and full EWS traffic in the log.", file=sys.stderr)
            return 1
        mail_client = MailClient(account, page_size=cfg.page_size)
        calendar_client = CalendarClient(account)

    app = EwstuiApp(mail_client, calendar_client, cfg)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
