from logging import getLogger
from typing import Callable, Awaitable, NamedTuple

import discord.ui
from discord import Interaction
from discord._types import ClientT

from dncore.abc.serializables import Embed

log = getLogger(__name__)
__all__ = [
    "custom_id_new_request_prefix",
    "create_new_request_view",
    "create_discussion_channel_view",
    "RequestValues",
    "create_request_modal",
]
custom_id_new_request_prefix = "dncore:utrequestboard:new_request:"
custom_id_open_discussion_channel = "dncore:utrequestboard:open_discussion_channel"


def create_new_request_view(id: str, on_click: Callable[[discord.InteractionResponse], Awaitable[None]]):
    custom_id = custom_id_new_request_prefix + id

    class NewRequestView(discord.ui.View):
        @discord.ui.button(custom_id=custom_id, label="作成")
        async def click_new(self, inter: discord.Interaction, _):
            # noinspection PyTypeChecker
            res: discord.InteractionResponse = inter.response
            try:
                await on_click(res)
            except Exception as e:
                log.exception("Exception in handling button new_request", exc_info=e)
                try:
                    await res.send_message(
                        embed=Embed.error(":warning: 内部エラーが発生しました。"),
                        ephemeral=True,
                        delete_after=6,
                    )
                except (Exception,):
                    pass

    return NewRequestView(timeout=None)


def create_discussion_channel_view(on_click: Callable[[discord.Interaction, discord.InteractionResponse], Awaitable[None]]):
    custom_id = custom_id_open_discussion_channel

    class OpenDiscussionChannelView(discord.ui.View):
        @discord.ui.button(custom_id=custom_id, label="チャンネルを作成")
        async def click_new(self, inter: discord.Interaction, _):
            # noinspection PyTypeChecker
            res: discord.InteractionResponse = inter.response
            try:
                await on_click(inter, res)
            except Exception as e:
                log.exception("Exception in handling button open_discussion", exc_info=e)
                try:
                    await res.send_message(
                        embed=Embed.error(":warning: 内部エラーが発生しました。"),
                        ephemeral=True,
                        delete_after=6,
                    )
                except (Exception,):
                    pass

    return OpenDiscussionChannelView(timeout=None)


class RequestValues(NamedTuple):
    mcid: str
    title: str
    content: str | None


def create_request_modal(
    on_submit_: Callable[[discord.Interaction, discord.InteractionResponse, RequestValues], Awaitable[None]],
):
    class RequestModal(discord.ui.Modal, title="内容を入力してください"):
        input_mcid = discord.ui.TextInput(label="MCID", required=True)
        input_title = discord.ui.TextInput(label="タイトル", required=True)
        input_content = discord.ui.TextInput(label="詳しい内容", required=False, style=discord.TextStyle.paragraph)

        async def on_submit(self, inter: Interaction[ClientT], /) -> None:
            # noinspection PyTypeChecker
            res: discord.InteractionResponse = inter.response
            try:
                await on_submit_(inter, res, RequestValues(
                    self.input_mcid.value,
                    self.input_title.value,
                    self.input_content.value
                ))
            except Exception as e:
                log.exception("Exception in handling submit request", exc_info=e)
                try:
                    await res.send_message(
                        embed=Embed.error(":warning: 内部エラーが発生しました。"),
                        ephemeral=True,
                        delete_after=6,
                    )
                except (Exception,):
                    pass

    return RequestModal()
