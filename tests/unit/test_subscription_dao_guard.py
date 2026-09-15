"""Guard logic of SubscriptionDaoImpl with a mocked AsyncSession (no DB test infra exists).

SQL semantics (joins/aggregations) are only compile-checked against the PostgreSQL dialect here.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from src.core.enums import SubscriptionStatus
from src.core.exceptions import RemnaUserBindingError
from src.infrastructure.database.dao.subscription import SubscriptionDaoImpl


def _result(rows):
    result = MagicMock()
    result.all = MagicMock(return_value=rows)
    return result


@pytest.fixture
def session():
    session = MagicMock()
    session.execute = AsyncMock(return_value=_result([]))
    session.scalar = AsyncMock(return_value=MagicMock())
    session.flush = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture
def dao(session):
    retort = MagicMock()
    retort.dump = MagicMock(side_effect=lambda value, *args: value if args else {})
    user_dao = MagicMock()
    user_dao.set_current_subscription_by_id = AsyncMock()
    return SubscriptionDaoImpl(
        session=session,
        retort=retort,
        conversion_retort=MagicMock(),
        redis=MagicMock(),
        user_dao=user_dao,
    )


def _compiled(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_create_refuses_remna_id_bound_to_other_users_current_subscription(
    dao, session, sample_subscription_dto
):
    session.execute.return_value = _result([(7, 222)])

    with pytest.raises(RemnaUserBindingError):
        await dao.create(sample_subscription_dto, user_id=1)

    session.add.assert_not_called()
    sql = _compiled(session.execute.await_args.args[0])
    assert "current_subscription_id" in sql
    assert "users.id != " in sql


@pytest.mark.asyncio
async def test_create_allowed_when_remna_id_free(dao, session, sample_subscription_dto):
    await dao.create(sample_subscription_dto, user_id=1)

    session.add.assert_called_once()
    dao.user_dao.set_current_subscription_by_id.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_without_remna_id_change_skips_guard(dao, session, sample_subscription_dto):
    sample_subscription_dto.status = SubscriptionStatus.DISABLED

    await dao.update(sample_subscription_dto)

    session.execute.assert_not_awaited()
    session.scalar.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_refuses_remna_id_of_other_user(dao, session, sample_subscription_dto):
    sample_subscription_dto.user_remna_id = 149
    session.execute.return_value = _result([(9, 333)])

    with pytest.raises(RemnaUserBindingError):
        await dao.update(sample_subscription_dto)

    session.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_looks_up_owner_when_dto_has_no_user_id(dao, session, sample_subscription_dto):
    sample_subscription_dto.user_id = 0
    sample_subscription_dto.user_remna_id = 149
    session.scalar = AsyncMock(side_effect=[42, MagicMock()])

    await dao.update(sample_subscription_dto)

    sql = _compiled(session.execute.await_args.args[0])
    params = session.execute.await_args.args[0].compile().params
    assert "current_subscription_id" in sql
    assert 42 in params.values()
    assert 149 in params.values()


@pytest.mark.asyncio
async def test_get_remna_id_conflicts_maps_rows(dao, session):
    session.execute.return_value = _result([(149, [1, 2, 3], [111, 222, None])])

    conflicts = await dao.get_remna_id_conflicts()

    assert len(conflicts) == 1
    assert conflicts[0].user_remna_id == 149
    assert conflicts[0].user_ids == [1, 2, 3]
    assert conflicts[0].telegram_ids == [111, 222, None]
    sql = _compiled(session.execute.await_args.args[0])
    assert "HAVING count(distinct(users.id)) >" in sql
    assert "subscriptions.user_remna_id > " in sql
