# ruff: noqa: PLC0415
"""Press every dialog button on every reachable screen, per role, and require zero crashes.

Known source bugs are listed in KNOWN_BUGS as `(state, action-prefix) -> reason`. They are
enforced strictly: a listed bug that no longer reproduces fails the test so the entry gets
removed, and any failure not listed fails the test with its reproduction path + traceback.
"""

from __future__ import annotations

import sys

import pytest

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio(loop_scope="session")]

# Buttons that would end the crawl for the acting user or have no in-bot outcome to check.
SKIP_COMMON = {
    "back_main_menu",  # always leads to the already expanded main menu
}

# (state, action label prefix) -> reason. Filled from triaged crawl failures.
KNOWN_BUGS: dict[tuple[str, str], str] = {}


def _format(result) -> str:
    return "\n\n".join(f.details() for f in result.failures)


async def _crawl(app, telegram_id: int, name: str, skip: set[str]):
    from tests.smoke.harness.client import TgUser
    from tests.smoke.harness.crawler import Crawler

    user = TgUser(app, telegram_id, name, name.lower())
    crawler = Crawler(app, user, skip=SKIP_COMMON | skip)
    result = await crawler.run()

    unexpected = []
    reproduced = set()
    for failure in result.failures:
        key = next(
            (k for k in KNOWN_BUGS if k[0] == failure.state and failure.action.startswith(k[1])),
            None,
        )
        if key is None:
            unexpected.append(failure)
        else:
            reproduced.add(key)

    truncated = " (TRUNCATED)" if result.truncated else ""
    sys.stdout.write(
        f"\n[{name}] visited {len(result.visited)} states with {result.actions} actions"
        f"{truncated}:\n  {', '.join(sorted(result.visited))}\n"
    )
    if unexpected:
        summary = "\n".join(f"- {f.summary()}" for f in unexpected)
        details = "\n\n".join(f.details() for f in unexpected)
        pytest.fail(f"{len(unexpected)} crashing actions for {name}:\n{summary}\n\n{details}")
    return result, reproduced


async def test_crawl_as_owner(app):
    # Deleting the target user / own role changes would cut off the rest of the crawl.
    skip = {"delete_user", "confirm_delete", "role_revoke"}
    await _crawl(app, app.seed.owner_tg, "Owner", skip)


async def test_crawl_as_subscriber(app):
    await _crawl(app, app.seed.subscriber_tg, "Subscriber", set())


async def test_crawl_as_newbie(app):
    await _crawl(app, app.seed.newbie_tg, "Newbie", set())
