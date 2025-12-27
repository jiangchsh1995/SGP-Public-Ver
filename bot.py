import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import sqlite3
import os
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Tuple
from concurrent.futures import ThreadPoolExecutor
import traceback

# SGP 核心模块导入
from src.watermark_service import (
    load_config,
    create_master_copy,
    generate_distribution,
    check_watermark
)

# ==================== 配置常量 ====================

# 加载全局配置
_GLOBAL_CONFIG = load_config()

# 从配置文件读取参数（支持运行时配置）
EMBED_COLOR = int(_GLOBAL_CONFIG.get('embed_color', '0x00A8FC'), 16)
DB_PATH = _GLOBAL_CONFIG.get('db_path', 'data.db')
TEMP_DIR = Path(_GLOBAL_CONFIG.get('temp_dir', 'temp_uploads'))
TEMP_DIR.mkdir(exist_ok=True)

# UI 配置
VIEW_TIMEOUT = int(_GLOBAL_CONFIG.get('view_timeout', '180'))
MAX_FILE_SIZE_MB = int(_GLOBAL_CONFIG.get('max_file_size', '25'))
MAX_WORKERS = int(_GLOBAL_CONFIG.get('workers', '4'))

# ==================== 数据库初始化 ====================

def init_database():
    """初始化 SQLite 数据库 - 支持 Discord 复杂层级结构"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            parent_id INTEGER,
            uploader_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            stored_filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            allow_repost BOOLEAN NOT NULL,
            allow_modify BOOLEAN NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 创建索引加速查询
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_guild_channel 
        ON cards(guild_id, channel_id)
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_uploader 
        ON cards(uploader_id)
    """)
    
    conn.commit()
    conn.close()
    print("[DB] ✅ 数据库初始化完成")


# ==================== 数据库操作辅助函数 ====================

def add_card(guild_id: int, channel_id: int, parent_id: Optional[int],
             uploader_id: int, filename: str, stored_filename: str, 
             file_path: str, allow_repost: bool, allow_modify: bool) -> int:
    """添加卡片记录"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO cards (
            guild_id, channel_id, parent_id, uploader_id, 
            filename, stored_filename, file_path, 
            allow_repost, allow_modify
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (guild_id, channel_id, parent_id, uploader_id, 
          filename, stored_filename, file_path, 
          allow_repost, allow_modify))
    
    card_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return card_id


def get_cards_by_channel(guild_id: int, channel_id: int) -> List[Tuple]:
    """获取指定频道/Thread 的所有卡片"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, filename, stored_filename, file_path, uploader_id, 
               allow_repost, allow_modify, created_at
        FROM cards
        WHERE guild_id = ? AND channel_id = ?
        ORDER BY created_at DESC
    """, (guild_id, channel_id))
    
    cards = cursor.fetchall()
    conn.close()
    
    return cards


def get_user_cards_in_channel(guild_id: int, channel_id: int, user_id: int) -> List[Tuple]:
    """获取用户在指定频道上传的卡片"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, filename, stored_filename, file_path, 
               allow_repost, allow_modify, created_at
        FROM cards
        WHERE guild_id = ? AND channel_id = ? AND uploader_id = ?
        ORDER BY created_at DESC
    """, (guild_id, channel_id, user_id))
    
    cards = cursor.fetchall()
    conn.close()
    
    return cards


def delete_card(card_id: int, user_id: int) -> Tuple[bool, Optional[str]]:
    """删除卡片（仅允许所有者删除）"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 先验证所有权并获取文件路径
    cursor.execute("""
        SELECT file_path FROM cards 
        WHERE id = ? AND uploader_id = ?
    """, (card_id, user_id))
    
    result = cursor.fetchone()
    
    if not result:
        conn.close()
        return False, None
    
    file_path = result[0]
    
    # 删除数据库记录
    cursor.execute("DELETE FROM cards WHERE id = ?", (card_id,))
    conn.commit()
    conn.close()
    
    return True, file_path


