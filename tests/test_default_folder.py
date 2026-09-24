from __future__ import annotations

import pytest

from ewstui.app import EwstuiApp
from ewstui.config import config_from_args
from ewstui.demo_backend import DemoCalendarClient, DemoMailClient
from ewstui.ews_client import FolderSummary
from ewstui.widgets.folder_list import FolderList
from ewstui.widgets.message_table import MessageTable


class LiveShapedMailClient(DemoMailClient):
    """Folder tree shaped like a real mailbox: msg_folder_root ("Top of
    Information Store") first, with the Inbox nested under it and a
    localized display name.
    """

    def __init__(self, inbox_resolves: bool = True):
        super().__init__()
        self.inbox_resolves = inbox_resolves
        self._folders = [
            FolderSummary(id="root", name="Top of Information Store", total_count=0, unread_count=0, depth=0),
            FolderSummary(id="sent", name="Sendt post", total_count=2, unread_count=0, depth=1),
            FolderSummary(id="inbox", name="Indbakke", total_count=4, unread_count=2, depth=1),
        ]
        self._messages["root"] = []

    def default_folder_id(self) -> str:
        if not self.inbox_resolves:
            raise RuntimeError("EWS error resolving inbox")
        return "inbox"


def make_app(tmp_path, mail) -> EwstuiApp:
    cfg = config_from_args(["--demo", "--priority-file", str(tmp_path / "priorities.todo.txt")])
    return EwstuiApp(mail, DemoCalendarClient(), cfg)


async def test_opens_inbox_not_top_of_information_store(tmp_path):
    app = make_app(tmp_path, LiveShapedMailClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.current_folder_id == "inbox"
        assert app.query_one("#folders", FolderList).index == 2
        assert app.query_one("#messages", MessageTable).row_count == 4


@pytest.mark.parametrize("mail", [LiveShapedMailClient(inbox_resolves=False)], ids=["inbox-lookup-fails"])
async def test_falls_back_to_first_folder(tmp_path, mail):
    app = make_app(tmp_path, mail)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.current_folder_id == "root"
        assert app.query_one("#folders", FolderList).index == 0
