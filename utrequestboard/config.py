import uuid
from typing import Iterable
from uuid import UUID

from dncore.abc import ObjectSerializer
from dncore.abc.serializables import MessageId, ChannelId, Embed
from dncore.configuration import ConfigValues
from dncore.configuration.files import FileConfigValues


class UUIDSerializer(ObjectSerializer):
    def check(self, clazz):
        return issubclass(clazz, UUID)

    def serialize(self, obj: UUID):
        return obj.hex

    @classmethod
    def deserialize(cls, value):
        return uuid.UUID(value)


class Board(ConfigValues):
    id: UUID
    guild: int
    panel_message: MessageId
    forum_channel: ChannelId
    panel_format: Embed | None = None
    new_request_button_id: str | None = None
    discussion_channel_category: ChannelId | None

    @classmethod
    def _serializers(cls) -> Iterable[ObjectSerializer]:
        return [UUIDSerializer()]


class SQLiteConfig(ConfigValues):
    path: str = "database.db"


class MySQLConfig(ConfigValues):
    host: str = "localhost"
    port: int = 3306
    database: str = "utrequestboard"
    username: str = "root"
    password: str = "abcdefg"


class DatabaseSection(ConfigValues):
    # タイプ: sqlite, mysql
    type: str = "sqlite"

    sqlite: SQLiteConfig
    mysql: MySQLConfig


class RequestBoardConfig(FileConfigValues):
    # 設定されたボード
    # 追加や登録はコマンドから行ってください
    boards: list[Board]
    # 再度作成できるようになるまでの時間 (分)
    create_cool_times: int | None = None
    # パネルの内容
    panel_format = Embed("作成ボタンからリクエストを送信できます", title="リクエストの送信")

    # データベース設定
    database: DatabaseSection