def update_card_permissions(card_id: int, user_id: int, 
                           allow_repost: bool, allow_modify: bool) -> bool:
    """更新卡片权限"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE cards 
        SET allow_repost = ?, allow_modify = ?
        WHERE id = ? AND uploader_id = ?
    """, (allow_repost, allow_modify, card_id, user_id))
    
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    
    return affected > 0


# ==================== Discord UI 组件 ====================

class CardSelectMenu(discord.ui.Select):
    """卡片选择下拉菜单"""
    
    def __init__(self, cards: List[Tuple], action: str, master_dir: Path):
        self.cards_data = cards
        self.action = action
        self.master_dir = master_dir  # 母带存储根目录，用于还原绝对路径
        
        options = [
            discord.SelectOption(
                label=card[1][:100],  # filename
                description=f"上传于 {card[7][:16] if len(card) > 7 else '未知时间'}",
                value=str(card[0])  # card_id
            )
            for card in cards[:25]  # Discord 限制最多 25 个选项
        ]
        
        super().__init__(
            placeholder="请选择一张角色卡...",
            options=options,
            custom_id=f"{action}_select"
        )
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        card_id = int(self.values[0])
        card_data = next((c for c in self.cards_data if c[0] == card_id), None)
        
        if not card_data:
            await interaction.followup.send("❌ 卡片不存在", ephemeral=True)
            return
        
        if self.action == "download":
            await self.handle_download(interaction, card_data)
        elif self.action == "manage":
            await self.handle_manage(interaction, card_data)
    
    async def handle_download(self, interaction: discord.Interaction, card_data: Tuple):
        """处理下载请求"""
        try:
            card_id, filename, stored_filename, file_path, uploader_id, allow_repost, allow_modify = card_data[:7]
            
            # 从数据库读取的是相对路径，需要还原为绝对路径
            real_file_path = self.master_dir / file_path
            
            # 检查文件是否存在
            if not real_file_path.exists():
                await interaction.followup.send(
                    "❌ 文件已丢失：母带文件不存在",
                    ephemeral=True
                )
                return
            
            # 并发生成分发版本
            loop = asyncio.get_event_loop()
            config = await loop.run_in_executor(None, load_config)
            
            # 使用还原后的绝对路径作为母带文件名
            dist_path = await loop.run_in_executor(
                None,
                generate_distribution,
                str(real_file_path),
                interaction.user.id,
                config
            )
            
            # 发送文件
            file = discord.File(dist_path, filename=filename)
            
            embed = discord.Embed(
                title="📥 角色卡已生成",
                description=(
                    f"**文件名:** {filename}\n"
                    f"**上传者:** <@{uploader_id}>\n\n"
                    f"⚠️ **重要提示:**\n"
                    f"• 此文件已嵌入您的专属追溯标识 (UID: `{interaction.user.id}`)\n"
                    f"• 仅供个人使用，请勿随意传播\n"
                    f"• 若发现泄露，系统可追溯到您的账号"
                ),
                color=EMBED_COLOR
            )
            
            embed.set_footer(text="ShadowGuard Protocol - 角色卡追溯系统")
            
            await interaction.followup.send(embed=embed, file=file, ephemeral=True)
            
            # 清理临时文件
            try:
                if os.path.exists(dist_path):
                    os.remove(dist_path)
            except Exception as e:
                print(f"[警告] 清理临时文件失败: {e}")
                
        except Exception as e:
            print(f"[错误] 下载处理失败: {traceback.format_exc()}")
            await interaction.followup.send(
                "❌ 生成失败：文件处理出现错误",
                ephemeral=True
            )
    
    async def handle_manage(self, interaction: discord.Interaction, card_data: Tuple):
        """处理管理请求"""
        try:
            card_id, filename, stored_filename, file_path, allow_repost, allow_modify = card_data[:6]
            
            # 传递master_dir用于删除操作
            view = CardManageView(card_id, filename, file_path, allow_repost, allow_modify, self.master_dir)
            
            embed = discord.Embed(
                title="⚙️ 管理角色卡",
                description=(
                    f"**文件名:** {filename}\n"
                    f"**当前权限:**\n"
                    f"• 允许转载: {'✅ 是' if allow_repost else '❌ 否'}\n"
                    f"• 允许二改: {'✅ 是' if allow_modify else '❌ 否'}\n\n"
                    f"请选择操作："
                ),
                color=EMBED_COLOR
            )
            
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            
        except Exception as e:
            print(f"[错误] 管理处理失败: {traceback.format_exc()}")
            await interaction.followup.send(
                "❌ 操作失败：无法加载管理界面",
                ephemeral=True
            )


