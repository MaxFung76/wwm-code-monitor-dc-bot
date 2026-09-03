from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
from urllib.parse import urlparse

import discord
import httpx
from discord import app_commands
from discord.ext import commands, tasks

from .arlen import ArlenCodesMonitor
from .bahamut import BahamutMonitor, extract_codes_from_text, normalize_code
from .config import Settings
from .models import CodeSnapshot, CodeStatus, RedeemCode
from .snapshot_io import snapshot_from_json
from .storage import Storage

# Storage 用於記錄面板訊息 id 與其所在頻道（用來確保面板維持置底）
PANEL_STATE_KEY = "panel_message_id"
PANEL_CHANNEL_STATE_KEY = "panel_channel_id"
ARLEN_EXPIRED_STREAK_PREFIX = "arlen_expired_streak:"


def build_snapshot_candidate_urls(snapshot_url: str) -> list[str]:
    # snapshot-cache 的備援鏈：raw.githubusercontent.com -> jsDelivr
    candidates = [snapshot_url]
    parsed = urlparse(snapshot_url)
    if parsed.scheme != "https" or parsed.netloc != "raw.githubusercontent.com":
        return candidates

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 4:
        return candidates

    owner, repo, ref, *rest = parts
    if not rest:
        return candidates

    mirror_url = f"https://cdn.jsdelivr.net/gh/{owner}/{repo}@{ref}/{'/'.join(rest)}"
    if mirror_url not in candidates:
        candidates.append(mirror_url)
    return candidates


def channel_matches_target(*, channel_id: int, parent_id: int | None, target_id: int) -> bool:
    # Thread 內回覆時，message.channel.id 會是 thread id；parent_id 才是原頻道 id
    return channel_id == target_id or parent_id == target_id


def merge_snapshots(snapshots: list[CodeSnapshot]) -> CodeSnapshot:
    # 多來源合併規則：
    # - 以第一次見到的順序保留輸出順序（方便人類閱讀）
    # - 同碼若出現 active/expired 衝突，expired 覆蓋 active（避免誤公告）
    if not snapshots:
        raise ValueError("Cannot merge an empty snapshot list.")
    if len(snapshots) == 1:
        return snapshots[0]

    collected: dict[str, RedeemCode] = {}
    order: list[str] = []
    for snapshot in snapshots:
        for item in snapshot.codes:
            code = normalize_code(item.code)
            existing = collected.get(code)
            candidate = RedeemCode(code=code, status=item.status, note=item.note)
            if existing is None:
                collected[code] = candidate
                order.append(code)
                continue
            if existing.status == CodeStatus.ACTIVE and candidate.status == CodeStatus.EXPIRED:
                collected[code] = candidate

    source_urls = list(dict.fromkeys(snapshot.source_url for snapshot in snapshots))
    return CodeSnapshot(
        source_url=" | ".join(source_urls),
        observed_at=max(snapshot.observed_at for snapshot in snapshots),
        codes=[collected[code] for code in order],
    )


async def send_interaction_message(
    interaction: discord.Interaction,
    message: str,
    *,
    ephemeral: bool = True,
) -> None:
    # slash command / modal 可能已 defer，這裡統一處理回覆方式
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=ephemeral)
    else:
        await interaction.response.send_message(message, ephemeral=ephemeral)


