from dataclasses import dataclass, field

from sqlalchemy import URL

__all__ = [
    "SQLiteOption",
    "MySQLOption",
]


class DatabaseOption:
    def create_url(self) -> URL:
        raise NotImplementedError


@dataclass
class SQLiteOption(DatabaseOption):
    file_path: str
    query: dict = field(default_factory=lambda: dict(charset="utf8mb4"))

    def create_url(self) -> URL:
        return URL.create(
            drivername="sqlite+aiosqlite",
            database=self.file_path,
            query=self.query,
        )


@dataclass
class MySQLOption(DatabaseOption):
    host: str
    port: int
    database: str
    username: str
    password: str
    query: dict = field(default_factory=lambda: dict(charset="utf8mb4"))

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