class CardManageView(discord.ui.View):
    """卡片管理视图"""
    
    def __init__(self, card_id: int, filename: str, file_path: str, 
                 allow_repost: bool, allow_modify: bool, master_dir: Path):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.card_id = card_id
        self.filename = filename
        self.file_path = file_path  # 相对路径
        self.allow_repost = allow_repost
        self.allow_modify = allow_modify
        self.master_dir = master_dir  # 母带存储根目录，用于还原绝对路径
    
    @discord.ui.button(label="删除", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        success, relative_path = delete_card(self.card_id, interaction.user.id)
        
        if success:
            # 删除物理文件 - 从相对路径还原绝对路径
            try:
                if relative_path:
                    target_file = self.master_dir / relative_path
                    if target_file.exists():
                        target_file.unlink()
                        print(f"[DB] ✅ 已删除文件: {target_file}")
                    else:
                        print(f"[警告] 文件不存在: {target_file}")
            except Exception as e:
                print(f"[警告] 删除物理文件失败: {e}")
            
            embed = discord.Embed(
                title="✅ 删除成功",
                description=f"已删除角色卡: **{self.filename}**",
                color=EMBED_COLOR
            )
        else:
            embed = discord.Embed(
                title="❌ 删除失败",
                description="您没有权限删除此卡片，或卡片不存在",
                color=0xFF0000
            )
        
        await interaction.response.edit_message(embed=embed, view=None)
    
    @discord.ui.button(label="修改权限", style=discord.ButtonStyle.secondary, emoji="🔧")
    async def edit_permissions_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = PermissionModal(self.card_id, self.filename, self.allow_repost, self.allow_modify)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="已取消",
            description="管理操作已取消",
            color=EMBED_COLOR
        )
        await interaction.response.edit_message(embed=embed, view=None)


