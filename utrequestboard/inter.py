from logging import getLogger
from typing import Callable, Awaitable, NamedTuple

import discord.ui
from discord import Interaction
from discord._types import ClientT

from dncore.abc.serializables import Embed
from .abc import ReadableError

log = getLogger(__name__)
__all__ = [
    "custom_id_new_request_prefix",
    "create_new_request_view",
    "create_single_button_view",
    "RequestValues",
    "create_request_modal",
]
custom_id_new_request_prefix = "dncore:utrequestboard:new_request:"
custom_id_prefix = "dncore:utrequestboard:"


async def handle_error(error: BaseException, res: discord.InteractionResponse):
    try:
        await res.send_message(embed=Embed.error(
            f":warning: {str(error)}" if isinstance(error, ReadableError) else ":warning: 内部エラーが発生しました。"
        ), ephemeral=True, delete_after=15 if isinstance(error, ReadableError) else 6)
    except discord.HTTPException as e:
        log.warning(f"Failed to send response: {e}")
    except Exception as e:
        log.warning(f"Failed to send response", exc_info=e)


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
                await handle_error(e, res)

    return NewRequestView(timeout=None)


def create_single_button_view(
    button_id: str, label: str, on_click: Callable[[discord.Interaction, discord.InteractionResponse], Awaitable[None]],
):

    class CreateDiscussionChannelView(discord.ui.View):
        @discord.ui.button(custom_id=custom_id_prefix + button_id, label=label)
        async def click_new(self, inter: discord.Interaction, _):
            # noinspection PyTypeChecker
            res: discord.InteractionResponse = inter.response
            try:
                await on_click(inter, res)
            except Exception as e:
                log.exception("Exception in handling button %s", button_id, exc_info=e)
                await handle_error(e, res)

    return CreateDiscussionChannelView(timeout=None)


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
                await handle_error(e, res)

    return RequestModal()
