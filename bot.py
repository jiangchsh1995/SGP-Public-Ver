import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import sqlite3
import os
import uuid
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from concurrent.futures import ThreadPoolExecutor
import traceback
from urllib.parse import unquote

# SGP 核心模块导入
from src.watermark_service import (
    load_config,
    create_master_copy,
    generate_distribution,
    check_watermark,
    update_master_permissions
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

class PaginatedCardView(discord.ui.View):
    """分页卡片视图（使用下拉菜单+翻页按钮）"""
    
    def __init__(self, cards: List[Tuple], action: str, master_dir: Path, page: int = 0):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cards = cards
        self.action = action
        self.master_dir = master_dir
        self.page = page
        self.items_per_page = 10
        self.total_pages = (len(cards) + self.items_per_page - 1) // self.items_per_page
        
        # 格式化时间显示（UTC+8）
        def format_time(timestamp: str) -> str:
            if not timestamp:
                return "未知时间"
            try:
                # 处理SQLite的TIMESTAMP格式
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00').replace(' ', 'T'))
                # 转换为北京时间（UTC+8）
                dt_beijing = dt + timedelta(hours=8)
                return dt_beijing.strftime("%Y-%m-%d %H:%M")
            except Exception as e:
                print(f"[警告] 时间格式化失败: {timestamp}, 错误: {e}")
                return "未知时间"
        
        # 获取当前页的卡片
        start_idx = page * self.items_per_page
        end_idx = min(start_idx + self.items_per_page, len(cards))
        current_cards = cards[start_idx:end_idx]
        
        # 创建下拉菜单选项
        options = []
        for idx, card in enumerate(current_cards):
            card_id = card[0]
            filename = card[1]
            
            # 根据action确定created_at的位置
            # download: (id, filename, stored_filename, file_path, uploader_id, allow_repost, allow_modify, created_at)
            # manage:   (id, filename, stored_filename, file_path, allow_repost, allow_modify, created_at)
            if self.action == "download":
                created_at = card[7] if len(card) > 7 else None
            else:  # manage
                created_at = card[6] if len(card) > 6 else None
            
            # Discord下拉菜单标签限制100字符，描述限制100字符
            display_name = filename[:70] + "..." if len(filename) > 70 else filename
            time_str = format_time(created_at)
            
            options.append(
                discord.SelectOption(
                    label=f"{start_idx + idx + 1}. {display_name}",
                    description=f"上传: {time_str}",
                    value=str(card_id)
                )
            )
        
        # 添加下拉菜单
        if options:
            select = discord.ui.Select(
                placeholder="请选择角色卡...",
                options=options,
                custom_id="card_select"
            )
            select.callback = self.select_callback
            self.add_item(select)
        
        # 添加翻页按钮（如果有多页）
        if self.total_pages > 1:
            # 上一页按钮
            prev_button = discord.ui.Button(
                label="◀️ 上一页",
                style=discord.ButtonStyle.secondary,
                disabled=(page == 0),
                custom_id="prev_page"
            )
            prev_button.callback = self.prev_page_callback
            self.add_item(prev_button)
            
            # 页码显示
            page_button = discord.ui.Button(
                label=f"第 {page + 1}/{self.total_pages} 页",
                style=discord.ButtonStyle.secondary,
                disabled=True,
                custom_id="page_info"
            )
            self.add_item(page_button)
            
            # 下一页按钮
            next_button = discord.ui.Button(
                label="下一页 ▶️",
                style=discord.ButtonStyle.secondary,
                disabled=(page >= self.total_pages - 1),
                custom_id="next_page"
            )
            next_button.callback = self.next_page_callback
            self.add_item(next_button)
    
    async def select_callback(self, interaction: discord.Interaction):
        """下拉菜单选择回调"""
        card_id = int(interaction.data['values'][0])
        
        # 从卡片列表中找到对应的卡片数据
        card_data = None
        for card in self.cards:
            if card[0] == card_id:
                card_data = card
                break
        
        if not card_data:
            await interaction.response.send_message(
                "❌ 未找到该卡片",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True)
        
        if self.action == "download":
            await self.handle_download(interaction, card_data)
        elif self.action == "manage":
            await self.handle_manage(interaction, card_data)
    
    async def handle_download(self, interaction: discord.Interaction, card_data: Tuple):
        """处理下载请求"""
        try:
            card_id, filename, stored_filename, file_path, uploader_id, allow_repost, allow_modify = card_data[:7]
            
            # 路径处理 - 兼容旧数据（包含storage/masters前缀）和新数据（纯相对路径）
            file_path_str = str(file_path).replace('\\', '/')  # 统一路径分隔符
            file_path_obj = Path(file_path)
            
            # 检查是否已包含 storage/masters 路径（旧数据）
            if 'storage' in file_path_str.lower() and 'masters' in file_path_str.lower():
                # 旧数据：路径已包含完整前缀，直接使用
                real_file_path = Path(file_path_str)
            elif file_path_obj.is_absolute():
                # 绝对路径
                real_file_path = file_path_obj
            else:
                # 新数据：纯相对路径（guild_id/channel_id/filename），需要拼接
                real_file_path = self.master_dir / file_path
            
            # 检查文件是否存在
            if not real_file_path.exists():
                await interaction.followup.send(
                    f"❌ 文件已丢失：母带文件不存在\n调试信息：`{real_file_path}`",
                    ephemeral=True
                )
                return
            
            # 并发生成分发版本
            loop = asyncio.get_event_loop()
            config = await loop.run_in_executor(None, load_config)
            
            dist_path = await loop.run_in_executor(
                None,
                generate_distribution,
                str(real_file_path),
                interaction.user.id,
                config
            )
            
            # 发送文件
            file = discord.File(dist_path, filename=filename)
            
            # 根据作者权限设置动态生成使用提示
            usage_tips = []
            if allow_repost and allow_modify:
                usage_tips.append("• 作者同意转载和二次创作")
            elif allow_repost:
                usage_tips.append("• 作者同意转载")
            elif allow_modify:
                usage_tips.append("• 作者同意二次创作")
            else:
                usage_tips.append("• 仅供个人使用，请勿随意传播")
            
            usage_text = "\n".join(usage_tips)
            
            embed = discord.Embed(
                title="📥 角色卡已生成",
                description=(
                    f"**文件名:** {filename}\n"
                    f"**上传者:** <@{uploader_id}>\n\n"
                    f"⚠️ **重要提示:**\n"
                    f"• 此文件已嵌入您的专属追溯标识\n"
                    f"{usage_text}\n"
                    f"• 若出现在第三方商业网站，系统可追溯到您的 DC 账号"
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
            # get_user_cards_in_channel返回7个字段（没有uploader_id）
            card_id, filename, stored_filename, file_path, allow_repost, allow_modify, created_at = card_data[:7]
            
            # 格式化时间显示（UTC+8）
            def format_time(timestamp: str) -> str:
                try:
                    dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    dt_beijing = dt + timedelta(hours=8)
                    return dt_beijing.strftime("%Y-%m-%d %H:%M")
                except:
                    return "未知时间"
            
            view = CardManageView(card_id, filename, file_path, allow_repost, allow_modify, self.master_dir, created_at)
            
            embed = discord.Embed(
                title="⚙️ 管理角色卡",
                description=(
                    f"**文件名:** {filename}\n"
                    f"**上传时间:** {format_time(created_at) if created_at else '未知时间'}\n"
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
    
    async def prev_page_callback(self, interaction: discord.Interaction):
        """上一页回调"""
        if self.page > 0:
            new_view = PaginatedCardView(self.cards, self.action, self.master_dir, self.page - 1)
            
            if self.action == "download":
                title = "📥 选择角色卡"
                desc = f"当前帖子共有 **{len(self.cards)}** 张角色卡可供下载。\n请点击按钮选择："
            else:
                title = "⚙️ 管理角色卡"
                desc = f"您在当前帖子共有 **{len(self.cards)}** 张角色卡。\n请点击按钮选择要管理的卡片："
            
            embed = discord.Embed(
                title=title,
                description=desc,
                color=EMBED_COLOR
            )
            
            await interaction.response.edit_message(embed=embed, view=new_view)
    
    async def next_page_callback(self, interaction: discord.Interaction):
        """下一页回调"""
        if self.page < self.total_pages - 1:
            new_view = PaginatedCardView(self.cards, self.action, self.master_dir, self.page + 1)
            
            if self.action == "download":
                title = "📥 选择角色卡"
                desc = f"当前帖子共有 **{len(self.cards)}** 张角色卡可供下载。\n请点击按钮选择："
            else:
                title = "⚙️ 管理角色卡"
                desc = f"您在当前帖子共有 **{len(self.cards)}** 张角色卡。\n请点击按钮选择要管理的卡片："
            
            embed = discord.Embed(
                title=title,
                description=desc,
                color=EMBED_COLOR
            )
            
            await interaction.response.edit_message(embed=embed, view=new_view)


class CardManageView(discord.ui.View):
    """卡片管理视图"""
    
    def __init__(self, card_id: int, filename: str, file_path: str, 
                 allow_repost: bool, allow_modify: bool, master_dir: Path, created_at: str = None):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.card_id = card_id
        self.filename = filename
        self.file_path = file_path  # 相对路径
        self.allow_repost = allow_repost
        self.allow_modify = allow_modify
        self.master_dir = master_dir  # 母带存储根目录，用于还原绝对路径
        self.created_at = created_at  # 上传时间
    
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
        # 格式化时间显示（UTC+8）
        def format_time(timestamp: str) -> str:
            try:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                dt_beijing = dt + timedelta(hours=8)
                return dt_beijing.strftime("%Y-%m-%d %H:%M")
            except:
                return "未知时间"
        
        # 使用下拉菜单视图替代模态框
        view = PermissionEditView(
            self.card_id, 
            self.filename, 
            self.file_path,
            self.allow_repost, 
            self.allow_modify,
            self.master_dir
        )
        
        time_str = format_time(self.created_at) if self.created_at else "未知时间"
        
        embed = discord.Embed(
            title="🔧 修改权限",
            description=(
                f"**文件名:** {self.filename}\n"
                f"**上传时间:** {time_str}\n\n"
                f"**当前权限:**\n"
                f"• 允许转载: {'✅ 是' if self.allow_repost else '❌ 否'}\n"
                f"• 允许二改: {'✅ 是' if self.allow_modify else '❌ 否'}\n\n"
                f"请使用下拉菜单选择新的权限设置："
            ),
            color=EMBED_COLOR
        )
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="已取消",
            description="管理操作已取消",
            color=EMBED_COLOR
        )
        await interaction.response.edit_message(embed=embed, view=None)


class PermissionEditView(discord.ui.View):
    """权限编辑视图（使用下拉菜单）"""
    
    def __init__(self, card_id: int, filename: str, file_path: str,
                 current_repost: bool, current_modify: bool, master_dir: Path):
        super().__init__(timeout=VIEW_TIMEOUT)
        self.card_id = card_id
        self.filename = filename
        self.file_path = file_path
        self.current_repost = current_repost
        self.current_modify = current_modify
        self.master_dir = master_dir
        
        # 添加转载权限选择器
        self.repost_select = discord.ui.Select(
            placeholder="选择转载权限",
            options=[
                discord.SelectOption(
                    label="✅ 允许转载",
                    value="true",
                    description="允许他人转载此卡片",
                    default=current_repost
                ),
                discord.SelectOption(
                    label="❌ 禁止转载",
                    value="false",
                    description="禁止他人转载此卡片",
                    default=not current_repost
                )
            ],
            custom_id="repost_select"
        )
        
        # 添加二改权限选择器
        self.modify_select = discord.ui.Select(
            placeholder="选择二改权限",
            options=[
                discord.SelectOption(
                    label="✅ 允许二改",
                    value="true",
                    description="允许他人二次创作",
                    default=current_modify
                ),
                discord.SelectOption(
                    label="❌ 禁止二改",
                    value="false",
                    description="禁止他人二次创作",
                    default=not current_modify
                )
            ],
            custom_id="modify_select"
        )
        
        self.add_item(self.repost_select)
        self.add_item(self.modify_select)
        
        # 用于存储选择的值
        self.new_repost = current_repost
        self.new_modify = current_modify
        
        # 设置回调
        self.repost_select.callback = self.repost_callback
        self.modify_select.callback = self.modify_callback
    
    async def repost_callback(self, interaction: discord.Interaction):
        self.new_repost = self.repost_select.values[0] == "true"
        await interaction.response.defer()
    
    async def modify_callback(self, interaction: discord.Interaction):
        self.new_modify = self.modify_select.values[0] == "true"
        await interaction.response.defer()
    
    @discord.ui.button(label="确认修改", style=discord.ButtonStyle.primary, emoji="✅")
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        try:
            # 更新数据库权限
            db_success = update_card_permissions(
                self.card_id,
                interaction.user.id,
                self.new_repost,
                self.new_modify
            )
            
            if not db_success:
                await interaction.followup.send(
                    "❌ 更新失败：您没有权限修改此卡片",
                    ephemeral=True
                )
                return
            
            # 更新母带文件追溯信息
            try:
                real_file_path = self.master_dir / self.file_path
                
                loop = asyncio.get_event_loop()
                config = await loop.run_in_executor(None, load_config)
                
                await loop.run_in_executor(
                    None,
                    update_master_permissions,
                    str(real_file_path),
                    self.new_repost,
                    self.new_modify,
                    config
                )
                
                print(f"[权限更新] ✅ 卡片 {self.card_id} 权限已更新（含母带文件）")
                
            except Exception as e:
                print(f"[警告] 母带文件权限更新失败: {traceback.format_exc()}")
                await interaction.followup.send(
                    "⚠️ 数据库权限已更新，但母带文件更新失败。请联系管理员。",
                    ephemeral=True
                )
                return
            
            embed = discord.Embed(
                title="✅ 权限已更新",
                description=(
                    f"**文件名:** {self.filename}\n\n"
                    f"**新权限:**\n"
                    f"• 允许转载: {'✅ 是' if self.new_repost else '❌ 否'}\n"
                    f"• 允许二改: {'✅ 是' if self.new_modify else '❌ 否'}\n\n"
                    f"数据库和母带文件均已更新。"
                ),
                color=EMBED_COLOR
            )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            print(f"[错误] 权限更新失败: {traceback.format_exc()}")
            await interaction.followup.send(
                "❌ 更新失败：权限设置出现错误",
                ephemeral=True
            )
    
    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="已取消",
            description="权限修改已取消",
            color=EMBED_COLOR
        )
        await interaction.response.edit_message(embed=embed, view=None)


# ==================== Bot Commands Cog ====================

class SGPCog(commands.Cog):
    """SGP 角色卡追溯系统命令集"""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self.config = load_config()
        
        # 全局配置：从配置文件读取母带存储根目录
        self.master_dir = Path(self.config.get('master_dir', 'storage/masters'))
        self.master_dir.mkdir(parents=True, exist_ok=True)
    
    @app_commands.command(name="上传角色卡", description="上传角色卡并生成追溯母带")
    @app_commands.describe(
        attachment="PNG 格式的角色卡图片",
        name="角色卡名称 (可选，若不填则尝试自动获取)",
        allow_repost="是否允许他人转载",
        allow_modify="是否允许他人二次创作"
    )
    @app_commands.rename(
        attachment="附件",
        name="名称",
        allow_repost="是否允许转载",
        allow_modify="是否允许二改"
    )
    @app_commands.choices(
        allow_repost=[
            app_commands.Choice(name="是", value=1),
            app_commands.Choice(name="否", value=0)
        ],
        allow_modify=[
            app_commands.Choice(name="是", value=1),
            app_commands.Choice(name="否", value=0)
        ]
    )
    async def upload_card(
        self,
        interaction: discord.Interaction,
        attachment: discord.Attachment,
        name: str = None,
        allow_repost: int = 0,
        allow_modify: int = 0
    ):
        await interaction.response.defer(ephemeral=True)

        # ==================== 文件名获取逻辑（支持手动指定+自动恢复）====================
        # 优先级1: 用户手动输入的名称参数
        if name:
            # 获取真实文件的后缀名
            ext = os.path.splitext(attachment.filename)[1]
            # 如果用户输入的名称没带后缀，自动补上
            if not name.endswith(ext):
                original_filename = f"{name}{ext}"
            else:
                original_filename = name
            print(f"DEBUG: 使用手动指定文件名: {original_filename}")
        
        # 优先级2: 尝试从 URL 自动获取（修复Discord客户端中文丢失问题）
        else:
            # Step A: Extract raw filename from attachment.url
            # URL format: https://.../filename.png?ex=...
            raw_url_name = attachment.url.split('?')[0].split('/')[-1]
            
            # Step B: Decode URL-encoded filename
            decoded_name = unquote(raw_url_name)
            
            # Step C: Smart Selection Logic
            # If decoded_name contains non-ASCII characters or differs from attachment.filename,
            # treat decoded_name as the True Original Filename
            original_filename = decoded_name
            
            # Check if decoded_name has non-ASCII characters or is different/longer
            has_non_ascii = any(ord(char) > 127 for char in decoded_name)
            is_different = decoded_name != attachment.filename
            
            if not has_non_ascii and not is_different:
                # Fallback to attachment.filename if no difference detected
                original_filename = attachment.filename
            
            # Debug logging
            print(f"DEBUG: Discord attachment.filename: {attachment.filename}")
            print(f"DEBUG: URL raw name: {raw_url_name}")
            print(f"DEBUG: URL decoded name: {decoded_name}")
            print(f"DEBUG: Restored Filename: {original_filename}")
        # ==================== 文件名获取逻辑结束 ====================

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
            
            # 生成安全的存储文件名（UUID + 扩展名），避免非ASCII字符问题
            file_extension = os.path.splitext(original_filename)[1]  # 从恢复的原始文件名获取扩展名
            stored_filename = f"{uuid.uuid4().hex}{file_extension}"  # 纯UUID文件名
            full_path = storage_path / stored_filename
            
            # 保存临时文件
            temp_file = TEMP_DIR / f"{interaction.user.id}_{attachment.filename}"
            await attachment.save(temp_file)
            
            # 并发调用 SGP Core 制作母带
            loop = asyncio.get_event_loop()
            
            # 更新配置（将int转为bool）
            config = self.config.copy()
            config['owner_uuid'] = interaction.user.id
            config['allow_reprint'] = bool(allow_repost)
            config['allow_derivative'] = bool(allow_modify)
            
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
            
            # 写入数据库 - 存储相对路径，而非绝对路径（将int转为bool）
            # 使用 original_filename（恢复的原始文件名）而不是 attachment.filename
            card_id = add_card(
                guild_id=guild_id,
                channel_id=channel_id,
                parent_id=parent_id,
                uploader_id=interaction.user.id,
                filename=original_filename,  # ← 使用恢复的原始文件名
                stored_filename=stored_filename,
                file_path=relative_path,  # ← 存储相对路径
                allow_repost=bool(allow_repost),
                allow_modify=bool(allow_modify)
            )
            
            # 清理临时文件
            temp_file.unlink(missing_ok=True)
            
            # 返回成功消息 - 显示恢复的原始文件名
            embed = discord.Embed(
                title="✅ 上传成功",
                description=(
                    f"**文件名:** {original_filename}\n"
                    f"**卡片 ID:** {card_id}\n"
                    f"**权限配置:**\n"
                    f"• 允许转载: {'✅ 是' if allow_repost else '❌ 否'}\n"
                    f"• 允许二改: {'✅ 是' if allow_modify else '❌ 否'}\n\n"
                    f"母带已生成并保存到安全存储区。\n"
                    f"其他用户可通过 `/下载角色卡` 获取带追溯标识的副本。"
                ),
                color=EMBED_COLOR
            )
            
            embed.set_footer(text="ShadowGuard Protocol - 角色卡追溯系统")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            print(f"[上传] ✅ 用户 {interaction.user.id} 上传: {original_filename} (ID: {card_id})")
            
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
    
    @app_commands.command(name="下载角色卡", description="下载当前帖子的角色卡（自动添加追溯标识）")
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
            
            # 创建分页视图（每页最多10个）
            view = PaginatedCardView(cards, action="download", master_dir=self.master_dir, page=0)
            
            embed = discord.Embed(
                title="📥 选择角色卡",
                description=f"当前帖子共有 **{len(cards)}** 张角色卡可供下载。\n请点击按钮选择：",
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
            
            # 创建分页视图（每页最多10个）
            view = PaginatedCardView(cards, action="manage", master_dir=self.master_dir, page=0)
            
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
    
    @app_commands.command(name="审查角色卡", description="检查图片的溯源信息")
    @app_commands.describe(attachment="要审查的图片")
    @app_commands.rename(attachment="附件")
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
        
        embed.set_footer(text="角色卡追溯系统 - ShadowGuard Protocol v5.0")
        
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
        await self.tree.sync()
        print("[Bot] ✅ 命令已请求全球同步（请等待生效）")
    
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
    print("DWT+DCT+QIM 混合追溯系统 v5.0")
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
