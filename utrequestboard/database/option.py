from pathlib import Path
from typing import NamedTuple

from sqlalchemy import URL

__all__ = [
    "SQLiteOption",
    "MySQLOption",
]


class DatabaseOption:
    def create_url(self) -> URL:
        raise NotImplementedError


class SQLiteOption(DatabaseOption, NamedTuple):
    file_path: str
    query: dict = dict(
        charset="utf8mb4",
    )

    def create_url(self) -> URL:
        return URL.create(
            drivername="sqlite+aiosqlite",
            database=Path(self.file_path).as_posix(),
            query=self.query,
        )


class MySQLOption(DatabaseOption, NamedTuple):
    host: str
    port: int
    database: str
    username: str
    password: str
    query: dict = dict(
        charset="utf8mb4",
    )

    def create_url(self) -> URL:
        return URL.create(
            drivername="mysql+aiomysql",
            host=self.host,
            port=self.port,
            database=self.database,
            username=self.username,
            password=self.password,
            query=self.query,
        )
