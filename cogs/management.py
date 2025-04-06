import discord
from discord.ext import commands
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from .utils import Embed, event_dispatcher
from database import get_connection
import sqlite3
import logging

logger = logging.getLogger(__name__)

class Management(commands.Cog):
    """⚙️ Advanced Server Management System"""
    
    def __init__(self, bot):
        self.bot = bot
        self.register_handlers()

    def setup_tables(self):
        """Setup necessary database tables"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Server settings
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS server_settings (
                    guild_id TEXT PRIMARY KEY,
                    prefix TEXT DEFAULT '!',
                    auto_role TEXT,
                    mute_role TEXT,
                    mod_role TEXT,
                    admin_role TEXT,
                    suggestion_channel TEXT,
                    report_channel TEXT,
                    log_channel TEXT,
                    join_age INTEGER DEFAULT 0,
                    verification_required BOOLEAN DEFAULT FALSE
                )
            """)
            
            # Channel permissions
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS channel_permissions (
                    guild_id TEXT,
                    channel_id TEXT,
                    role_id TEXT,
                    permission_type TEXT,
                    allowed BOOLEAN DEFAULT TRUE,
                    PRIMARY KEY (guild_id, channel_id, role_id, permission_type)
                )
            """)
            
            # Scheduled tasks
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT,
                    task_type TEXT,
                    execute_at DATETIME,
                    data TEXT,
                    created_by TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.commit()
            logger.info("Management tables created successfully")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to setup management tables: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()

    def register_handlers(self):
        """Register event handlers"""
        event_dispatcher.register('role_update', self.log_role_change)
        event_dispatcher.register('channel_update', self.log_channel_change)
        event_dispatcher.register('permission_update', self.log_permission_change)

    def get_settings(self, guild_id: int) -> Dict:
        """Get server settings"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM server_settings WHERE guild_id = ?
            """, (str(guild_id),))
            data = cursor.fetchone()
            
            if not data:
                default_settings = {
                    'prefix': '!',
                    'auto_role': None,
                    'mute_role': None,
                    'mod_role': None,
                    'admin_role': None,
                    'suggestion_channel': None,
                    'report_channel': None,
                    'log_channel': None,
                    'join_age': 0,
                    'verification_required': False
                }
                
                cursor.execute("""
                    INSERT INTO server_settings (guild_id, prefix)
                    VALUES (?, ?)
                """, (str(guild_id), '!'))
                conn.commit()
                return default_settings
                
            return dict(data)
        except sqlite3.Error as e:
            logger.error(f"Failed to get server settings: {e}")
            raise
        finally:
            if conn:
                conn.close()

    @commands.group(name="config")
    @commands.has_permissions(administrator=True)
    async def config(self, ctx):
        """⚙️ Server configuration commands"""
        if ctx.invoked_subcommand is None:
            settings = self.get_settings(ctx.guild.id)
            
            embed = Embed.create(
                title="⚙️ Server Configuration",
                color=discord.Color.blue(),
                field_Prefix=settings['prefix'],
                field_Auto_Role=f"<@&{settings['auto_role']}>" if settings['auto_role'] else "None",
                field_Mute_Role=f"<@&{settings['mute_role']}>" if settings['mute_role'] else "None",
                field_Mod_Role=f"<@&{settings['mod_role']}>" if settings['mod_role'] else "None",
                field_Admin_Role=f"<@&{settings['admin_role']}>" if settings['admin_role'] else "None",
                field_Join_Age=f"{settings['join_age']} days" if settings['join_age'] > 0 else "None",
                field_Verification=str(settings['verification_required'])
            )
            
            await ctx.send(embed=embed)

    @config.command(name="prefix")
    async def set_prefix(self, ctx, prefix: str):
        """Set server prefix"""
        if len(prefix) > 5:
            return await ctx.send("❌ Prefix must be 5 characters or less!")
        
        conn = None    
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE server_settings
                SET prefix = ?
                WHERE guild_id = ?
            """, (prefix, str(ctx.guild.id)))
            conn.commit()
            
            await ctx.send(f"✅ Prefix set to `{prefix}`")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to set prefix: {e}")
            await ctx.send("❌ An error occurred while setting the prefix")
        finally:
            if conn:
                conn.close()

    @config.command(name="autorole")
    async def set_auto_role(self, ctx, role: discord.Role = None):
        """Set auto-role for new members"""
        role_id = str(role.id) if role else None
        
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE server_settings
                SET auto_role = ?
                WHERE guild_id = ?
            """, (role_id, str(ctx.guild.id)))
            conn.commit()
            
            if role:
                await ctx.send(f"✅ Auto-role set to {role.mention}")
            else:
                await ctx.send("✅ Auto-role disabled")
                
        except sqlite3.Error as e:
            logger.error(f"Failed to set auto-role: {e}")
            await ctx.send("❌ An error occurred while setting the auto-role")
        finally:
            if conn:
                conn.close()

    @config.command(name="muterole")
    async def set_mute_role(self, ctx, role: discord.Role = None):
        """Set mute role"""
        if role:
            # Setup role permissions
            for channel in ctx.guild.channels:
                try:
                    await channel.set_permissions(role, send_messages=False, speak=False)
                except discord.Forbidden:
                    continue
                    
        role_id = str(role.id) if role else None
        
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE server_settings
                SET mute_role = ?
                WHERE guild_id = ?
            """, (role_id, str(ctx.guild.id)))
            conn.commit()
            
            if role:
                await ctx.send(f"✅ Mute role set to {role.mention}")
            else:
                await ctx.send("✅ Mute role disabled")
                
        except sqlite3.Error as e:
            logger.error(f"Failed to set mute role: {e}")
            await ctx.send("❌ An error occurred while setting the mute role")
        finally:
            if conn:
                conn.close()

    @config.command(name="modrole")
    async def set_mod_role(self, ctx, role: discord.Role = None):
        """Set moderator role"""
        role_id = str(role.id) if role else None
        
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE server_settings
                SET mod_role = ?
                WHERE guild_id = ?
            """, (role_id, str(ctx.guild.id)))
            conn.commit()
            
            if role:
                await ctx.send(f"✅ Moderator role set to {role.mention}")
            else:
                await ctx.send("✅ Moderator role disabled")
                
        except sqlite3.Error as e:
            logger.error(f"Failed to set mod role: {e}")
            await ctx.send("❌ An error occurred while setting the moderator role")
        finally:
            if conn:
                conn.close()

    @config.command(name="adminrole")
    async def set_admin_role(self, ctx, role: discord.Role = None):
        """Set administrator role"""
        role_id = str(role.id) if role else None
        
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE server_settings
                SET admin_role = ?
                WHERE guild_id = ?
            """, (role_id, str(ctx.guild.id)))
            conn.commit()
            
            if role:
                await ctx.send(f"✅ Administrator role set to {role.mention}")
            else:
                await ctx.send("✅ Administrator role disabled")
                
        except sqlite3.Error as e:
            logger.error(f"Failed to set admin role: {e}")
            await ctx.send("❌ An error occurred while setting the administrator role")
        finally:
            if conn:
                conn.close()

    @config.command(name="verification")
    async def toggle_verification(self, ctx, required: bool = None):
        """Toggle member verification requirement"""
        if required is None:
            settings = self.get_settings(ctx.guild.id)
            required = not settings['verification_required']
            
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE server_settings
                SET verification_required = ?
                WHERE guild_id = ?
            """, (required, str(ctx.guild.id)))
            conn.commit()
            
            await ctx.send(f"✅ Verification requirement {'enabled' if required else 'disabled'}")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to toggle verification: {e}")
            await ctx.send("❌ An error occurred while updating verification settings")
        finally:
            if conn:
                conn.close()

    @config.command(name="joinage")
    async def set_join_age(self, ctx, days: int):
        """Set minimum account age to join (0 to disable)"""
        if days < 0:
            return await ctx.send("❌ Days must be 0 or positive!")
            
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE server_settings
                SET join_age = ?
                WHERE guild_id = ?
            """, (days, str(ctx.guild.id)))
            conn.commit()
            
            if days > 0:
                await ctx.send(f"✅ Minimum account age set to {days} days")
            else:
                await ctx.send("✅ Account age requirement disabled")
                
        except sqlite3.Error as e:
            logger.error(f"Failed to set join age: {e}")
            await ctx.send("❌ An error occurred while setting the join age")
        finally:
            if conn:
                conn.close()

    @commands.group(name="channel")
    @commands.has_permissions(manage_channels=True)
    async def channel(self, ctx):
        """📝 Channel management commands"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @channel.command(name="lock")
    async def lock_channel(self, ctx, channel: discord.TextChannel = None):
        """Lock a channel"""
        channel = channel or ctx.channel
        
        await channel.set_permissions(ctx.guild.default_role, send_messages=False)
        await ctx.send(f"🔒 {channel.mention} has been locked")

    @channel.command(name="unlock")
    async def unlock_channel(self, ctx, channel: discord.TextChannel = None):
        """Unlock a channel"""
        channel = channel or ctx.channel
        
        await channel.set_permissions(ctx.guild.default_role, send_messages=True)
        await ctx.send(f"🔓 {channel.mention} has been unlocked")

    @channel.command(name="slowmode")
    async def set_slowmode(self, ctx, seconds: int, channel: discord.TextChannel = None):
        """Set channel slowmode"""
        channel = channel or ctx.channel
        
        if seconds < 0:
            return await ctx.send("❌ Slowmode must be 0 or positive!")
            
        await channel.edit(slowmode_delay=seconds)
        
        if seconds > 0:
            await ctx.send(f"⏱️ Slowmode in {channel.mention} set to {seconds} seconds")
        else:
            await ctx.send(f"⏱️ Slowmode in {channel.mention} disabled")

    @channel.command(name="clone")
    async def clone_channel(self, ctx, channel: discord.TextChannel = None):
        """Clone a channel"""
        channel = channel or ctx.channel
        
        cloned = await channel.clone()
        await ctx.send(f"✅ Channel cloned: {cloned.mention}")

    @commands.group(name="clean")
    @commands.has_permissions(manage_messages=True)
    async def clean(self, ctx):
        """🧹 Channel cleanup commands"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @clean.command(name="messages")
    async def clean_messages(self, ctx, amount: int = 100):
        """Clean messages in a channel"""
        if amount < 1:
            return await ctx.send("❌ Amount must be positive!")
            
        deleted = await ctx.channel.purge(limit=amount + 1)  # +1 for command message
        
        msg = await ctx.send(f"✅ Deleted {len(deleted) - 1} messages")
        await asyncio.sleep(3)
        await msg.delete()

    @clean.command(name="user")
    async def clean_user_messages(self, ctx, user: discord.Member, amount: int = 100):
        """Clean messages from a specific user"""
        if amount < 1:
            return await ctx.send("❌ Amount must be positive!")
            
        def check(msg):
            return msg.author == user
            
        deleted = await ctx.channel.purge(limit=amount, check=check)
        
        msg = await ctx.send(f"✅ Deleted {len(deleted)} messages from {user.mention}")
        await asyncio.sleep(3)
        await msg.delete()

    @clean.command(name="bots")
    async def clean_bot_messages(self, ctx, amount: int = 100):
        """Clean bot messages"""
        if amount < 1:
            return await ctx.send("❌ Amount must be positive!")
            
    @clean.command(name="bots")
    async def clean_bot_messages(self, ctx, amount: int = 100):
        """Clean bot messages"""
        if amount < 1:
            return await ctx.send("❌ Amount must be positive!")
            
        def check(msg):
            return msg.author.bot
            
        deleted = await ctx.channel.purge(limit=amount, check=check)
        
        msg = await ctx.send(f"✅ Deleted {len(deleted)} bot messages")
        await asyncio.sleep(3)
        await msg.delete()

    async def log_role_change(self, guild: discord.Guild, role: discord.Role, action: str):
        """Log role changes"""
        settings = self.get_settings(guild.id)
        if not settings['log_channel']:
            return
            
        channel = guild.get_channel(int(settings['log_channel']))
        if not channel:
            return
            
        embed = Embed.create(
            title="👥 Role Update",
            color=discord.Color.blue(),
            field_Role=role.name,
            field_Action=action
        )
        
        await channel.send(embed=embed)

    async def log_channel_change(self, guild: discord.Guild, channel: discord.abc.GuildChannel, action: str):
        """Log channel changes"""
        settings = self.get_settings(guild.id)
        if not settings['log_channel']:
            return
            
        log_channel = guild.get_channel(int(settings['log_channel']))
        if not log_channel:
            return
            
        embed = Embed.create(
            title="📝 Channel Update",
            color=discord.Color.blue(),
            field_Channel=channel.name,
            field_Action=action
        )
        
        await log_channel.send(embed=embed)

    async def log_permission_change(self, guild: discord.Guild, target: str, action: str):
        """Log permission changes"""
        settings = self.get_settings(guild.id)
        if not settings['log_channel']:
            return
            
        channel = guild.get_channel(int(settings['log_channel']))
        if not channel:
            return
            
        embed = Embed.create(
            title="🔑 Permission Update",
            color=discord.Color.blue(),
            field_Target=target,
            field_Action=action
        )
        
        await channel.send(embed=embed)

async def setup(bot):
    """Setup the Management cog"""
    cog = Management(bot)
    cog.setup_tables()  # Not async anymore since using sqlite3
    await bot.add_cog(cog)