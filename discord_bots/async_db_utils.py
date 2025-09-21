"""
Async database utilities and migration helpers.

This module provides utilities for gradually migrating from sync to async SQLAlchemy.
"""

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable, Optional, TypeVar

from sqlalchemy import and_, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AsyncSessionLocal

T = TypeVar("T")


def _build_query(model_class: type[T], *conditions):
    """Helper function to build a query with conditions."""
    query = select(model_class)
    if conditions:
        if len(conditions) == 1:
            query = query.where(conditions[0])
        else:
            query = query.where(and_(*conditions))
    return query


@asynccontextmanager
async def async_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager for database sessions.

    Usage:
        async with async_session() as session:
            result = await session.execute(select(Player).where(Player.id == 1))
            player = result.scalar_one_or_none()
    """
    if not AsyncSessionLocal:
        raise RuntimeError("Async database not configured - check your DATABASE_URI")

    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def async_query_first(
    session: AsyncSession, model_class: type[T], *conditions
) -> Optional[T]:
    """
    Async query for fetching a single record using provided session.

    Args:
        session: The async session to use for the query
        model_class: The SQLAlchemy model class
        *conditions: One or more filter conditions (combined with AND unless using and_/or_)

    Returns:
        The first model instance or None if not found

    Examples:
        async with async_session() as session:
            # Single condition
            player = await async_query_first(session, Player, Player.id == 123)

            # Multiple conditions (AND)
            player = await async_query_first(
                session,
                Player,
                Player.discord_id == 12345,
                Player.is_banned == False
            )

            # Complex conditions using and_/or_
            player = await async_query_first(
                session,
                Player,
                and_(Player.is_banned == False, or_(Player.discord_id == 12345, Player.name == "Test"))
            )

            # Mixed conditions
            player = await async_query_first(
                session,
                Player,
                Player.is_banned == False,
                or_(Player.discord_id == 12345, Player.name == "Test")
            )
    """
    query = _build_query(model_class, *conditions)
    result = await session.scalars(query)  # Use scalars for ORM objects over execute
    return result.first()


async def async_query_all(
    session: AsyncSession, model_class: type[T], *conditions
) -> list[T]:
    """
    Async query for fetching multiple records using provided session.

    Args:
        session: The async session to use for the query
        model_class: The SQLAlchemy model class
        *conditions: Zero or more filter conditions (combined with AND unless using and_/or_)

    Returns:
        List of model instances

    Examples:
        async with async_session() as session:
            # All records
            players = await async_query_all(session, Player)

            # Single condition
            players = await async_query_all(session, Player, Player.is_banned == False)

            # Multiple conditions (AND)
            players = await async_query_all(
                session,
                Player,
                Player.is_banned == False,
                Player.discord_id.in_([123, 456, 789])
            )

            # Complex OR conditions
            players = await async_query_all(
                session,
                Player,
                or_(Player.discord_id == 12345, Player.name.like("%Admin%"))
            )
    """
    query = _build_query(model_class, *conditions)
    result = await session.scalars(query)  # Use scalars for ORM objects over execute
    return list(result.all())


async def async_update_by_id(
    session: AsyncSession, model_class: type, record_id: Any, **values
) -> bool:
    """
    Update a record by ID asynchronously using provided session.

    Args:
        session: The async session to use
        model_class: The SQLAlchemy model class
        record_id: The ID of the record to update
        **values: The fields to update

    Returns:
        True if a record was updated, False otherwise

    Example:
        async with async_session() as session:
            success = await async_update_by_id(session, Player, 123, name="NewName")
            await session.commit()
    """
    stmt = update(model_class).where(model_class.id == record_id).values(**values)
    result = await session.execute(stmt)
    return result.rowcount > 0


async def async_delete_by_id(
    session: AsyncSession, model_class: type, record_id: Any
) -> bool:
    """
    Delete a record by ID asynchronously.

    Args:
        session: The async session to use
        model_class: The SQLAlchemy model class
        record_id: The ID of the record to delete

    Returns:
        True if a record was deleted, False otherwise

    Example:
        async with async_session() as session:
            success = await async_delete_by_id(session, Player, 123)
            await session.commit()
    """
    result = await session.execute(
        delete(model_class).where(model_class.id == record_id)
    )
    return result.rowcount > 0


def run_async_in_sync(async_func: Callable[..., Any], *args, **kwargs):
    """
    Helper to run async functions in sync code during migration.
    Use sparingly - prefer converting to full async.

    Args:
        async_func: The async function to run
        *args, **kwargs: Arguments to pass to the function

    Returns:
        The result of the async function

    Example:
        # In a sync function that you haven't converted yet:
        async with async_session() as session:
            player = run_async_in_sync(async_query_first, session, Player, Player.id == 123)
    """
    import asyncio

    try:
        # Try to get the running loop
        asyncio.get_running_loop()
        raise RuntimeError(
            "run_async_in_sync cannot be called from within an async context. "
            "Use await directly instead."
        )
    except RuntimeError:
        # No running loop - safe to use asyncio.run
        return asyncio.run(async_func(*args, **kwargs))
