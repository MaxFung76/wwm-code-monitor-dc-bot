from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .bahamut import BahamutMonitor, extract_codes_from_text
from .config import Settings
from .models import CodeStatus, RedeemCode
from .storage import Storage

PANEL_STATE_KEY = "panel_message_id"
PANEL_CHANNEL_STATE_KEY = "panel_channel_id"


class AddCodeModal(discord.ui.Modal, title="新增兌換碼"):
    codes_input = discord.ui.TextInput(
        label="請輸入兌換碼",
        placeholder="可一次貼多筆，機器人會自動拆行與去重",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
    )

    def __init__(self, bot: "RedeemCodeBot") -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        codes = [
            RedeemCode(code=value, status=CodeStatus.ACTIVE, note="added from modal")
            for value in extract_codes_from_text(self.codes_input.value)
        ]

        if not codes:
            await interaction.response.send_message("沒有辨識到任何兌換碼。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        result = await self.bot.storage.reconcile_codes(
            codes,
            source_url=f"discord://channel/{interaction.channel_id}",
            source_type="manual",
        )
        if result.new_active_codes:
            await self.bot.announce_new_codes(
                result.new_active_codes,
                title="人工新增兌換碼",
            )

        joined_codes = ", ".join(item.code for item in codes)
        await interaction.followup.send(
            f"已處理 {len(codes)} 筆兌換碼：{joined_codes}",
            ephemeral=True,
        )
        await self.bot.repost_panel()

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        print(f"Modal error: {type(error).__name__} {error}", flush=True)
        if interaction.response.is_done():
            await interaction.followup.send("操作失敗，請稍後再試。", ephemeral=True)
        else:
            await interaction.response.send_message("操作失敗，請稍後再試。", ephemeral=True)


class ControlPanelView(discord.ui.View):
    def __init__(self, bot: "RedeemCodeBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="新增兌換碼",
        style=discord.ButtonStyle.primary,
        custom_id="panel:add-code",
    )
    async def add_code_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(AddCodeModal(self.bot))

    @discord.ui.button(
        label="查詢當月列表",
        style=discord.ButtonStyle.secondary,
        custom_id="panel:monthly-list",
    )
    async def monthly_list_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        report = await self.bot.build_monthly_report()
        await interaction.followup.send(report, ephemeral=True)
        await self.bot.repost_panel()

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        _: discord.ui.Item[discord.ui.View],
    ) -> None:
        print(f"View error: {type(error).__name__} {error}", flush=True)
        if interaction.response.is_done():
            await interaction.followup.send("操作失敗，請稍後再試。", ephemeral=True)
        else:
            await interaction.response.send_message("操作失敗，請稍後再試。", ephemeral=True)


