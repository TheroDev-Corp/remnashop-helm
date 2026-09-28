"""Scope conditions of UserDaoImpl: the "from panel" / "not in bot" SQL and its counter twin.

Nothing records the first private contact with the bot and the production database must not be
migrated for it, so "not in bot" is a heuristic over existing columns. These tests pin that SQL
and check the segment list and the filter counters build it from the same helper, so the number
on the button cannot drift from the list behind it. Statements are only compile-checked against
the PostgreSQL dialect; no database is involved.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from src.core.enums import UserFilter, UserSource
from src.infrastructure.database.dao.user import UserDaoImpl

HEURISTIC_FRAGMENTS = (
    "users.telegram_id IS NULL",
    "users.username IS NULL",
    "users.name = CAST(users.telegram_id AS TEXT)",
    "plan_snapshot",
)


def _compiled(element) -> str:
    return str(element.compile(dialect=postgresql.dialect()))


@pytest.fixture
def session():
    row = MagicMock()
    row._mapping = {user_filter.value: 0 for user_filter in UserFilter}
    result = MagicMock()
    result.one = MagicMock(return_value=row)
    result.all = MagicMock(return_value=[])
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.scalars = AsyncMock(return_value=result)
    return session


@pytest.fixture
def dao(session):
    conversion_retort = MagicMock()
    conversion_retort.get_converter = MagicMock(return_value=MagicMock(return_value=[]))
    return UserDaoImpl(
        session=session,
        retort=MagicMock(),
        conversion_retort=conversion_retort,
        redis=MagicMock(),
    )


def test_not_in_bot_reads_import_defaults_instead_of_a_column():
    sql = _compiled(UserDaoImpl._never_wrote_the_bot())

    assert "bot_started_at" not in sql
    for fragment in HEURISTIC_FRAGMENTS:
        assert fragment in sql, sql
    assert "EXISTS" in sql
    assert "subscriptions" in sql


def test_from_panel_is_the_snapshot_tag_and_self_is_its_negation():
    assert UserDaoImpl._scope_conditions(UserSource.ANY, False) == []

    panel = _compiled(UserDaoImpl._scope_conditions(UserSource.PANEL, False)[0])
    self_made = _compiled(UserDaoImpl._scope_conditions(UserSource.SELF, False)[0])

    assert panel.startswith("EXISTS")
    assert "plan_snapshot" in panel
    assert self_made.startswith("NOT (EXISTS")
    # "from panel" must not borrow the "not in bot" heuristic: a panel import that later wrote
    # the bot is still a panel import.
    assert "users.username IS NULL" not in panel


async def test_segment_list_and_counters_apply_the_same_scope(dao, session):
    scope = {"source": UserSource.PANEL, "not_in_bot": True}

    await dao.get_by_filter(UserFilter.ALL, **scope)
    list_sql = _compiled(session.scalars.await_args.args[0])

    await dao.count_by_filters(**scope)
    count_sql = _compiled(session.execute.await_args.args[0])

    for fragment in HEURISTIC_FRAGMENTS:
        assert fragment in list_sql, list_sql
        assert fragment in count_sql, count_sql
    # Source + not-in-bot: the panel EXISTS is used by both conditions, twice in each statement.
    assert list_sql.count("EXISTS") == count_sql.count("EXISTS") == 2


async def test_unscoped_queries_carry_no_scope_predicate(dao, session):
    await dao.get_by_filter(UserFilter.ALL)
    list_sql = _compiled(session.scalars.await_args.args[0])

    await dao.count_by_filters()
    count_sql = _compiled(session.execute.await_args.args[0])

    for sql in (list_sql, count_sql):
        assert "EXISTS" not in sql
        assert "users.username IS NULL" not in sql
