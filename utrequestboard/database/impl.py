import asyncio
import uuid
from contextlib import asynccontextmanager
from logging import getLogger
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker, AsyncEngine, create_async_engine

from .option import DatabaseOption
from ..abc import *

__all__ = [
    "RequestBoardDatabase",
]
log = getLogger(__name__)


class RequestBoardDatabase(object):
    def __init__(self):
        self._engine = None  # type: AsyncEngine | None
        self._lock = asyncio.Lock()

    async def connect(self, db_option: DatabaseOption):
        if self._engine:
            raise RuntimeError("Current engine not closed")

        log.debug("Creating database engine")
        self._engine = create_async_engine(db_option.create_url(), echo=False)

        async with self._engine.begin() as conn:  # type: AsyncConnection
            await conn.run_sync(Base.metadata.create_all)

        log.debug("Connected database")

    async def close(self):
        if self._engine is None:
            return

        await self._engine.dispose()
        self._engine = None
        log.debug("Closed database")

    def session(self) -> AsyncSession:
        return async_sessionmaker(autoflush=True, bind=self._engine)()

    #

    async def get_order(self, order: UUID) -> RequestOrder | None:
        async with self.session() as db:
            result = await db.execute(select(RequestOrder).where(RequestOrder.id == order))
            try:
                return result.one()[0]
            except NoResultFound:
                return None

    async def get_order_by_forum_message_id(self, forum_message_id: int) -> RequestOrder | None:
        async with self.session() as db:
            result = await db.execute(select(RequestOrder).where(RequestOrder.forum_message == forum_message_id))
            try:
                return result.one()[0]
            except NoResultFound:
                return None

    async def add_order(self, order: RequestOrder):
        if order.id is None:
            order.id = uuid.uuid4()
        order_id = order.id
        async with self._lock:
            async with self.session() as db:
                db.add(order)
                await db.commit()
        return order_id

    async def remove_order(self, order: RequestOrder | UUID):
        async with self._lock:
            async with self.session() as db:
                if isinstance(order, RequestOrder):
                    await db.delete(order)
                else:
                    await db.execute(delete(RequestOrder).where(RequestOrder.id == order))
                await db.commit()

    @asynccontextmanager
    async def modify_order(self, order: UUID):
        async with self._lock:
            async with self.session() as db:
                result = await db.execute(select(RequestOrder).where(RequestOrder.id == order))
                try:
                    order = result.one()[0]
                except NoResultFound:
                    raise

                yield order
                db.add(order)
                await db.commit()