class RedeemCommandTree(app_commands.CommandTree["RedeemCodeBot"]):
    async def on_error(
        self,
        interaction: discord.Interaction["RedeemCodeBot"],
        error: app_commands.AppCommandError,
        /,
    ) -> None:
        # 避免錯誤吞掉，至少回報到 console + ephemeral 提示
        actual_error = getattr(error, "original", error)
        print(
            "App command error: "
            f"{type(actual_error).__name__} {actual_error}",
            flush=True,
        )
        with contextlib.suppress(discord.HTTPException):
            await send_interaction_message(
                interaction,
                f"指令執行失敗：{type(actual_error).__name__} {actual_error}",
                ephemeral=True,
            )


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
        # 手動補登：支援一次貼多筆，自動拆解與去重
        codes = [
            RedeemCode(code=value, status=CodeStatus.ACTIVE, note="added from modal")
            for value in extract_codes_from_text(self.codes_input.value)
        ]

        if not codes:
            await interaction.response.send_message("沒有辨識到任何兌換碼。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        # 寫入 Storage：reconcile 會保證 code 正規化與只公告新 active
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
        await send_interaction_message(interaction, "操作失敗，請稍後再試。", ephemeral=True)


class ControlPanelView(discord.ui.View):
    def __init__(self, bot: "RedeemCodeBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="新增兌換碼",
        style=discord.ButtonStyle.success,
        custom_id="panel:add-code",
    )
    async def add_code_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(AddCodeModal(self.bot))

    @discord.ui.button(
        label="新兌換碼",
        style=discord.ButtonStyle.primary,
        custom_id="panel:monthly-list",
    )
    async def monthly_list_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        # 月報是 ephemeral：顯示「你尚未看過」的新碼，並在顯示後標記已讀
        await interaction.response.defer(ephemeral=True, thinking=True)
        report = await self.bot.build_monthly_report(interaction.user.id)
        await interaction.followup.send(report, ephemeral=True)
        await self.bot.repost_panel()

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        _: discord.ui.Item[discord.ui.View],
    ) -> None:
        print(f"View error: {type(error).__name__} {error}", flush=True)
        await send_interaction_message(interaction, "操作失敗，請稍後再試。", ephemeral=True)


