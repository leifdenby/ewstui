from __future__ import annotations

import inspect
import re

from ewstui import __main__ as entry
from ewstui.config import SUBCOMMANDS, build_arg_parser


def test_help_lists_every_subcommand() -> None:
    """`ewstui -h` names each subcommand that main() dispatches, and only those."""
    dispatched = set(re.findall(r'argv\[:1\] == \["([\w-]+)"\]', inspect.getsource(entry.main)))
    assert dispatched == set(SUBCOMMANDS)
    help_text = build_arg_parser().format_help()
    assert "subcommands" in help_text
    for name in SUBCOMMANDS:
        assert f"\n  {name} " in help_text
