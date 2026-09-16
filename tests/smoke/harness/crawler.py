# ruff: noqa: PLC0415
"""Depth-first UI crawler: from /start, press every dialog button on every reachable screen.

Returning to a screen does not replay the path: the user's aiogram-dialog FSM keys (stack and
contexts in the fake Redis) and the fake chat's live messages are snapshotted and restored.
Database side effects are kept (each crawl test starts from a freshly seeded database).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from aiogram_dialog.utils import CB_SEP

from .app import RecordedError, SmokeApp
from .client import TgUser

TEXT_INPUTS = ("1", "smoke-test")


@dataclass
class Failure:
    path: list[str]
    state: Optional[str]
    action: str
    errors: list[RecordedError]

    def summary(self) -> str:
        first = self.errors[0]
        head = first.text.strip().splitlines()
        exc = f"{type(first.exception).__name__}: {first.exception}" if first.exception else head[0]
        return f"{self.state} :: {self.action} -> {exc} [{first.source}]"

    def details(self) -> str:
        steps = " -> ".join(self.path + [self.action])
        body = "\n".join(f"[{e.source}]\n{e.text}" for e in self.errors)
        return f"PATH: /start -> {steps}\n{body}"


@dataclass
class CrawlResult:
    visited: set[str] = field(default_factory=set)
    actions: int = 0
    failures: list[Failure] = field(default_factory=list)
    truncated: bool = False


class Crawler:
    def __init__(
        self,
        app: SmokeApp,
        user: TgUser,
        *,
        skip: set[str] | None = None,
        max_actions: int = 1500,
        max_seconds: float = 240.0,
        items_per_list: int = 1,
    ) -> None:
        self.app = app
        self.user = user
        self.skip = skip or set()
        self.max_actions = max_actions
        self.max_seconds = max_seconds
        self.items_per_list = items_per_list
        self.result = CrawlResult()
        self._expanded: set[str] = set()
        self._deadline = 0.0
        from aiogram_dialog.setup import collect_dialogs

        self._windows = {
            s.state: w for d in collect_dialogs(app.dispatcher) for s, w in d.windows.items()
        }

    # ------------------------------------------------------------------ entry
    async def run(self) -> CrawlResult:
        self._deadline = time.monotonic() + self.max_seconds
        await self._act([], ("send", "/start"))
        await self._explore([], depth=0)
        return self.result

    # ------------------------------------------------------------------ core
    def _budget_left(self) -> bool:
        if self.result.actions >= self.max_actions or time.monotonic() > self._deadline:
            self.result.truncated = True
            return False
        return True

    async def _explore(self, path: list[str], depth: int) -> None:
        state = self.app.current_state(self.user.id)
        if state is None or state in self._expanded or depth > 12:
            return
        self._expanded.add(state)
        self.result.visited.add(state)
        snapshot = await self.app.snapshot_dialog(self.user.id)
        for action in self._actions(state):
            if not self._budget_left():
                return
            await self.app.restore_dialog(self.user.id, snapshot)
            self.app.renders.append((self.user.id, state))
            label = await self._act(path, action, state)
            if label is None:
                continue
            new_state = self.app.current_state(self.user.id)
            if new_state is not None:
                self.result.visited.add(new_state)
            if new_state and new_state != state and new_state not in self._expanded:
                await self._explore(path + [label], depth + 1)

    def _actions(self, state: str) -> list[tuple[str, str]]:
        actions: list[tuple[str, str]] = []
        seen_keys: dict[str, int] = {}
        for button in self.user.buttons():
            data = button.callback_data or ""
            if CB_SEP not in data:
                continue
            widget_data = button.widget_data or ""
            parts = widget_data.split(":")
            key = parts[0] if len(parts) <= 2 else f"{parts[0]}:{parts[-1]}"
            base = parts[-1] if len(parts) >= 3 else parts[0]
            if parts[0] in self.skip or base in self.skip or f"{state}/{base}" in self.skip:
                continue
            if parts[0] == "__pager__" or parts[0].startswith("scroll"):
                key = parts[0]
            seen_keys[key] = seen_keys.get(key, 0) + 1
            if seen_keys[key] > self.items_per_list:
                continue
            actions.append(("click", data))
        window = self._windows.get(state)
        if window is not None and getattr(window, "on_message", None) is not None:
            if not _only_ignore_update(window.on_message):
                actions.extend(("send", text) for text in TEXT_INPUTS)
        return actions

    async def _act(
        self, path: list[str], action: tuple[str, str], state: Optional[str] = None
    ) -> Optional[str]:
        kind, value = action
        self.result.actions += 1
        if kind == "send":
            label = f"send({value!r})"
            await self.user.send(value, check=False)
        else:
            label = f"click({value.split(CB_SEP, 1)[-1]})"
            await self.user.click_data(value, check=False, label=label)
        errors = self.app.take_errors()
        if errors:
            self.result.failures.append(Failure(list(path), state, label, errors))
        return label


def _only_ignore_update(on_message: Any) -> bool:
    from aiogram_dialog.widgets.input import MessageInput

    from src.telegram.widgets import IgnoreUpdate

    stack = [on_message]
    found_input = False
    while stack:
        item = stack.pop()
        if isinstance(item, IgnoreUpdate):
            continue
        if isinstance(item, MessageInput):
            found_input = True
        stack.extend(getattr(item, "inputs", []) or [])
    return not found_input
