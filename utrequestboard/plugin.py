import asyncio
import uuid
from logging import getLogger

import discord

from dncore import DNCoreAPI
from dncore.abc.serializables import Embed, MessageId, ChannelId
from dncore.command import oncommand, DEFAULT_GUILD_OWNER_GROUP, CommandContext
from dncore.command.errors import CommandUsageError
from dncore.discord.events import ReadyEvent
from dncore.event import onevent
from dncore.plugin import Plugin
from .inter import *
from .config import RequestBoardConfig, Board

log = getLogger(__name__)


class RequestBoardPlugin(Plugin):
    def __init__(self):
        self.use_intents = discord.Intents.guilds
        self.config = RequestBoardConfig(self.data_dir / "config.yml")
        self._init_discord_ok = False

    async def on_enable(self):
        self.config.load()

        if not self._init_discord_ok and ((client := DNCoreAPI.client()) and client.is_ready()):
            await self._init_discord()

    @onevent(monitor=True)
    async def on_ready(self, _: ReadyEvent):
        if not self._init_discord_ok:
            await self._init_discord()

    async def _init_discord(self):
        if not (client := DNCoreAPI.client()):
            self._init_discord_ok = True
            return

        for board in self.config.boards:
            # register interaction
            if b_id := board.new_request_button_id:
                client.add_view(self.create_new_request_view(board, b_id))
            # check message content
            await asyncio.create_task(self.update_panel_content(board))

    async def update_panel_content(self, board: Board):
        if not (m_id := board.panel_message) or m_id.id is None or m_id.channel_id is None:
            return

        try:
            m = await m_id.fetch()
        except discord.HTTPException as e:
            log.warning(f"Failed to fetch panel: {e}")
            return
        except (ValueError, RuntimeError):
            return

        if m.author.id != DNCoreAPI.client().user.id:
            return  # no editable

        fmt = board.panel_format or self.config.panel_format
        if m.embeds and any(
                em.title == fmt.title and em.description == fmt.description
                for em in m.embeds
        ):
            return  # no changed

        log.debug("Updating panel: %s/%s", str(m.guild), str(m.channel))
        try:
            await m.edit(embed=fmt)
        except discord.HTTPException as e:
            log.warning(f"Failed to update panel: {e}")

    def create_new_request_view(self, board: Board, b_id: str):

        async def on_submit(inter: discord.Interaction, res: discord.InteractionResponse, values: RequestValues):
            try:
                result = await self.send_new_request(board, values, inter.user)
            except Exception as e:
                log.exception("Exception in send_new_request", exc_info=e)
                raise

            delete_after = max(1, self.config.create_cool_times or 0) * 60
            try:
                await res.send_message(
                    embed=(Embed.info(":ok_hand: 内容を送信しました")
                           if result else Embed.warn(":exclamation: 内容を送信できませんでした")),
                    ephemeral=True,
                    delete_after=delete_after,
                )
            except (Exception,):
                pass

        async def on_click(res: discord.InteractionResponse):
            modal = create_request_modal(on_submit)
            await res.send_modal(modal)

        return create_new_request_view(b_id, on_click)

    async def send_new_request(self, board: Board, values: RequestValues, user: discord.User) -> bool:
        if not (ch_id := board.forum_channel.id):
            log.warning("フォーラムチャンネルIDが未設定です: b_id: %s", board.new_request_button_id)
            return False

        try:
            channel = await DNCoreAPI.client().fetch_channel(ch_id)
        except discord.HTTPException as e:
            log.warning("フォーラムチャンネルを取得できませんでした: (ch:%s, b_id:%s): %s",
                        ch_id, board.new_request_button_id, str(e))
            return False

        if not isinstance(channel, discord.ForumChannel):
            log.warning("指定されたチャンネルはフォーラムチャンネルではありません: ch: %s, b_id: %s",
                        ch_id, board.new_request_button_id)
            return False

        return await self.create_request_thread(channel, board, values, user)

    # noinspection PyMethodMayBeStatic
    async def create_request_thread(self, channel: discord.ForumChannel,
                                    board: Board, values: RequestValues, user: discord.User):

        em = Embed.info(title=values.title, content=None)
        if values.content:
            em.description = "**詳細内容**\n> " + "\n> ".join(values.content.split("\n"))
        em.add_field(name="MCID", value=values.mcid)
        em.add_field(name="送信者", value=user.mention)

        view = AAAA  # TODO: current work

        try:
            _ = await channel.create_thread(name=values.title, embed=em, view=view)
        except discord.HTTPException as e:
            log.error(f"スレッドを作成/送信できませんでした: チャンネル {channel.id}: {e}")
            return False

        return True

    async def send_panel_message(self, board: Board, channel: discord.abc.Messageable, **kwargs):
        fmt = board.panel_format or self.config.panel_format
        board.new_request_button_id = button_id = board.new_request_button_id or uuid.uuid4().hex
        m = await channel.send(embed=fmt, view=self.create_new_request_view(board, button_id), **kwargs)
        return m

    def get_guild_boards(self, guild_id: int):
        return list(filter(lambda b: b.guild == guild_id, self.config.boards))

    # settings

    @oncommand(defaults=DEFAULT_GUILD_OWNER_GROUP)
    async def cmd_requestboard(self, ctx: CommandContext):
        """
        {command} [list]
        {command} add (ﾊﾟﾈﾙﾁｬﾝﾈﾙID) (ﾌｫｰﾗﾑﾁｬﾝﾈﾙID)
        {command} <remove/preview/send> (ｲﾝﾃﾞｯｸｽ)
        """
        args = ctx.args
        try:
            mode = args.pop(0).lower()
        except IndexError:
            mode = "list"

        if mode == "list":
            boards = self.get_guild_boards(ctx.guild.id)
            if not boards:
                return await ctx.send_warn(":warning: １つも設定されていません")

            def _format(n, b: Board):
                if b.panel_message.id:
                    channel_name = f"https://discord.com/channels/0/{b.panel_message.channel_id}/{b.panel_message.id}"
                else:
                    channel_name = f"<#{b.panel_message.channel_id}>"
                    
                return f"{n}. {channel_name} -> <#{b.forum_channel.id}>"

            lines = "\n".join(_format(n, b) for n, b in enumerate(boards, 1))
            return await ctx.send_info(":gear: 設定されているパネル\n" + lines)

        elif mode == "add":
            try:
                panel_channel_id = args.get_channel(0)
                args.pop(0)
            except IndexError:
                raise CommandUsageError()
            except ValueError:
                return await ctx.send_warn(":grey_exclamation: パネルチャンネルを数値で指定してください")
            try:
                panel_channel = await ctx.client.fetch_channel(panel_channel_id, force=True)
            except discord.HTTPException as e:
                return await ctx.send_warn(f":warning: <#{panel_channel_id}> にアクセスできません: {e}")

            try:
                forum_channel_id = args.get_channel(0)
                args.pop(0)
            except IndexError:
                raise CommandUsageError()
            except ValueError:
                return await ctx.send_warn(":grey_exclamation: フォーラムチャンネルを数値で指定してください")
            try:
                forum_channel = await ctx.client.fetch_channel(forum_channel_id, force=True)
            except discord.HTTPException as e:
                return await ctx.send_warn(f":warning: <#{forum_channel_id}> にアクセスできません: {e}")

            if not isinstance(forum_channel, discord.ForumChannel):
                return await ctx.send_warn(f":warning: <#{forum_channel_id}> チャンネルがフォーラムチャンネルではありません")

            board = Board()
            board.guild = ctx.guild.id
            board.panel_message = MessageId(message_id=None, channel_id=panel_channel.id)
            board.forum_channel = ChannelId(forum_channel_id)

            self.config.boards.append(board)
            self.config.save()

            boards = self.get_guild_boards(ctx.guild.id)

            return await ctx.send_info(
                ":ok_hand: 新しいボード(#{index})を追加しました。`{command} send {index}` でボードを送信できます。",
                args=dict(command=ctx.prefix + ctx.execute_name, index=len(boards)),
            )

        elif mode == "remove":
            try:
                board_index = int(args.pop(0))
            except IndexError:
                return await ctx.send_warn(":grey_exclamation: ボード番号を指定してください")
            except ValueError:
                return await ctx.send_warn(":grey_exclamation: ボード番号を数値で指定してください")

            boards = self.get_guild_boards(ctx.guild.id)
            try:
                if not 0 < board_index <= len(boards):
                    raise IndexError
                board = boards[board_index - 1]
            except IndexError:
                return await ctx.send_warn(f":warning: 1 から {len(boards)} で指定してください")

            self.config.boards.remove(board)
            self.config.save()

            has_error = False
            try:
                await (await board.panel_message.fetch()).delete()
            except discord.NotFound:
                pass  # ignored
            except (ValueError, discord.HTTPException) as e:
                log.warning(f"Failed to delete panel message: {board.panel_message.id}: {e}")
                has_error = True

            return await ctx.send_info(":ok_hand: 指定されたボードを削除しました" + ["", "。(パネルを削除できませんでした)"][has_error])

        elif mode == "preview":
            try:
                board_index = int(args.pop(0))
            except IndexError:
                return await ctx.send_warn(":grey_exclamation: ボード番号を指定してください")
            except ValueError:
                return await ctx.send_warn(":grey_exclamation: ボード番号を数値で指定してください")

            boards = self.get_guild_boards(ctx.guild.id)
            try:
                if not 0 < board_index <= len(boards):
                    raise IndexError
                board = boards[board_index-1]
            except IndexError:
                return await ctx.send_warn(f":warning: 1 から {len(boards)} で指定してください")

            # check forum channel
            try:
                async with ctx.typing():
                    forum_channel = await board.forum_channel.fetch()
            except ValueError:
                return await ctx.send_warn(":exclamation: 内部エラーまたは正しくボードが設定されていません。再設定してみてください。")
            except discord.HTTPException as e:
                return await ctx.send_warn(f":warning: <#{board.forum_channel.id}> にアクセスできません: {e}")

            forum_error = None
            if isinstance(forum_channel, discord.ForumChannel):
                if not forum_channel.permissions_for(ctx.guild.me).create_public_threads:  # とりあえず入れておく
                    forum_error = f"<#{board.forum_channel.id}> チャンネルの管理権限がありません"
            else:
                forum_error = f"<#{board.forum_channel.id}> チャンネルがフォーラムチャンネルではありません"

            # send preview to current channel
            try:
                await self.send_panel_message(board, ctx.channel, delete_after=60)
            except discord.HTTPException as e:
                return await ctx.send_error(f":warning: パネルメッセージを送信できませんでした: {e}")
            except Exception as e:
                log.error("Exception in send preview panel", exc_info=e)
                return await ctx.send_error(":warning: 内部エラーが発生しました")

            # send forum check error
            if forum_error:
                await ctx.send_warn(":grey_exclamation: " + forum_error)

        elif mode == "send":
            try:
                board_index = int(args.pop(0))
            except IndexError:
                return await ctx.send_warn(":grey_exclamation: ボード番号を指定してください")
            except ValueError:
                return await ctx.send_warn(":grey_exclamation: ボード番号を数値で指定してください")

            boards = self.get_guild_boards(ctx.guild.id)
            try:
                if not 0 < board_index <= len(boards):
                    raise IndexError
                board = boards[board_index - 1]
            except IndexError:
                return await ctx.send_warn(f":warning: 1 から {len(boards)} で指定してください")

            panel_channel_id = board.panel_message
            try:
                if panel_channel_id.channel_id is None:
                    raise ValueError
                async with ctx.typing():
                    panel_channel = await ctx.guild.fetch_channel(panel_channel_id.channel_id)
            except ValueError:
                raise  # bug
            except discord.HTTPException as e:
                return await ctx.send_warn(f":warning: パネルを送信するチャンネルを取得できませんでした: {e}")

            # check forum channel
            try:
                async with ctx.typing():
                    forum_channel = await board.forum_channel.fetch()
            except ValueError:
                return await ctx.send_warn(":exclamation: 内部エラーまたは正しくボードが設定されていません。再設定してみてください。")
            except discord.HTTPException as e:
                return await ctx.send_warn(f":warning: <#{board.forum_channel.id}> にアクセスできません: {e}")

            # send forum channel error
            if not isinstance(forum_channel, discord.ForumChannel):
                return await ctx.send_warn(f":grey_exclamation: <# {board.forum_channel.id}> チャンネルがフォーラムチャンネルではありません")
            if not forum_channel.permissions_for(ctx.guild.me).create_public_threads:  # とりあえず
                return await ctx.send_warn(f":grey_exclamation: <#{board.forum_channel.id}> チャンネルの管理権限がありません")

            # send panel
            try:
                async with ctx.typing():
                    m = await self.send_panel_message(board, panel_channel)
            except discord.HTTPException as e:
                return await ctx.send_error(f":warning: パネルメッセージを送信できませんでした: {e}")
            except Exception as e:
                log.error("Exception in send preview panel", exc_info=e)
                return await ctx.send_error(":warning: 内部エラーが発生しました")

            board.panel_message = MessageId(m.id, m.channel.id)
            self.config.save()
            await ctx.send_info(f":ok_hand: パネルメッセージを送信しました: {m.jump_url}")

        else:
            raise CommandUsageError()