class PermissionModal(discord.ui.Modal, title="修改权限"):
    """权限修改模态框"""
    
    def __init__(self, card_id: int, filename: str, current_repost: bool, current_modify: bool):
        super().__init__()
        self.card_id = card_id
        self.filename = filename
        
        self.repost_input = discord.ui.TextInput(
            label="允许转载 (true/false)",
            placeholder="true 或 false",
            default=str(current_repost).lower(),
            max_length=5
        )
        
        self.modify_input = discord.ui.TextInput(
            label="允许二改 (true/false)",
            placeholder="true 或 false",
            default=str(current_modify).lower(),
            max_length=5
        )
        
        self.add_item(self.repost_input)
        self.add_item(self.modify_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            allow_repost = self.repost_input.value.lower() in ('true', '1', 'yes', 'y')
            allow_modify = self.modify_input.value.lower() in ('true', '1', 'yes', 'y')
            
            success = update_card_permissions(
                self.card_id,
                interaction.user.id,
                allow_repost,
                allow_modify
            )
            
            if success:
                embed = discord.Embed(
                    title="✅ 权限已更新",
                    description=(
                        f"**文件名:** {self.filename}\n"
                        f"**新权限:**\n"
                        f"• 允许转载: {'✅ 是' if allow_repost else '❌ 否'}\n"
                        f"• 允许二改: {'✅ 是' if allow_modify else '❌ 否'}"
                    ),
                    color=EMBED_COLOR
                )
            else:
                embed = discord.Embed(
                    title="❌ 更新失败",
                    description="您没有权限修改此卡片",
                    color=0xFF0000
                )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except Exception as e:
            print(f"[错误] 权限更新失败: {traceback.format_exc()}")
            await interaction.response.send_message(
                "❌ 更新失败：权限设置出现错误",
                ephemeral=True
            )


# ==================== Bot Commands Cog ====================

class SGPCog(commands.Cog):
    """SGP 水印系统命令集"""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self.config = load_config()
        
        # 全局配置：从配置文件读取母带存储根目录
        self.master_dir = Path(self.config.get('master_dir', 'storage/masters'))
        self.master_dir.mkdir(parents=True, exist_ok=True)
    
    @app_commands.command(name="上传角色卡", description="上传角色卡并生成母带水印")
    @app_commands.describe(
        attachment="PNG 格式的角色卡图片",
        allow_repost="是否允许他人转载",
        allow_modify="是否允许他人二次创作"
    )
    async def upload_card(
        self,
        interaction: discord.Interaction,
        attachment: discord.Attachment,
        allow_repost: bool = False,
        allow_modify: bool = False
    ):
        await interaction.response.defer(ephemeral=True)
        
        try:
            # 验证服务器环境
            if not interaction.guild:
                await interaction.followup.send(
                    "❌ 此命令仅能在服务器中使用",
                    ephemeral=True
                )
                return
            
            # 验证文件类型
            if not attachment.filename.lower().endswith('.png'):
                await interaction.followup.send(
                    "❌ 仅支持 PNG 格式的图片",
                    ephemeral=True
                )
                return
            
            # 验证文件大小（从配置读取）
            max_size = MAX_FILE_SIZE_MB * 1024 * 1024
            if attachment.size > max_size:
                await interaction.followup.send(
                    f"❌ 文件过大，请上传小于 {MAX_FILE_SIZE_MB}MB 的图片",
                    ephemeral=True
                )
                return
            
            # 获取上下文信息
            guild_id = interaction.guild_id
            channel_id = interaction.channel_id
            parent_id = None
            
            # 检测是否在 Thread 中
            if isinstance(interaction.channel, discord.Thread):
                parent_id = interaction.channel.parent_id
            
            # 构建分层存储路径
            master_dir = Path(self.config.get('master_dir', 'storage/masters'))
            storage_path = master_dir / str(guild_id) / str(channel_id)
            storage_path.mkdir(parents=True, exist_ok=True)
            
            # 生成唯一文件名
            stored_filename = f"{uuid.uuid4().hex}_{attachment.filename}"
            full_path = storage_path / stored_filename
            
            # 保存临时文件
            temp_file = TEMP_DIR / f"{interaction.user.id}_{attachment.filename}"
            await attachment.save(temp_file)
            
            # 并发调用 SGP Core 制作母带
            loop = asyncio.get_event_loop()
            
            # 更新配置
            config = self.config.copy()
            config['owner_uuid'] = interaction.user.id
            config['allow_reprint'] = allow_repost
            config['allow_derivative'] = allow_modify
            
            master_path = await loop.run_in_executor(
                self.executor,
                create_master_copy,
                str(temp_file),
                config
            )
            
            # 移动母带到分层存储位置
            import shutil
            shutil.move(master_path, str(full_path))
            
            # 计算相对路径（存储到数据库）
            relative_path = f"{guild_id}/{channel_id}/{stored_filename}"
            
            # 写入数据库 - 存储相对路径，而非绝对路径
            card_id = add_card(
                guild_id=guild_id,
                channel_id=channel_id,
                parent_id=parent_id,
                uploader_id=interaction.user.id,
                filename=attachment.filename,
                stored_filename=stored_filename,
                file_path=relative_path,  # ← 存储相对路径
                allow_repost=allow_repost,
                allow_modify=allow_modify
            )
            
            # 清理临时文件
            temp_file.unlink(missing_ok=True)
            
            # 返回成功消息
            embed = discord.Embed(
                title="✅ 上传成功",
                description=(
                    f"**文件名:** {attachment.filename}\n"
                    f"**卡片 ID:** {card_id}\n"
                    f"**权限配置:**\n"
                    f"• 允许转载: {'✅ 是' if allow_repost else '❌ 否'}\n"
                    f"• 允许二改: {'✅ 是' if allow_modify else '❌ 否'}\n\n"
                    f"母带已生成并保存到安全存储区。\n"
                    f"其他用户可通过 `/下载角色卡` 获取带水印的副本。"
                ),
                color=EMBED_COLOR
            )
            
            embed.set_footer(text="ShadowGuard Protocol - 角色卡追溯系统")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            print(f"[上传] ✅ 用户 {interaction.user.id} 上传: {attachment.filename} (ID: {card_id})")
            
        except PermissionError as e:
            await interaction.followup.send(
                f"❌ 权限不足：此图片已被原作者禁止二次创作",
                ephemeral=True
            )
        except Exception as e:
            print(f"[错误] 上传失败: {traceback.format_exc()}")
            await interaction.followup.send(
                "❌ 上传失败：文件处理出现错误，请检查图片格式是否正确",
                ephemeral=True
            )
            # 确保清理临时文件
            try:
                temp_file = TEMP_DIR / f"{interaction.user.id}_{attachment.filename}"
                temp_file.unlink(missing_ok=True)
            except:
                pass
    
    @app_commands.command(name="下载角色卡", description="下载当前帖子的角色卡（自动添加水印）")
    async def download_card(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        try:
            # 验证服务器环境
            if not interaction.guild:
                await interaction.followup.send(
                    "❌ 此命令仅能在服务器中使用",
                    ephemeral=True
                )
                return
            
            # 查询当前频道/Thread 的卡片
            cards = get_cards_by_channel(interaction.guild_id, interaction.channel_id)
            
            if not cards:
                embed = discord.Embed(
                    title="📂 暂无资源",
                    description=(
                        "当前帖子还没有上传任何角色卡。\n\n"
                        "使用 `/上传角色卡` 命令来上传第一张卡片！"
                    ),
                    color=EMBED_COLOR
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
            
            # 创建选择菜单（传递master_dir用于路径还原）
            view = discord.ui.View(timeout=VIEW_TIMEOUT)
            select_menu = CardSelectMenu(cards, action="download", master_dir=self.master_dir)
            view.add_item(select_menu)
            
            embed = discord.Embed(
                title="📥 选择角色卡",
                description=f"当前帖子共有 **{len(cards)}** 张角色卡可供下载。\n请从下拉菜单中选择：",
                color=EMBED_COLOR
            )
            
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            
        except Exception as e:
            print(f"[错误] 下载失败: {traceback.format_exc()}")
            await interaction.followup.send(
                "❌ 查询失败：无法获取卡片列表",
                ephemeral=True
            )
    
    @app_commands.command(name="管理角色卡", description="管理您上传的角色卡")
    async def manage_card(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        try:
            # 验证服务器环境
            if not interaction.guild:
                await interaction.followup.send(
                    "❌ 此命令仅能在服务器中使用",
                    ephemeral=True
                )
                return
            
            # 查询用户在当前频道的卡片
            cards = get_user_cards_in_channel(
                interaction.guild_id, 
                interaction.channel_id, 
                interaction.user.id
            )
            
            if not cards:
                embed = discord.Embed(
                    title="📂 暂无资源",
                    description="您在当前帖子没有上传过角色卡。",
                    color=EMBED_COLOR
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
            
            # 创建选择菜单（传递master_dir用于路径还原）
            view = discord.ui.View(timeout=VIEW_TIMEOUT)
            select_menu = CardSelectMenu(cards, action="manage", master_dir=self.master_dir)
            view.add_item(select_menu)
            
            embed = discord.Embed(
                title="⚙️ 管理角色卡",
                description=f"您在当前帖子共有 **{len(cards)}** 张角色卡。\n请选择要管理的卡片：",
                color=EMBED_COLOR
            )
            
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            
        except Exception as e:
            print(f"[错误] 管理失败: {traceback.format_exc()}")
            await interaction.followup.send(
                "❌ 查询失败：无法获取您的卡片列表",
                ephemeral=True
            )
    
    @app_commands.command(name="审查角色卡", description="检查图片的水印信息（溯源）")
    @app_commands.describe(attachment="要审查的图片")
    async def audit_card(self, interaction: discord.Interaction, attachment: discord.Attachment):
        await interaction.response.defer(ephemeral=True)
        
        temp_file = None
        
        try:
            # 验证文件大小（从配置读取）
            max_size = MAX_FILE_SIZE_MB * 1024 * 1024
            if attachment.size > max_size:
                await interaction.followup.send(
                    f"❌ 文件过大，请上传小于 {MAX_FILE_SIZE_MB}MB 的图片",
                    ephemeral=True
                )
                return
            
            # 保存临时文件
            temp_file = TEMP_DIR / f"audit_{uuid.uuid4().hex}_{attachment.filename}"
            await attachment.save(temp_file)
            
            # 并发调用审计功能
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self.executor,
                check_watermark,
                str(temp_file),
                self.config
            )
            
            # 构建结果 Embed
            if result['has_watermark']:
                status = "✅ 检测到追溯标识"
                info = (
                    f"**原作者 UID:** `{result['original_uid']}`\n"
                    f"**当前持有者 UID:** `{result['current_uid']}`\n"
                    f"**版本类型:** {'🎯 母带版本' if result['is_master'] else '📦 分发版本'}\n"
                    f"**置信度:** {result['confidence']*100:.1f}%"
                )
                permissions = (
                    f"• 允许转载: {'✅ 是' if result['allow_reprint'] else '❌ 否'}\n"
                    f"• 允许二改: {'✅ 是' if result['allow_derivative'] else '❌ 否'}"
                )
                color = EMBED_COLOR
            else:
                status = "❌ 未检测到追溯标识"
                info = "该图片可能未经系统处理，或追溯标识已被破坏。"
                permissions = "无权限信息"
                color = 0xFF9900
            
            embed = discord.Embed(
                title="🔍 追溯审查结果",
                color=color
            )
            
            embed.add_field(name="🔍 标识状态", value=status, inline=False)
            embed.add_field(name="ℹ️ 追溯信息", value=info, inline=False)
            embed.add_field(name="🛡️ 权限配置", value=permissions, inline=False)
            
            embed.set_footer(text="ShadowGuard Protocol - 角色卡追溯系统")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            print(f"[错误] 审查失败: {traceback.format_exc()}")
            await interaction.followup.send(
                "❌ 审查失败：无法分析该图片，请确认文件格式正确",
                ephemeral=True
            )
        finally:
            # 确保清理临时文件
            if temp_file and temp_file.exists():
                try:
                    temp_file.unlink()
                except Exception as e:
                    print(f"[警告] 清理审计临时文件失败: {e}")
    
    @app_commands.command(name="使用说明", description="查看角色卡系统使用说明")
    async def instructions(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📘 角色卡追溯系统使用说明",
            description="欢迎使用 ShadowGuard Protocol (SGP) 角色卡追溯管理系统！",
            color=EMBED_COLOR
        )
        
        embed.add_field(
            name="📤 1. 上传角色卡",
            value=(
                "使用 `/上传角色卡` 命令上传 PNG 格式的角色卡。\n"
                "• 系统会自动生成母带并嵌入您的专属追溯标识\n"
                "• 可配置转载和二改权限\n"
                "• 母带仅存储于服务器，不会公开分发"
            ),
            inline=False
        )
        
        embed.add_field(
            name="📥 2. 下载角色卡",
            value=(
                "使用 `/下载角色卡` 命令获取当前帖子的角色卡。\n"
                "• 系统会自动为您生成带有专属追溯标识的副本\n"
                "• 文件中包含您的 Discord UID，仅供个人使用\n"
                "• 若发现泄露，可通过追溯系统定位到源头"
            ),
            inline=False
        )
        
        embed.add_field(
            name="⚙️ 3. 管理角色卡",
            value=(
                "使用 `/管理角色卡` 命令管理您上传的卡片。\n"
                "• 删除不再需要的卡片\n"
                "• 修改转载和二改权限\n"
                "• 仅能管理自己上传的卡片"
            ),
            inline=False
        )
        
        embed.add_field(
            name="🔍 4. 审查角色卡",
            value=(
                "使用 `/审查角色卡` 命令检查图片的追溯信息。\n"
                "• 查看原作者和当前持有者 UID\n"
                "• 确认权限配置（转载/二改）\n"
                "• 识别母带版本或分发版本"
            ),
            inline=False
        )
        
        embed.add_field(
            name="⚠️ 重要提示",
            value=(
                "• 所有下载的角色卡均包含您的专属追溯标识\n"
                "• 请勿将文件随意传播\n"
                "• 尊重原作者的权限设置\n"
                "• 系统可追溯所有分发记录"
            ),
            inline=False
        )
        
        embed.set_footer(text="ShadowGuard Protocol v5.0 - DWT+DCT+QIM 混合水印系统")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ==================== Bot 主程序 ====================

class SGPBot(commands.Bot):
    """SGP Discord Bot 主类"""
    
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None
        )
    
    async def setup_hook(self):
        """Bot 启动时的钩子函数"""
        await self.add_cog(SGPCog(self))
        print("[Bot] ✅ 命令已注册")
    
    async def on_ready(self):
        """Bot 就绪事件处理"""
        print(f"[Bot] ✅ 已登录为 {self.user}")
        print(f"[Bot] Discord.py 版本: {discord.__version__}")
        print(f"[Bot] 已连接到 {len(self.guilds)} 个服务器")
        
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="角色卡追溯系统 | /使用说明"
            )
        )


# ==================== 启动入口 ====================

def main():
    """主函数"""
    print("=" * 60)
    print("ShadowGuard Protocol (SGP) - Discord Bot")
    print("DWT+DCT+QIM 混合水印系统 v5.0")
    print("=" * 60)
    
    # 初始化数据库
    init_database()
    
    # 从 config.ini 读取 Token
    try:
        config = load_config()
        token = config.get('bot_token')
        
        if not token or token == "YOUR_DISCORD_BOT_TOKEN_HERE":
            print("\n[错误] ❌ 请在 config.ini 中设置 Discord Bot Token")
            print("[提示] 从 Discord Developer Portal 获取 Token 并填写到 config.ini 的 [Discord] 部分")
            print("[提示] 配置文件路径: ./config.ini")
            return
        
        print(f"[配置] ✅ 配置加载成功")
        print(f"[配置] 存储根目录: {config.get('master_dir', 'storage/masters')}")
        
    except FileNotFoundError:
        print("\n[错误] ❌ 找不到 config.ini 文件")
        print("[提示] 请在项目根目录创建 config.ini 文件")
        return
    except Exception as e:
        print(f"\n[错误] ❌ 配置加载失败: {e}")
        traceback.print_exc()
        return
    
    # 启动 Bot
    bot = SGPBot()
    
    try:
        print("\n[Bot] 🚀 正在启动...")
        bot.run(token)
    except KeyboardInterrupt:
        print("\n[Bot] ⏹️ 正在关闭...")
    except discord.LoginFailure:
        print("\n[错误] ❌ Bot Token 无效，请检查 config.ini 中的配置")
    except Exception as e:
        print(f"\n[错误] ❌ Bot 运行失败: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