class RedeemCodeBot(commands.Bot):
    def __init__(self, settings: Settings, storage: Storage) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.messages = True
        intents.message_content = True

        super().__init__(command_prefix="!", intents=intents)
        self.settings = settings
        self.storage = storage
        self.monitor = BahamutMonitor(
            forum_url=settings.forum_url,
            timeout_seconds=settings.request_timeout_seconds,
        )
        self.panel_lock = asyncio.Lock()
        self._initial_sync_done = False
        self._resolved_channel_logged = False

    async def setup_hook(self) -> None:
        await self.storage.initialize()
        self.add_view(ControlPanelView(self))
        self.monitor_forum.change_interval(minutes=self.settings.monitor_interval_minutes)
        self.monitor_forum.start()
        self.ensure_panel.change_interval(minutes=5)
        self.ensure_panel.start()
        self.tree.add_command(
            app_commands.Command(
                name="setup_buttons",
                description="在目前頻道重新發送兌換碼面板",
                callback=self._setup_buttons,
            )
        )
        if self.settings.discord_guild_id:
            await self.tree.sync(guild=discord.Object(id=self.settings.discord_guild_id))

    async def on_ready(self) -> None:
        print(
            f"Logged in as {self.user} ({self.user.id if self.user else 'unknown'})",
            flush=True,
        )
        if not self._initial_sync_done:
            self._initial_sync_done = True
            await self.run_monitor_cycle(reason="startup")
            try:
                await self.repost_panel()
            except Exception as exc:
                print(f"Failed to post panel: {type(exc).__name__} {exc}", flush=True)

    async def _setup_buttons(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if interaction.channel_id is None:
                await interaction.followup.send("無法取得頻道資訊。", ephemeral=True)
                return
            await self.repost_panel(channel_id=interaction.channel_id)
            await interaction.followup.send("已重新發送面板。", ephemeral=True)
        except Exception as exc:
            print(f"setup_buttons error: {type(exc).__name__} {exc}", flush=True)
            await interaction.followup.send(
                f"發送失敗：{type(exc).__name__} {exc}",
                ephemeral=True,
            )

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        listen_channel_id = await self.get_panel_channel_id()
        if message.channel.id != listen_channel_id:
            await self.process_commands(message)
            return

        codes = extract_codes_from_text(message.content)
        if codes:
            result = await self.storage.reconcile_codes(
                [
                    RedeemCode(code=code, status=CodeStatus.ACTIVE, note="captured from message")
                    for code in codes
                ],
                source_url=message.jump_url,
                source_type="message",
            )
            if result.new_active_codes:
                await self.announce_new_codes(
                    result.new_active_codes,
                    title="頻道新貼上的兌換碼",
                )

        await self.repost_panel()
        await self.process_commands(message)

    @tasks.loop(minutes=10)
    async def monitor_forum(self) -> None:
        await self.run_monitor_cycle(reason="scheduled")

    @monitor_forum.before_loop
    async def before_monitor_forum(self) -> None:
        await self.wait_until_ready()

    async def run_monitor_cycle(self, *, reason: str) -> None:
        try:
            snapshot = await self.monitor.fetch_snapshot()
            result = await self.storage.reconcile_codes(
                snapshot.codes,
                source_url=snapshot.source_url,
                source_type="monitor",
            )
            if result.new_active_codes:
                await self.announce_new_codes(
                    result.new_active_codes,
                    title=f"巴哈監控發現新有效兌換碼 ({reason})",
                )
        except Exception as exc:
            channel = await self.resolve_channel()
            if channel is not None:
                await channel.send(f"監控執行失敗：`{type(exc).__name__}` {exc}")
            else:
                print(
                    f"Monitor failed and channel not resolved: {type(exc).__name__} {exc}",
                    flush=True,
                )

    async def announce_new_codes(self, codes: list[RedeemCode], *, title: str) -> None:
        channel = await self.resolve_channel()
        if channel is None or not codes:
            return

        lines = [f"- `{item.code}`" for item in codes]
        await channel.send("\n".join([f"**{title}**", *lines]))

    async def build_monthly_report(self) -> str:
        rows = await self.storage.get_monthly_rows(now=datetime.now(timezone.utc))
        if not rows:
            return "本月目前沒有已收錄的兌換碼。"

        lines = ["本月已收錄兌換碼："]
        for row in rows[:50]:
            status_label = "有效" if row.status == CodeStatus.ACTIVE else "過期"
            timestamp = row.first_seen_at.split("T", 1)[0]
            lines.append(f"- `{row.code}` | {status_label} | {row.source_type} | {timestamp}")

        if len(rows) > 50:
            lines.append(f"- 其餘 {len(rows) - 50} 筆請直接查詢資料庫")

        return "\n".join(lines)

    async def repost_panel(self, *, channel_id: int | None = None) -> None:
        async with self.panel_lock:
            target_channel_id = channel_id or await self.get_panel_channel_id()
            channel = await self.resolve_channel(target_channel_id)
            if channel is None:
                print(
                    f"Panel skipped: channel {target_channel_id} not resolved",
                    flush=True,
                )
                return

            current_id = await self.storage.get_state(PANEL_STATE_KEY)
            current_channel_id_raw = await self.storage.get_state(PANEL_CHANNEL_STATE_KEY)
            current_channel_id = int(current_channel_id_raw) if current_channel_id_raw else None
            if current_id:
                with contextlib.suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                    if current_channel_id and current_channel_id != channel.id:
                        old_channel = await self.resolve_channel(current_channel_id)
                        if old_channel is not None:
                            old_message = await old_channel.fetch_message(int(current_id))
                            await old_message.delete()
                    else:
                        old_message = await channel.fetch_message(int(current_id))
                    await old_message.delete()

            try:
                print(
                    f"Posting panel to channel {channel.id}",
                    flush=True,
                )
                panel_message = await channel.send(
                    "\n".join(
                        [
                            "**兌換碼面板**",
                            "- 使用按鈕可人工新增代碼或查詢本月清單",
                            "- 機器人會自動監控巴哈文章並同步新碼",
                            "- 頻道內成員直接貼代碼，機器人也會自動收錄",
                        ]
                    ),
                    view=ControlPanelView(self),
                )
                await self.storage.set_state(PANEL_STATE_KEY, str(panel_message.id))
                await self.storage.set_state(PANEL_CHANNEL_STATE_KEY, str(channel.id))
                print(f"Panel posted: message_id={panel_message.id}", flush=True)
            except Exception as exc:
                print(
                    f"Failed to send panel message: {type(exc).__name__} {exc}",
                    flush=True,
                )

    @tasks.loop(minutes=5)
    async def ensure_panel(self) -> None:
        channel_id = await self.get_panel_channel_id()
        channel = await self.resolve_channel(channel_id)
        if channel is None:
            return

        current_id = await self.storage.get_state(PANEL_STATE_KEY)
        current_channel_id_raw = await self.storage.get_state(PANEL_CHANNEL_STATE_KEY)
        current_channel_id = int(current_channel_id_raw) if current_channel_id_raw else channel.id
        if not current_id:
            await self.repost_panel(channel_id=channel.id)
            return

        try:
            if current_channel_id != channel.id:
                old_channel = await self.resolve_channel(current_channel_id)
                if old_channel is None:
                    await self.repost_panel(channel_id=channel.id)
                    return
                await old_channel.fetch_message(int(current_id))
            else:
                await channel.fetch_message(int(current_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            await self.repost_panel(channel_id=channel.id)

    @ensure_panel.before_loop
    async def before_ensure_panel(self) -> None:
        await self.wait_until_ready()

    async def get_panel_channel_id(self) -> int:
        stored = await self.storage.get_state(PANEL_CHANNEL_STATE_KEY)
        if stored:
            try:
                return int(stored)
            except ValueError:
                pass
        return self.settings.discord_channel_id

    async def resolve_channel(self, channel_id: int) -> discord.TextChannel | discord.Thread | None:
        channel = self.get_channel(channel_id)
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            if not self._resolved_channel_logged:
                self._resolved_channel_logged = True
                print(
                    f"Resolved channel: {channel} ({channel.id})",
                    flush=True,
                )
            return channel

        try:
            fetched = await self.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            print(
                "Failed to fetch channel "
                f"{channel_id}: {type(exc).__name__} {exc}",
                flush=True,
            )
            return None

        if isinstance(fetched, (discord.TextChannel, discord.Thread)):
            if not self._resolved_channel_logged:
                self._resolved_channel_logged = True
                print(
                    f"Resolved channel: {fetched} ({fetched.id})",
                    flush=True,
                )
            return fetched

        print(
            f"Unsupported channel type for {channel_id}: {type(fetched)}",
            flush=True,
        )
        return None
