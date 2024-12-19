from sqlalchemy import Column, Uuid, Integer, String, DateTime
from sqlalchemy.orm import declarative_base

__all__ = [
    "Base",
    "RequestOrder",
    "ReadableError",
]


class ReadableError(Exception):
    pass


Base = declarative_base()


class RequestOrder(Base):
    __tablename__ = "orders"
    __table_args__ = {
        "sqlite_autoincrement": True,
    }

    id = Column(Uuid, nullable=False, unique=True, primary_key=True)
    board_id = Column(Uuid, nullable=False)
    created = Column(DateTime(), nullable=False)
    discord_user = Column(Integer, nullable=False)
    mcid = Column(String, nullable=False)
    title = Column(String, nullable=False)
    content = Column(String, nullable=True)
    #
    forum_message = Column(Integer, nullable=True)
    forum_message_channel = Column(Integer, nullable=True)
    discussion_channel = Column(Integer, nullable=True)
    discussion_closed = Column(DateTime(), nullable=True)
