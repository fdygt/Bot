import discord
from discord.ext import commands
import sqlite3
from datetime import datetime
import random
import asyncio
from typing import Optional, Dict, List
from .utils import Embed, event_dispatcher
from database import get_connection
import logging

logger = logging.getLogger(__name__)

class Leveling(commands.Cog):
    """⭐ Advanced Leveling System"""
    
    def __init__(self, bot):
        self.bot = bot
        self.xp_cooldown = {}
        self.register_handlers()

    def setup_tables(self):
        """Setup necessary database tables"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # User levels table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_levels (
                    guild_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    xp INTEGER DEFAULT 0,
                    level INTEGER DEFAULT 0,
                    messages INTEGER DEFAULT 0,
                    last_message TIMESTAMP,
                    PRIMARY KEY (guild_id, user_id)
                )
            """)
            
            # Level rewards table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS level_rewards (
                    guild_id TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    role_id TEXT NOT NULL,
                    PRIMARY KEY (guild_id, level)
                )
            """)
            
            # Leveling settings table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS leveling_settings (
                    guild_id TEXT PRIMARY KEY,
                    enabled BOOLEAN DEFAULT TRUE,
                    announcement_channel TEXT,
                    min_xp INTEGER DEFAULT 15,
                    max_xp INTEGER DEFAULT 25,
                    cooldown INTEGER DEFAULT 60,
                    stack_rewards BOOLEAN DEFAULT TRUE,
                    ignored_channels TEXT,
                    ignored_roles TEXT,
                    double_xp_roles TEXT
                )
            """)
            
            conn.commit()
            logger.info("Leveling tables created successfully")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to setup leveling tables: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()

    def register_handlers(self):
        """Register event handlers"""
        event_dispatcher.register('level_reward', self.handle_reward)

    async def handle_reward(self, member, level):
        """Handle level rewards"""
        try:
            conn = get_connection()
            cursor = conn.cursor()

            # Get reward roles for this level
            cursor.execute("""
                SELECT role_id FROM level_rewards
                WHERE guild_id = ? AND level <= ?
                ORDER BY level DESC
            """, (str(member.guild.id), level))
            
            rewards = cursor.fetchall()
            
            # Get settings to check if rewards should stack
            cursor.execute("""
                SELECT stack_rewards FROM leveling_settings
                WHERE guild_id = ?
            """, (str(member.guild.id),))
            
            settings = cursor.fetchone()
            stack_rewards = settings['stack_rewards'] if settings else True
            
            roles_to_add = []
            for reward in rewards:
                role = member.guild.get_role(int(reward['role_id']))
                if role and role not in member.roles:
                    roles_to_add.append(role)
                    if not stack_rewards:
                        break  # Only add highest level role if not stacking
            
            if roles_to_add:
                await member.add_roles(*roles_to_add)
                
                # Get announcement channel
                cursor.execute("""
                    SELECT announcement_channel FROM leveling_settings
                    WHERE guild_id = ?
                """, (str(member.guild.id),))
                
                channel_data = cursor.fetchone()
                if channel_data and channel_data['announcement_channel']:
                    channel = member.guild.get_channel(int(channel_data['announcement_channel']))
                    if channel:
                        role_mentions = ' '.join(role.mention for role in roles_to_add)
                        await channel.send(
                            f"🎉 Congratulations {member.mention}! "
                            f"You reached level {level} and earned: {role_mentions}"
                        )

        except Exception as e:
            logger.error(f"Error handling level reward: {e}")
        finally:
            if conn:
                conn.close()

    def get_settings(self, guild_id: int) -> Dict:
        """Get leveling settings for a guild"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM leveling_settings WHERE guild_id = ?
            """, (str(guild_id),))
            data = cursor.fetchone()
            
            if not data:
                default_settings = {
                    'enabled': True,
                    'announcement_channel': None,
                    'min_xp': 15,
                    'max_xp': 25,
                    'cooldown': 60,
                    'stack_rewards': True,
                    'ignored_channels': None,
                    'ignored_roles': None,
                    'double_xp_roles': None
                }
                
                cursor.execute("""
                    INSERT INTO leveling_settings (guild_id)
                    VALUES (?)
                """, (str(guild_id),))
                conn.commit()
                return default_settings
                
            return dict(data)
            
        except sqlite3.Error as e:
            logger.error(f"Failed to get leveling settings: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def calculate_xp_for_level(self, level: int) -> int:
        """Calculate XP required for a specific level"""
        return 5 * (level ** 2) + 50 * level + 100

    def calculate_level_for_xp(self, xp: int) -> int:
        """Calculate level for a specific amount of XP"""
        level = 0
        while self.calculate_xp_for_level(level + 1) <= xp:
            level += 1
        return level

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handle message XP"""
        if message.author.bot or not message.guild:
            return
            
        # Get settings
        settings = self.get_settings(message.guild.id)
        if not settings['enabled']:
            return
            
        # Check cooldown
        user_id = str(message.author.id)
        guild_id = str(message.guild.id)
        current_time = datetime.utcnow()
        
        cooldown_key = f"{guild_id}-{user_id}"
        if cooldown_key in self.xp_cooldown:
            time_diff = (current_time - self.xp_cooldown[cooldown_key]).total_seconds()
            if time_diff < settings['cooldown']:
                return
                
        # Check ignored channels
        if settings['ignored_channels']:
            ignored_channels = settings['ignored_channels'].split(',')
            if str(message.channel.id) in ignored_channels:
                return
                
        # Check ignored roles
        if settings['ignored_roles']:
            ignored_roles = settings['ignored_roles'].split(',')
            if any(str(role.id) in ignored_roles for role in message.author.roles):
                return
        
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Calculate XP gain
            xp_gain = random.randint(settings['min_xp'], settings['max_xp'])
            
            # Check double XP roles
            if settings['double_xp_roles']:
                double_xp_roles = settings['double_xp_roles'].split(',')
                if any(str(role.id) in double_xp_roles for role in message.author.roles):
                    xp_gain *= 2
            
            # Update or insert user data
            cursor.execute("""
                INSERT INTO user_levels (guild_id, user_id, xp, messages, last_message)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                xp = xp + ?,
                messages = messages + 1,
                last_message = ?
            """, (
                guild_id,
                user_id,
                xp_gain,
                current_time,
                xp_gain,
                current_time
            ))
            
            # Get updated XP
            cursor.execute("""
                SELECT xp, level FROM user_levels
                WHERE guild_id = ? AND user_id = ?
            """, (guild_id, user_id))
            data = cursor.fetchone()
            
            if data:
                new_level = self.calculate_level_for_xp(data['xp'])
                if new_level > data['level']:
                    # Update level
                    cursor.execute("""
                        UPDATE user_levels
                        SET level = ?
                        WHERE guild_id = ? AND user_id = ?
                    """, (new_level, guild_id, user_id))
                    
                    # Handle level up
                    await self.handle_level_up(message.author, new_level)
            
            conn.commit()
            self.xp_cooldown[cooldown_key] = current_time
            
        except sqlite3.Error as e:
            logger.error(f"Failed to update user XP: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

    async def handle_level_up(self, member, new_level):
        """Handle level up events"""
        try:
            settings = self.get_settings(member.guild.id)
            
            # Send level up message
            if settings['announcement_channel']:
                channel = member.guild.get_channel(int(settings['announcement_channel']))
                if channel:
                    await channel.send(
                        f"🎉 Congratulations {member.mention}! You've reached level {new_level}!"
                    )
            
            # Trigger reward handler
            await self.handle_reward(member, new_level)
            
        except Exception as e:
            logger.error(f"Error handling level up: {e}")

    @commands.group(name="level")
    async def level(self, ctx):
        """⭐ Leveling commands"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @level.command(name="rank")
    async def show_rank(self, ctx, member: discord.Member = None):
        """Show rank for a user"""
        member = member or ctx.author
        
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM user_levels
                WHERE guild_id = ? AND user_id = ?
            """, (str(ctx.guild.id), str(member.id)))
            data = cursor.fetchone()
            
            if not data:
                return await ctx.send(f"❌ {member.mention} hasn't gained any XP yet!")
            
            # Get rank
            cursor.execute("""
                SELECT COUNT(*) as rank
                FROM user_levels
                WHERE guild_id = ? AND xp > ?
            """, (str(ctx.guild.id), data['xp']))
            rank_data = cursor.fetchone()
            
            rank = rank_data['rank'] + 1
            
            # Calculate progress to next level
            current_level_xp = self.calculate_xp_for_level(data['level'])
            next_level_xp = self.calculate_xp_for_level(data['level'] + 1)
            xp_needed = next_level_xp - current_level_xp
            xp_progress = data['xp'] - current_level_xp
            progress_percent = (xp_progress / xp_needed) * 100
            
            embed = Embed.create(
                title=f"Rank for {member.display_name}",
                color=member.color,
                field_Level=str(data['level']),
                field_Rank=f"#{rank}",
                field_XP=f"{data['xp']:,} XP",
                field_Messages=f"{data['messages']:,} messages",
                field_Progress=f"{progress_percent:.1f}% to level {data['level'] + 1}"
            )
            
            await ctx.send(embed=embed)
            
        except sqlite3.Error as e:
            logger.error(f"Failed to get user rank: {e}")
            await ctx.send("❌ An error occurred while getting rank data")
        finally:
            if conn:
                conn.close()

    @level.command(name="top")
    async def show_leaderboard(self, ctx, page: int = 1):
        """Show XP leaderboard"""
        if page < 1:
            return await ctx.send("❌ Page number must be 1 or higher!")
            
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Get total pages
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM user_levels
                WHERE guild_id = ?
            """, (str(ctx.guild.id),))
            total = cursor.fetchone()['count']
            
            per_page = 10
            pages = (total + per_page - 1) // per_page
            
            if page > pages and pages > 0:
                return await ctx.send(f"❌ Invalid page! Total pages: {pages}")
            
            # Get leaderboard data
            cursor.execute("""
                SELECT user_id, xp, level, messages
                FROM user_levels
                WHERE guild_id = ?
                ORDER BY xp DESC
                LIMIT ? OFFSET ?
            """, (str(ctx.guild.id), per_page, (page - 1) * per_page))
            leaders = cursor.fetchall()
            
            if not leaders:
                return await ctx.send("❌ No users have gained XP yet!")
            
            embed = Embed.create(
                title="🏆 XP Leaderboard",
                color=discord.Color.gold(),
                description=f"Page {page}/{pages}"
            )
            
            for i, leader in enumerate(leaders, 1):
                member = ctx.guild.get_member(int(leader['user_id']))
                name = member.display_name if member else "Unknown User"
                
                embed.add_field(
                    name=f"#{(page-1)*per_page + i}. {name}",
                    value=f"Level: {leader['level']}\n"
                          f"XP: {leader['xp']:,}\n"
                          f"Messages: {leader['messages']:,}",
                    inline=False
                )
                
            await ctx.send(embed=embed)
            
        except sqlite3.Error as e:
            logger.error(f"Failed to get leaderboard: {e}")
            await ctx.send("❌ An error occurred while getting leaderboard")
        finally:
            if conn:
                conn.close()

    @commands.group(name="levelset", aliases=["lset"])
    @commands.has_permissions(administrator=True)
    async def levelset(self, ctx):
        """⚙️ Leveling system settings"""
        if ctx.invoked_subcommand is None:
            settings = self.get_settings(ctx.guild.id)
            
            embed = Embed.create(
                title="⚙️ Leveling Settings",
                color=discord.Color.blue(),
                field_Enabled=str(settings['enabled']),
                field_XP_Range=f"{settings['min_xp']} - {settings['max_xp']}",
                field_Cooldown=f"{settings['cooldown']} seconds",
                field_Stack_Rewards=str(settings['stack_rewards'])
            )
            
            if settings['announcement_channel']:
                channel = ctx.guild.get_channel(int(settings['announcement_channel']))
                if channel:
                    embed.add_field(name="Announcement Channel", value=channel.mention)
            
            if settings['ignored_channels']:
                channels = [f"<#{c}>" for c in settings['ignored_channels'].split(',')]
                embed.add_field(name="Ignored Channels", value="\n".join(channels))
            
            if settings['ignored_roles']:
                roles = [f"<@&{r}>" for r in settings['ignored_roles'].split(',')]
                embed.add_field(name="Ignored Roles", value="\n".join(roles))
            
            if settings['double_xp_roles']:
                roles = [f"<@&{r}>" for r in settings['double_xp_roles'].split(',')]
                embed.add_field(name="Double XP Roles", value="\n".join(roles))
            
            await ctx.send(embed=embed)

    @levelset.command(name="toggle")
    async def toggle_leveling(self, ctx, enabled: bool):
        """Toggle leveling system"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE leveling_settings
                SET enabled = ?
                WHERE guild_id = ?
            """, (enabled, str(ctx.guild.id)))
            conn.commit()
            
            status = "enabled" if enabled else "disabled"
            await ctx.send(f"✅ Leveling system {status}!")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to toggle leveling: {e}")
            await ctx.send("❌ An error occurred while updating settings")
        finally:
            if conn:
                conn.close()

    @levelset.command(name="channel")
    async def set_announcement_channel(self, ctx, channel: discord.TextChannel = None):
        """Set level up announcement channel"""
        channel_id = str(channel.id) if channel else None
        
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE leveling_settings
                SET announcement_channel = ?
                WHERE guild_id = ?
            """, (channel_id, str(ctx.guild.id)))
            conn.commit()
            
            if channel:
                await ctx.send(f"✅ Level up announcements will be sent to {channel.mention}")
            else:
                await ctx.send("✅ Level up announcements disabled")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to set announcement channel: {e}")
            await ctx.send("❌ An error occurred while updating settings")
        finally:
            if conn:
                conn.close()

    @levelset.command(name="xprange")
    async def set_xp_range(self, ctx, min_xp: int, max_xp: int):
        """Set XP gain range"""
        if min_xp < 1 or max_xp < min_xp:
            return await ctx.send("❌ Invalid XP range!")
        
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE leveling_settings
                SET min_xp = ?, max_xp = ?
                WHERE guild_id = ?
            """, (min_xp, max_xp, str(ctx.guild.id)))
            conn.commit()
            
            await ctx.send(f"✅ XP gain range set to {min_xp}-{max_xp}")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to set XP range: {e}")
            await ctx.send("❌ An error occurred while updating settings")
        finally:
            if conn:
                conn.close()

    @levelset.command(name="cooldown")
    async def set_cooldown(self, ctx, seconds: int):
        """Set XP gain cooldown"""
        if seconds < 0:
            return await ctx.send("❌ Cooldown cannot be negative!")
        
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE leveling_settings
                SET cooldown = ?
                WHERE guild_id = ?
            """, (seconds, str(ctx.guild.id)))
            conn.commit()
            
            await ctx.send(f"✅ XP gain cooldown set to {seconds} seconds")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to set cooldown: {e}")
            await ctx.send("❌ An error occurred while updating settings")
        finally:
            if conn:
                conn.close()

    @levelset.command(name="stackrewards")
    async def toggle_stack_rewards(self, ctx, enabled: bool):
        """Toggle stacking of level rewards"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE leveling_settings
                SET stack_rewards = ?
                WHERE guild_id = ?
            """, (enabled, str(ctx.guild.id)))
            conn.commit()
            
            status = "will now stack" if enabled else "will no longer stack"
            await ctx.send(f"✅ Level rewards {status}")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to toggle stack rewards: {e}")
            await ctx.send("❌ An error occurred while updating settings")
        finally:
            if conn:
                conn.close()

    @levelset.command(name="addreward")
    async def add_level_reward(self, ctx, level: int, role: discord.Role):
        """Add a level reward role"""
        if level < 1:
            return await ctx.send("❌ Level must be greater than 0!")
        
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO level_rewards
                (guild_id, level, role_id)
                VALUES (?, ?, ?)
            """, (str(ctx.guild.id), level, str(role.id)))
            conn.commit()
            
            await ctx.send(f"✅ {role.mention} will be awarded at level {level}")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to add level reward: {e}")
            await ctx.send("❌ An error occurred while adding reward")
        finally:
            if conn:
                conn.close()

    @levelset.command(name="removereward")
    async def remove_level_reward(self, ctx, level: int):
        """Remove a level reward"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                DELETE FROM level_rewards
                WHERE guild_id = ? AND level = ?
            """, (str(ctx.guild.id), level))
            conn.commit()
            
            if cursor.rowcount > 0:
                await ctx.send(f"✅ Removed reward for level {level}")
            else:
                await ctx.send("❌ No reward found for that level")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to remove level reward: {e}")
            await ctx.send("❌ An error occurred while removing reward")
        finally:
            if conn:
                conn.close()

    @levelset.command(name="rewards")
    async def list_rewards(self, ctx):
        """List all level rewards"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT level, role_id
                FROM level_rewards
                WHERE guild_id = ?
                ORDER BY level ASC
            """, (str(ctx.guild.id),))
            rewards = cursor.fetchall()
            
            if not rewards:
                return await ctx.send("❌ No level rewards set!")
            
            embed = Embed.create(
                title="🎁 Level Rewards",
                color=discord.Color.blue()
            )
            
            for reward in rewards:
                role = ctx.guild.get_role(int(reward['role_id']))
                if role:
                    embed.add_field(
                        name=f"Level {reward['level']}",
                        value=role.mention,
                        inline=False
                    )
            
            await ctx.send(embed=embed)
            
        except sqlite3.Error as e:
            logger.error(f"Failed to list rewards: {e}")
            await ctx.send("❌ An error occurred while getting rewards")
        finally:
            if conn:
                conn.close()

    @levelset.command(name="ignorechannel")
    async def toggle_ignore_channel(self, ctx, channel: discord.TextChannel):
        """Toggle XP gain in a channel"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT ignored_channels FROM leveling_settings
                WHERE guild_id = ?
            """, (str(ctx.guild.id),))
            data = cursor.fetchone()
            
            ignored = set(data['ignored_channels'].split(',') if data['ignored_channels'] else [])
            channel_id = str(channel.id)
            
            if channel_id in ignored:
                ignored.remove(channel_id)
                action = "enabled"
            else:
                ignored.add(channel_id)
                action = "disabled"
            
            cursor.execute("""
                UPDATE leveling_settings
                SET ignored_channels = ?
                WHERE guild_id = ?
            """, (','.join(ignored) if ignored else None, str(ctx.guild.id)))
            conn.commit()
            
            await ctx.send(f"✅ XP gain {action} in {channel.mention}")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to toggle ignored channel: {e}")
            await ctx.send("❌ An error occurred while updating settings")
        finally:
            if conn:
                conn.close()

    @levelset.command(name="ignorerole")
    async def toggle_ignore_role(self, ctx, role: discord.Role):
        """Toggle XP gain for a role"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT ignored_roles FROM leveling_settings
                WHERE guild_id = ?
            """, (str(ctx.guild.id),))
            data = cursor.fetchone()
            
            ignored = set(data['ignored_roles'].split(',') if data['ignored_roles'] else [])
            role_id = str(role.id)
            
            if role_id in ignored:
                ignored.remove(role_id)
                action = "enabled"
            else:
                ignored.add(role_id)
                action = "disabled"
            
            cursor.execute("""
                UPDATE leveling_settings
                SET ignored_roles = ?
                WHERE guild_id = ?
            """, (','.join(ignored) if ignored else None, str(ctx.guild.id)))
            conn.commit()
            
            await ctx.send(f"✅ XP gain {action} for {role.mention}")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to toggle ignored role: {e}")
            await ctx.send("❌ An error occurred while updating settings")
        finally:
            if conn:
                conn.close()

    @levelset.command(name="doublexp")
    async def toggle_double_xp_role(self, ctx, role: discord.Role):
        """Toggle double XP for a role"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT double_xp_roles FROM leveling_settings
                WHERE guild_id = ?
            """, (str(ctx.guild.id),))
            data = cursor.fetchone()
            
            double_xp = set(data['double_xp_roles'].split(',') if data['double_xp_roles'] else [])
            role_id = str(role.id)
            
            if role_id in double_xp:
                double_xp.remove(role_id)
                action = "disabled"
            else:
                double_xp.add(role_id)
                action = "enabled"
            
            cursor.execute("""
                UPDATE leveling_settings
                SET double_xp_roles = ?
                WHERE guild_id = ?
            """, (','.join(double_xp) if double_xp else None, str(ctx.guild.id)))
            conn.commit()
            
            await ctx.send(f"✅ Double XP {action} for {role.mention}")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to toggle double XP role: {e}")
            await ctx.send("❌ An error occurred while updating settings")
        finally:
            if conn:
                conn.close()

async def setup(bot):
    """Setup the Leveling cog"""
    cog = Leveling(bot)
    cog.setup_tables()
    await bot.add_cog(cog)