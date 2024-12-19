import asyncio
import datetime
import uuid
from logging import getLogger
from uuid import UUID

import discord.channel

from dncore import DNCoreAPI
from dncore.abc.serializables import Embed, MessageId, ChannelId
from dncore.command import oncommand, DEFAULT_GUILD_OWNER_GROUP, CommandContext
from dncore.command.errors import CommandUsageError
from dncore.discord.events import ReadyEvent
from dncore.event import onevent
from dncore.plugin import Plugin
from .abc import *
from .config import RequestBoardConfig, Board
from .database import RequestBoardDatabase
from .database.option import SQLiteOption, MySQLOption
from .inter import *

log = getLogger(__name__)


def create_request_form_embed(order: RequestOrder):
    em = Embed.info(title=order.title, content=None)
    if order.content:
        em.description = "**詳細内容**\n> " + "\n> ".join(order.content.split("\n"))
    em.add_field(name="MCID", value=order.mcid)
    em.add_field(name="送信者", value=f"<@{order.discord_user}>")
    if ch_id := order.discussion_channel:
        em.add_field(name="議論チャンネル", value=f"<#{ch_id}>")
    return em


def get_discussion_user_permission():
    return discord.PermissionOverwrite(
        view_channel=True,
    )


def get_me_permission():
    return discord.PermissionOverwrite(
        manage_permissions=True,
    )


