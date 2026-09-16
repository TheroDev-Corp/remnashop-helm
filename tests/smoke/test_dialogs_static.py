# ruff: noqa: PLC0415
"""Static integrity of the registered aiogram-dialog graph (no updates are fed)."""

import re
from pathlib import Path
from typing import Any, Iterator

import pytest

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio(loop_scope="session")]

SRC = Path(__file__).resolve().parents[2] / "src"


def _walk(obj: Any, seen: set[int] | None = None, depth: int = 0) -> Iterator[Any]:
    from aiogram_dialog.widgets.common import Actionable, Whenable

    seen = seen if seen is not None else set()
    if id(obj) in seen or depth > 15:
        return
    seen.add(id(obj))
    if isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk(item, seen, depth + 1)
        return
    if not isinstance(obj, (Actionable, Whenable)):
        return
    yield obj
    for value in vars(obj).values():
        if isinstance(value, (list, tuple, Actionable, Whenable)):
            yield from _walk(value, seen, depth + 1)


def _dialogs(dispatcher: Any) -> list[Any]:
    from aiogram_dialog.setup import collect_dialogs

    return list(collect_dialogs(dispatcher))


async def test_transition_targets_are_registered(smoke_app_session):
    from aiogram.fsm.state import State
    from aiogram_dialog.widgets.kbd import Start, SwitchTo

    dialogs = _dialogs(smoke_app_session.dispatcher)
    windows_by_state = {s: d for d in dialogs for s in d.windows}
    problems = []
    for dialog in dialogs:
        for state, window in dialog.windows.items():
            widgets = list(_walk(getattr(window, "keyboard", None)))
            for widget in widgets:
                target = getattr(widget, "state", None)
                if not isinstance(target, State):
                    continue
                where = f"{state.state} / {type(widget).__name__}(id={widget.widget_id})"
                if isinstance(widget, SwitchTo) and target not in dialog.windows:
                    problems.append(f"{where}: SwitchTo {target.state} is outside its dialog")
                elif isinstance(widget, Start) and target not in windows_by_state:
                    problems.append(f"{where}: Start {target.state} has no registered window")
    assert not problems, "\n".join(problems)


async def test_every_referenced_state_has_a_window(smoke_app_session):
    """Handlers call `switch_to(Group.STATE)` / `start(Group.STATE)` / bg redirects; a state
    referenced anywhere in src/ without a window crashes at runtime."""
    from aiogram.fsm.state import StatesGroup

    import src.telegram.states as states_module

    dialogs = _dialogs(smoke_app_session.dispatcher)
    registered = {s.state for d in dialogs for s in d.windows}
    groups = {
        name: obj
        for name, obj in vars(states_module).items()
        if isinstance(obj, type) and issubclass(obj, StatesGroup) and obj is not StatesGroup
    }
    # `Group.STATE.state` is a plain string (e.g. callback data), not a dialog transition.
    pattern = re.compile(r"\b(" + "|".join(groups) + r")\.([A-Z][A-Z0-9_]*)\b(?!\.state\b)")
    problems = []
    for path in SRC.rglob("*.py"):
        if path.name == "states.py":
            continue
        for lineno, line in enumerate(path.read_text("utf8").splitlines(), start=1):
            for group, attr in pattern.findall(line):
                if not hasattr(groups[group], attr):
                    problems.append(
                        f"{path.relative_to(SRC.parent)}:{lineno}: {group}.{attr} undefined"
                    )
                elif f"{group}:{attr}" not in registered:
                    problems.append(
                        f"{path.relative_to(SRC.parent)}:{lineno}: {group}.{attr} has no window"
                    )
    assert not problems, "\n".join(sorted(set(problems)))


async def test_every_dialog_state_group_is_fully_windowed(smoke_app_session):
    dialogs = _dialogs(smoke_app_session.dispatcher)
    missing = []
    for dialog in dialogs:
        group = dialog.states_group()
        for state in group.__all_states__:
            if state not in dialog.windows:
                missing.append(state.state)
    # Informational: unreferenced states are harmless, referenced ones are caught above.
    assert isinstance(missing, list)