class RedeemCodeBot(commands.Bot):
    def __init__(self, settings: Settings, storage: Storage) -> None:
        # 需要 message_content 以便在指定頻道內自動擷取使用者貼上的兌換碼
        intents = discord.Intents.default()
        intents.guilds = True
        intents.messages = True
        intents.message_content = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            tree_cls=RedeemCommandTree,
        )
        self.settings = settings
        self.storage = storage
        # 主要來源：巴哈（支援 httpx -> playwright fallback）
        self.monitor = BahamutMonitor(
            forum_url=settings.forum_url,
            timeout_seconds=settings.request_timeout_seconds,
        )
        # 額外來源：阿冷整理頁（可透過 ARLEN_CODES_URL 關閉）
        self.arlen_monitor = (
            ArlenCodesMonitor(
                source_url=settings.arlen_codes_url,
                timeout_seconds=settings.request_timeout_seconds,
            )
            if settings.arlen_codes_url
            else None
        )
        self.panel_lock = asyncio.Lock()
        self._initial_sync_done = False
        self._resolved_channel_logged = False

    def build_panel_embed(self) -> discord.Embed:
        # 面板以 Embed 呈現，避免純文字冗長，並搭配 persistent View 提供按鈕
        embed = discord.Embed(
            title="兌換碼面板",
            description=(
                "這裡會整理最新兌換碼。\n"
                "你可以補登代碼、查看新碼，也可以直接在這裡貼上兌換碼。"
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="快速操作",
            value=(
                "`新增兌換碼`：手動補登\n"
                "`新兌換碼`：查看你還沒看過的有效代碼"
            ),
            inline=False,
        )
        embed.add_field(
            name="自動處理",
            value=(
                "機器人會定期同步監控來源。\n"
                "在這個頻道或討論串貼上兌換碼，也會自動幫你收錄。"
            ),
            inline=False,
        )
        embed.set_footer(text="有新公告或互動時，面板會自動回到底部。")
        return embed

    async def setup_hook(self) -> None:
        # bot 啟動時：初始化資料庫、註冊 persistent view、啟動排程、同步 slash commands
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
        self.tree.add_command(
            app_commands.Command(
                name="sync_now",
                description="立刻同步監控來源並更新兌換碼狀態（可選填 code）",
                callback=self._sync_now,
            )
        )
        if self.settings.discord_guild_id:
            guild = discord.Object(id=self.settings.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(
                "Guild commands synced: "
                + ", ".join(command.name for command in synced),
                flush=True,
            )
        else:
            synced = await self.tree.sync()
            print(
                "Global commands synced: "
                + ", ".join(command.name for command in synced),
                flush=True,
            )

    async def on_ready(self) -> None:
        # 首次 ready 後做一次同步與面板補底
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
        # /setup_buttons：把「目前頻道」設成新的面板頻道，並立刻重發置底面板
        print(
            f"/setup_buttons invoked by user={interaction.user.id} channel={interaction.channel_id}",
            flush=True,
        )
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

    async def _sync_now(self, interaction: discord.Interaction, code: str | None = None) -> None:
        # /sync_now：手動觸發一次抓取 + reconcile；可選填 code 做即時查核
        print(
            f"/sync_now invoked by user={interaction.user.id} channel={interaction.channel_id} code={code!r}",
            flush=True,
        )
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            primary_snapshot, arlen_snapshot, mode, complete = await self.fetch_monitor_snapshot()
            result, merged_snapshot = await self.apply_monitor_snapshots(
                primary_snapshot=primary_snapshot,
                arlen_snapshot=arlen_snapshot,
                complete=complete,
            )

            active_count = sum(1 for item in merged_snapshot.codes if item.status == CodeStatus.ACTIVE)
            expired_count = sum(1 for item in merged_snapshot.codes if item.status == CodeStatus.EXPIRED)

            lines = [
                "已同步監控來源。",
                f"- mode: {mode}",
                f"- sources: {'complete' if complete else 'partial'}",
                f"- active: {active_count}",
                f"- expired: {expired_count}",
                f"- new_active: {len(result.new_active_codes)}",
                f"- changed: {len(result.changed_codes)}",
            ]

            if code:
                normalized_code = normalize_code(code)
                target = next(
                    (item for item in merged_snapshot.codes if item.code == normalized_code),
                    None,
                )
                if target is None:
                    lines.append(f"- snapshot[{normalized_code}]: not found")
                else:
                    lines.append(f"- snapshot[{normalized_code}]: {target.status.value}")

                db_row = await self.storage.get_code_status(normalized_code)
                if db_row is None:
                    lines.append(f"- db[{normalized_code}]: not found")
                else:
                    lines.append(f"- db[{normalized_code}]: {db_row[0]} ({db_row[1]})")

            await interaction.followup.send("\n".join(lines), ephemeral=True)
        except Exception as exc:
            print(f"sync_now error: {type(exc).__name__} {exc}", flush=True)
            await interaction.followup.send(
                f"同步失敗：{type(exc).__name__} {exc}",
                ephemeral=True,
            )

    async def on_message(self, message: discord.Message) -> None:
        # 在面板頻道（或該頻道的討論串）內，自動擷取使用者貼上的兌換碼並收錄
        if message.author.bot:
            return
        listen_channel_id = await self.get_panel_channel_id()
        parent_id = message.channel.parent_id if isinstance(message.channel, discord.Thread) else None
        if not channel_matches_target(
            channel_id=message.channel.id,
            parent_id=parent_id,
            target_id=listen_channel_id,
        ):
            await self.process_commands(message)
            return

        codes = extract_codes_from_text(message.content)
        if codes:
            # 來源記成 message：可追溯 jump_url，並與 monitor/manual 區分
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
        # 排程/啟動共用：抓取監控來源 -> reconcile -> 有新碼才公告
        try:
            primary_snapshot, mode = await self.fetch_primary_monitor_snapshot()
            complete = True
            arlen_snapshot = None
            if getattr(self, "arlen_monitor", None) is not None:
                try:
                    arlen_snapshot = await self.arlen_monitor.fetch_snapshot()
                    mode = mode + "+arlen_codes"
                except Exception as exc:
                    print(
                        "Arlen source fetch failed, continuing with Bahamut only: "
                        f"{type(exc).__name__} {exc}",
                        flush=True,
                    )
                    complete = False

            result, _ = await self.apply_monitor_snapshots(
                primary_snapshot=primary_snapshot,
                arlen_snapshot=arlen_snapshot,
                complete=complete,
            )
            announced = result.first_seen_active_codes
            if announced:
                await self.announce_new_codes(announced, title="監控來源發現新兌換碼")
        except Exception as exc:
            channel = await self.resolve_channel(await self.get_panel_channel_id())
            if channel is not None:
                await channel.send(f"監控執行失敗：`{type(exc).__name__}` {exc}")
            else:
                print(
                    f"Monitor failed and channel not resolved: {type(exc).__name__} {exc}",
                    flush=True,
                )

    async def announce_new_codes(
        self,
        codes: list[RedeemCode],
        *,
        title: str,
    ) -> None:
        # 公告以 Embed 呈現，並在公告後重發面板，確保面板仍位於頻道最底
        channel = await self.resolve_channel(await self.get_panel_channel_id())
        if channel is None or not codes:
            return

        code_lines = "\n".join(f"`{item.code}`" for item in codes)
        embed = discord.Embed(
            title=title,
            description=code_lines,
            color=discord.Color.green(),
        )
        await channel.send(embed=embed)
        await self.repost_panel(channel_id=channel.id)

    async def build_monthly_report(self, user_id: int) -> str:
        # 月報：只顯示該 user 尚未看過、且仍為 active 的碼；顯示後會標記已讀
        rows = await self.storage.get_unseen_monthly_rows(
            user_id,
            now=datetime.now(timezone.utc),
        )
        if not rows:
            return "目前沒有你尚未查看的新兌換碼。"

        lines = ["你尚未查看的新兌換碼："]
        displayed_codes: list[str] = []
        hidden_count = 0
        limit = 1900
        for index, row in enumerate(rows):
            line = f"- `{row.code}`"
            candidate = "\n".join([*lines, line])
            if len(candidate) > limit:
                hidden_count = len(rows) - index
                break
            lines.append(line)
            displayed_codes.append(row.code)

        if hidden_count:
            lines.append(f"- 其餘 {hidden_count} 筆未顯示")

        if displayed_codes:
            await self.storage.mark_codes_seen(user_id, displayed_codes)

        return "\n".join(lines)

    async def repost_panel(self, *, channel_id: int | None = None) -> None:
        # 置底策略：刪舊面板、重發新面板（避免訊息被頂上去）
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
                # View 每次重發都重新建立，確保按鈕可用且 custom_id 維持一致
                panel_message = await channel.send(
                    embed=self.build_panel_embed(),
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
        # 補救機制：定期檢查面板是否存在且位於頻道最底，否則重發
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
            return

        latest_message = None
        async for message in channel.history(limit=1):
            latest_message = message
            break

        # 若面板不是最後一則訊息，就重發一次把面板推回底部
        if latest_message is None or latest_message.id != int(current_id):
            await self.repost_panel(channel_id=channel.id)

    @ensure_panel.before_loop
    async def before_ensure_panel(self) -> None:
        await self.wait_until_ready()

    async def get_panel_channel_id(self) -> int:
        # 若曾用 /setup_buttons 設定過，就以資料庫記錄的頻道為準
        stored = await self.storage.get_state(PANEL_CHANNEL_STATE_KEY)
        if stored:
            try:
                return int(stored)
            except ValueError:
                pass
        return self.settings.discord_channel_id

    async def resolve_channel(self, channel_id: int) -> discord.TextChannel | discord.Thread | None:
        # 優先用快取；拿不到才 fetch（降低 API 成本）
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

    async def fetch_monitor_snapshot(self) -> tuple[CodeSnapshot, CodeSnapshot | None, str, bool]:
        primary_snapshot, mode = await self.fetch_primary_monitor_snapshot()
        complete = True
        arlen_snapshot = None
        if getattr(self, "arlen_monitor", None) is not None:
            try:
                arlen_snapshot = await self.arlen_monitor.fetch_snapshot()
                mode = mode + "+arlen_codes"
            except Exception as exc:
                print(
                    "Arlen source fetch failed, continuing with Bahamut only: "
                    f"{type(exc).__name__} {exc}",
                    flush=True,
                )
                complete = False
        return primary_snapshot, arlen_snapshot, mode, complete

    async def fetch_primary_monitor_snapshot(self) -> tuple[CodeSnapshot, str]:
        # primary：有 snapshot 就用 snapshot，否則走 live bahamut
        if self.settings.remote_snapshot_url:
            try:
                snapshot = await self.fetch_remote_snapshot(self.settings.remote_snapshot_url)
                return snapshot, "github_snapshot"
            except Exception as exc:
                print(
                    "Remote snapshot fetch failed, falling back to live Bahamut: "
                    f"{type(exc).__name__} {exc}",
                    flush=True,
                )
        snapshot = await self.monitor.fetch_snapshot()
        return snapshot, "live_bahamut"

    def _arlen_streak_key(self, code: str) -> str:
        return ARLEN_EXPIRED_STREAK_PREFIX + normalize_code(code)

    async def apply_monitor_snapshots(
        self,
        *,
        primary_snapshot: CodeSnapshot,
        arlen_snapshot: CodeSnapshot | None,
        complete: bool,
    ):
        primary_result = await self.storage.reconcile_codes(
            primary_snapshot.codes,
            source_url=primary_snapshot.source_url,
            source_type="monitor",
        )

        merged_snapshots = [primary_snapshot]
        first_seen = list(primary_result.first_seen_active_codes)
        changed = list(primary_result.changed_codes)

        if arlen_snapshot is not None:
            merged_snapshots.append(arlen_snapshot)
            await self.storage.record_observations(
                arlen_snapshot.codes,
                source_url=arlen_snapshot.source_url,
                source_type="monitor",
            )
            arlen_active = [item for item in arlen_snapshot.codes if item.status == CodeStatus.ACTIVE]
            arlen_result = await self.storage.reconcile_codes(
                arlen_active,
                source_url=arlen_snapshot.source_url,
                source_type="monitor",
                record_observations=False,
                update_redeem_codes=True,
            )
            first_seen.extend(arlen_result.first_seen_active_codes)
            changed.extend(arlen_result.changed_codes)

            if complete:
                await self.apply_arlen_expired_confirmations(arlen_snapshot)

        merged = merge_snapshots(merged_snapshots)
        return (
            type(primary_result)(
                new_active_codes=first_seen,
                first_seen_active_codes=first_seen,
                changed_codes=changed,
            ),
            merged,
        )

    async def apply_arlen_expired_confirmations(self, arlen_snapshot: CodeSnapshot) -> None:
        threshold = int(self.settings.arlen_expired_confirmations)
        if threshold <= 0:
            return

        arlen_status: dict[str, CodeStatus] = {
            normalize_code(item.code): item.status for item in arlen_snapshot.codes
        }
        status_map = await self.storage.get_status_map(list(arlen_status.keys()))

        to_expire: list[RedeemCode] = []
        for code, db_status in status_map.items():
            if db_status != CodeStatus.ACTIVE:
                continue
            observed = arlen_status.get(code)
            if observed == CodeStatus.EXPIRED:
                key = self._arlen_streak_key(code)
                raw = await self.storage.get_state(key)
                streak = int(raw) if raw and raw.isdigit() else 0
                streak += 1
                await self.storage.set_state(key, str(streak))
                if streak >= threshold:
                    to_expire.append(RedeemCode(code=code, status=CodeStatus.EXPIRED, note="arlen confirmed"))
            elif observed == CodeStatus.ACTIVE:
                await self.storage.set_state(self._arlen_streak_key(code), "0")

        if to_expire:
            await self.storage.reconcile_codes(
                to_expire,
                source_url=arlen_snapshot.source_url,
                source_type="monitor",
                record_observations=False,
                update_redeem_codes=True,
            )

    async def fetch_remote_snapshot(self, snapshot_url: str) -> CodeSnapshot:
        # 遠端 snapshot（通常是 GitHub Actions 產生的 snapshot-cache）
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
        }
        last_error: Exception | None = None
        async with httpx.AsyncClient(
            timeout=self.settings.request_timeout_seconds,
            headers=headers,
        ) as client:
            for candidate_url in build_snapshot_candidate_urls(snapshot_url):
                try:
                    # 逐一嘗試候選 URL（raw -> jsDelivr），成功即回傳
                    response = await client.get(candidate_url, follow_redirects=True)
                    response.raise_for_status()
                    return snapshot_from_json(response.text)
                except Exception as exc:
                    last_error = exc
                    print(
                        "Snapshot fetch attempt failed: "
                        f"url={candidate_url} {type(exc).__name__} {exc}",
                        flush=True,
                    )
        if last_error is not None:
            raise last_error
        raise RuntimeError("Snapshot fetch failed without a captured exception.")
