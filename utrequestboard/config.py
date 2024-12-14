from dncore.abc.serializables import MessageId, ChannelId, Embed
from dncore.configuration import ConfigValues
from dncore.configuration.files import FileConfigValues


class Board(ConfigValues):
    guild: int
    panel_message: MessageId
    forum_channel: ChannelId
    panel_format: Embed | None = None
    new_request_button_id: str | None = None


class RequestBoardConfig(FileConfigValues):
    # 設定されたボード
    # 追加や登録はコマンドから行ってください
    boards: list[Board]
    # 再度作成できるようになるまでの時間 (分)
    create_cool_times: int | None = None
    # パネルの内容
    panel_format = Embed("作成ボタンからリクエストを送信できます", title="リクエストの送信")