class RequestBoardPlugin(Plugin):
    def __init__(self):
        self.use_intents = discord.Intents.guilds
        self.config = RequestBoardConfig(self.data_dir / "config.yml")
        self.db = RequestBoardDatabase()
        self._init_discord_ok = False
        #
        self.discussion_create_channel_view = self.create_discussion_channel_view()
        self.discussion_close_channel_view = self.create_discussion_close_channel_view()
        self.discussion_reopen_channel_view = self.create_discussion_reopen_channel_view()

    async def on_enable(self):
        self.config.load()
        await self.init_database()

        if not self._init_discord_ok and ((client := DNCoreAPI.client()) and client.is_ready()):
            await self._init_discord()

    async def on_disable(self):
        await self.close_database()

    @onevent(monitor=True)
    async def on_ready(self, _: ReadyEvent):
        if not self._init_discord_ok:
            await self._init_discord()

    async def init_database(self):
        if self.config.database.type == "mysql":
            conf = self.config.database.mysql
            await self.db.connect(MySQLOption(
                host=conf.host,
                port=conf.port,
                database=conf.database,
                username=conf.username,
                password=conf.password,
            ))
        else:
            conf = self.config.database.sqlite
            db_path = self.data_dir / conf.path
            db_path.parent.mkdir(exist_ok=True)
            await self.db.connect(SQLiteOption(
                file_path=db_path.as_posix(),
            ))

    async def close_database(self):
        await self.db.close()

    async def _init_discord(self):
        if not (client := DNCoreAPI.client()):
            self._init_discord_ok = True
            return

        client.add_view(self.discussion_create_channel_view)
        client.add_view(self.discussion_close_channel_view)
        client.add_view(self.discussion_reopen_channel_view)

        for board in self.config.boards:
            # register interaction
            if b_id := board.new_request_button_id:
                client.add_view(self.create_new_request_view(board, b_id))
            # check message content
            await asyncio.create_task(self.update_panel_content(board))

    #

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
                result = await self.create_and_send_new_request(board, values, inter.user)
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

    async def create_and_send_new_request(self, board: Board, values: RequestValues, user: discord.User) -> bool:
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

        order = RequestOrder(
            board_id=board.id,
            created=datetime.datetime.now(),
            discord_user=user.id,
            mcid=values.mcid,
            title=values.title,
            content=values.content,
        )

        if th_m := await self.create_request_thread(channel, order):
            order.forum_message = th_m.message.id
            order.forum_message_channel = th_m.message.channel.id
            order_id = await self.db.add_order(order)
            log.info("Created order (%s) by '%s' %s/%s", order_id, str(user), values.mcid, values.title)
            return True
        return False

    def create_discussion_channel_view(self):

        async def on_click(inter: discord.Interaction, res: discord.InteractionResponse):
            order = await self.db.get_order_by_forum_message_id(inter.message.id)
            if not order:
                log.warning("Cannot find order (from forum message '%s') by %s",
                            inter.message.id, inter.user)
                try:
                    await res.send_message(
                        embed=Embed.error(":warning: リクエスト内容がデータベースから見つかりませんでした"),
                        ephemeral=True,
                        delete_after=6,
                    )
                except (Exception,):
                    pass
                return

            if order.discussion_channel:
                try:
                    await inter.client.fetch_channel(order.discussion_channel)
                except discord.NotFound:
                    pass
                except discord.HTTPException:
                    try:
                        await res.send_message(
                            embed=Embed.error(f":warning: すでにチャンネルが作成されています: <#{order.discussion_channel}>"),
                            ephemeral=True,
                            delete_after=6,
                        )
                    except (Exception,):
                        pass
                    return
                else:
                    try:
                        await res.send_message(
                            embed=Embed.error(f":warning: すでにチャンネルが作成されています: <#{order.discussion_channel}>"),
                            ephemeral=True,
                            delete_after=6,
                        )
                    except (Exception,):
                        pass
                    return

            if not (board := self.get_board(order.board_id)):
                log.warning("Unknown board id: %s by %s", order.board_id, inter.user)
                try:
                    await res.send_message(
                        embed=Embed.error(
                            f":warning: すでにチャンネルが作成されています: <#{order.discussion_channel}>"),
                        ephemeral=True,
                        delete_after=6,
                    )
                except (Exception,):
                    pass
                return

            discussion = await self.create_discussion_channel(board, order)
            try:
                await res.send_message(
                    embed=Embed.info(
                        f":ok_hand: チャンネルが作成されました: <#{discussion.id}>"),
                    ephemeral=True,
                    delete_after=6,
                )
            except (Exception,):
                pass
            return

        return create_single_button_view("open_discussion_channel", "チャンネルを作成", on_click)

    async def create_request_thread(
        self, channel: discord.ForumChannel, order: RequestOrder,
    ) -> discord.channel.ThreadWithMessage | None:

        em = create_request_form_embed(order)
        view = self.discussion_create_channel_view

        try:
            return await channel.create_thread(name=order.title, embed=em, view=view)
        except discord.HTTPException as e:
            log.error(f"スレッドを作成/送信できませんでした: チャンネル {channel.id}: {e}")
            return

    async def send_panel_message(self, board: Board, channel: discord.abc.Messageable, **kwargs):
        fmt = board.panel_format or self.config.panel_format
        board.new_request_button_id = button_id = board.new_request_button_id or uuid.uuid4().hex
        m = await channel.send(embed=fmt, view=self.create_new_request_view(board, button_id), **kwargs)
        return m

    @staticmethod
    def create_discussion_channel_permission_overwrites(me: discord.Member, user: discord.Member):
        perms = discord.PermissionOverwrite()
        # noinspection PyDunderSlots,PyUnresolvedReferences
        perms.view_channel = True
        me_perms = discord.PermissionOverwrite()
        # noinspection PyDunderSlots,PyUnresolvedReferences
        # me_perms.manage_permissions = True  # なぜかカテゴリへの権限のみでは不十分だったので
        return {user: perms, me: me_perms}  # TODO: 閉じるが効かなくなる

    async def create_discussion_channel(self, board: Board, order: RequestOrder):
        if not ((category_id := board.discussion_channel_category) and (category_id := category_id.id)):
            raise ReadableError("カテゴリチャンネルが設定されていません")

        try:
            category = await DNCoreAPI.client().fetch_channel(category_id)
        except discord.HTTPException as e:
            log.error(f"Error in get category channel ({category_id}): {e}")
            raise ReadableError("カテゴリチャンネルを取得できませんでした")

        if not isinstance(category, discord.CategoryChannel):
            log.error("Not a category channel: %s/%s", category.id, category.name)
            raise ReadableError("カテゴリではないチャンネルがカテゴリチャンネルとして設定されています")

        try:
            if not (order_user := category.guild.get_member(order.discord_user)):
                order_user = await category.guild.fetch_member(order.discord_user)
        except discord.HTTPException as e:
            log.error(f"Error in get member ({order.discord_user}): {e}")
            raise ReadableError("リクエストユーザーを取得できませんでした")

        try:
            discussion = await category.guild.create_text_channel(
                name=order.title,
                category=category,
                position=0,
                overwrites={
                    category.guild.me: get_me_permission(),
                    order_user: get_discussion_user_permission(),
                },
            )

        except discord.HTTPException as e:
            log.error(f"Error in create discussion channel by {order.discord_user}: {e}")
            raise ReadableError(f"チャンネルを作成できませんでした: {e}")

        async with self.db.modify_order(order.id) as _order:
            _order.discussion_channel = discussion.id
            _order.discussion_closed = None

        log.info("Created discussion channel (%s) by '%s' %s/%s",
                 order.id, str(order_user), order.mcid, order.title)

        order = await self.db.get_order(order.id)
        DNCoreAPI.run_coroutine(self.update_board_forum_message(order))
        return discussion

    async def update_board_forum_message(self, order: RequestOrder):
        if not (m_id := order.forum_message) or not (ch_id := order.forum_message_channel):
            return False

        try:
            channel = await DNCoreAPI.client().fetch_channel(ch_id)
            message = await channel.fetch_message(m_id)
        except discord.NotFound:
            return False
        except discord.HTTPException as e:
            log.warning(f"Unable to update forum board message: {e}")
            return False

        em = create_request_form_embed(order)
        if not order.discussion_channel:
            view = self.discussion_create_channel_view
        elif order.discussion_closed:
            view = self.discussion_reopen_channel_view
        else:
            view = self.discussion_close_channel_view

        try:
            await message.edit(embed=em, view=view)
        except discord.HTTPException as e:
            log.warning(f"Error in update forum board message: {e}")
            return False
        return True

    async def on_close_channel_button(self, inter: discord.Interaction, res: discord.InteractionResponse):
        order = await self.db.get_order_by_forum_message_id(inter.message.id)
        if not order:
            log.warning("Cannot find order (from forum message '%s') by %s",
                        inter.message.id, inter.user)
            try:
                await res.send_message(
                    embed=Embed.error(":warning: リクエスト内容がデータベースから見つかりませんでした"),
                    ephemeral=True,
                    delete_after=6,
                )
            except (Exception,):
                pass
            return

        channel = None
        if order.discussion_channel:
            try:
                channel = await inter.client.fetch_channel(order.discussion_channel)
            except discord.NotFound:
                pass
            except discord.HTTPException as e:
                log.warning("Cannot fetch channel (%s): %s", order.discussion_channel, str(e))
                try:
                    await res.send_message(
                        embed=Embed.error(
                            f":warning: チャンネルを参照できませんでした: <#{order.discussion_channel}>"),
                        ephemeral=True,
                        delete_after=6,
                    )
                except (Exception,):
                    pass
                return

        if not channel:
            try:
                await res.send_message(
                    embed=Embed.error(":warning: チャンネルが見つかりませんでした"),
                    ephemeral=True,
                    delete_after=6,
                )
            except (Exception,):
                pass
            return

        await self.update_discussion_channel_closed(order, channel)
        try:
            await res.send_message(
                embed=Embed.info(
                    f":ok_hand: チャンネルを閉じました: <#{channel.id}>"),
                ephemeral=True,
                delete_after=6,
            )
        except (Exception,):
            pass

    def create_discussion_close_channel_view(self):
        return create_single_button_view(
            "close_discussion_channel",
            "チャンネルを閉じる",
            self.on_close_channel_button,
        )

    async def on_reopen_channel_button(self, inter: discord.Interaction, res: discord.InteractionResponse):
        order = await self.db.get_order_by_forum_message_id(inter.message.id)
        if not order:
            log.warning("Cannot find order (from forum message '%s') by %s",
                        inter.message.id, inter.user)
            try:
                await res.send_message(
                    embed=Embed.error(":warning: リクエスト内容がデータベースから見つかりませんでした"),
                    ephemeral=True,
                    delete_after=6,
                )
            except (Exception,):
                pass
            return

        channel = None
        if order.discussion_channel:
            try:
                channel = await inter.client.fetch_channel(order.discussion_channel)
            except discord.NotFound:
                pass
            except discord.HTTPException as e:
                log.warning("Cannot fetch channel (%s): %s", order.discussion_channel, str(e))
                try:
                    await res.send_message(
                        embed=Embed.error(
                            f":warning: チャンネルを参照できませんでした: <#{order.discussion_channel}>"),
                        ephemeral=True,
                        delete_after=6,
                    )
                except (Exception,):
                    pass
                return

        if not channel:
            if not (board := self.get_board(order.board_id)):
                raise ReadableError("ボード設定が見つかりません")

            discussion = await self.create_discussion_channel(board, order)
            try:
                await res.send_message(
                    embed=Embed.info(
                        f":ok_hand: チャンネルが作成されました: <#{discussion.id}>"),
                    ephemeral=True,
                    delete_after=6,
                )
            except (Exception,):
                pass
            return

        await self.update_discussion_channel_reopen(order, channel)
        try:
            await res.send_message(
                embed=Embed.info(
                    f":ok_hand: チャンネルを開きました: <#{channel.id}>"),
                ephemeral=True,
                delete_after=6,
            )
        except (Exception,):
            pass

    def create_discussion_reopen_channel_view(self):
        return create_single_button_view(
            "reopen_discussion_channel",
            "チャンネルを開く",
            self.on_reopen_channel_button,
        )

    async def update_discussion_channel_closed(self, order: RequestOrder, channel: discord.TextChannel):
        try:
            if not (order_user := channel.guild.get_member(order.discord_user)):
                order_user = await channel.guild.fetch_member(order.discord_user)
        except discord.HTTPException as e:
            log.error(f"Error in get member ({order.discord_user}): {e}")
            raise ReadableError("リクエストユーザーを取得できませんでした")

        try:
            await channel.set_permissions(order_user, overwrite=None)
            # await channel.edit(name="closed-" + order.title)

        except discord.HTTPException as e:
            log.error(f"Error in update discussion channel {channel.id}: {e}")
            raise ReadableError("チャンネルを編集できませんでした")

        async with self.db.modify_order(order.id) as _order:
            # _order.discussion_channel = None
            _order.discussion_closed = datetime.datetime.now()

        log.info("Closed discussion channel (%s) by '%s' %s/%s",
                 order.id, str(order_user), order.mcid, order.title)

        order = await self.db.get_order(order.id)
        DNCoreAPI.run_coroutine(self.update_board_forum_message(order))
        return True

    async def update_discussion_channel_reopen(self, order: RequestOrder, channel: discord.TextChannel):
        try:
            if not (order_user := channel.guild.get_member(order.discord_user)):
                order_user = await channel.guild.fetch_member(order.discord_user)
        except discord.HTTPException as e:
            log.error(f"Error in get member ({order.discord_user}): {e}")
            raise ReadableError("リクエストユーザーを取得できませんでした")

        try:
            await channel.set_permissions(order_user, overwrite=get_discussion_user_permission())
            await channel.edit(name=order.title)

        except discord.HTTPException as e:
            log.error(f"Error in update discussion channel {channel.id}: {e}")
            raise ReadableError("チャンネルを編集できませんでした")

        async with self.db.modify_order(order.id) as _order:
            _order.discussion_channel = channel.id
            _order.discussion_closed = None

        log.info("Reopen discussion channel (%s) by '%s' %s/%s",
                 order.id, str(order_user), order.mcid, order.title)

        order = await self.db.get_order(order.id)
        DNCoreAPI.run_coroutine(self.update_board_forum_message(order))
        return True

    def get_guild_boards(self, guild_id: int):
        return list(filter(lambda b: b.guild == guild_id, self.config.boards))

    def get_board(self, board_id: UUID):
        for board in self.config.boards:
            if board.id == board_id:
                return board

    # settings

    @oncommand(defaults=DEFAULT_GUILD_OWNER_GROUP)
    async def cmd_requestboard(self, ctx: CommandContext):
        """
        {command} [list]
        {command} add (ﾊﾟﾈﾙﾁｬﾝﾈﾙID) (ﾌｫｰﾗﾑﾁｬﾝﾈﾙID) [議論ﾁｬﾝﾈﾙｶﾃｺﾞﾘID]
        {command} <remove/preview/send> (ｲﾝﾃﾞｯｸｽ)
        {command} setChCate (ｲﾝﾃﾞｯｸｽ) (議論ﾁｬﾝﾈﾙｶﾃｺﾞﾘID / unset)
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
                return await ctx.send_warn(f":warning: <#{forum_channel_id}> がフォーラムチャンネルではありません")

            discussion_channel_category = None
            try:
                discussion_channel_category_id = args.get_channel(0)
                args.pop(0)
            except IndexError:
                pass
            except ValueError:
                return await ctx.send_warn(":grey_exclamation: カテゴリチャンネルを数値で指定してください")
            else:
                try:
                    discussion_channel_category = await ctx.client.fetch_channel(discussion_channel_category_id, force=True)
                except discord.HTTPException as e:
                    return await ctx.send_warn(f":warning: <#{discussion_channel_category_id}> にアクセスできません: {e}")
                else:
                    if not isinstance(discussion_channel_category, discord.CategoryChannel):
                        return await ctx.send_warn(
                            f":warning: <#{discussion_channel_category_id}> がカテゴリチャンネルではありません")

            board = Board()
            board.id = uuid.uuid4()
            board.guild = ctx.guild.id
            board.panel_message = MessageId(message_id=None, channel_id=panel_channel.id)
            board.forum_channel = ChannelId(forum_channel_id)
            if discussion_channel_category:
                board.discussion_channel_category = discussion_channel_category.id

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

        elif mode in ("setchannelcategory", "setchcate"):
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

            if (args.get(0) or "").lower() == "unset":
                board.discussion_channel_category = None

            else:
                try:
                    discussion_channel_category_id = args.get_channel(0)
                    args.pop(0)
                except IndexError:
                    raise CommandUsageError()
                except ValueError:
                    return await ctx.send_warn(":grey_exclamation: カテゴリチャンネルを数値で指定してください")
                else:
                    try:
                        discussion_channel_category = await ctx.client.fetch_channel(discussion_channel_category_id, force=True)
                    except discord.HTTPException as e:
                        return await ctx.send_warn(f":warning: <#{discussion_channel_category_id}> にアクセスできません: {e}")
                    else:
                        if not isinstance(discussion_channel_category, discord.CategoryChannel):
                            return await ctx.send_warn(
                                f":warning: <#{discussion_channel_category_id}> がカテゴリチャンネルではありません")
                board.discussion_channel_category = discussion_channel_category.id

            self.config.save()
            if board.discussion_channel_category:
                m_text = "議論チャンネルを作成するカテゴリチャンネルを設定しました"
            else:
                m_text = "議論チャンネルの設定を解除しました"

            return await ctx.send_info(f":ok_hand: {m_text}")

        else:
            raise CommandUsageError()
