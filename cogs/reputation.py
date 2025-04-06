import discord
from discord.ext import commands
import asyncio
from asyncio import Lock
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from .utils import Embed, event_dispatcher
from database import get_connection
import logging

logger = logging.getLogger(__name__)

class Reputation(commands.Cog):
    """⭐ Advanced Reputation System"""
    
    def __init__(self, bot):
        self.bot = bot
        self.cooldowns = {}
        # Initialize locks
        self.db_lock = Lock()  # For database operations
        self.cooldown_lock = Lock()  # For cooldown management
        self.role_lock = Lock()  # For role updates
        self.response_lock = Lock()  # For preventing multiple responses
        self.setup_tables()
        self.register_handlers()

    async def acquire_lock(self, lock: Lock, timeout: float = 10.0) -> bool:
        """Helper method to acquire a lock with timeout"""
        try:
            await asyncio.wait_for(lock.acquire(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            logger.error(f"Failed to acquire lock within {timeout} seconds")
            return False
        except Exception as e:
            logger.error(f"Error acquiring lock: {e}")
            return False

    async def send_response_once(self, ctx, message, *, embed=None):
        """Helper method to ensure only one response is sent"""
        if not await self.acquire_lock(self.response_lock, timeout=5.0):
            return
        
        try:
            if embed:
                await ctx.send(embed=embed)
            else:
                await ctx.send(message)
        finally:
            self.response_lock.release()

    def setup_tables(self):
        """Setup necessary database tables"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Reputation settings
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reputation_settings (
                    guild_id TEXT PRIMARY KEY,
                    cooldown INTEGER DEFAULT 43200,
                    max_daily INTEGER DEFAULT 3,
                    min_message_age INTEGER DEFAULT 1800,
                    required_role TEXT,
                    blacklisted_roles TEXT,
                    log_channel TEXT,
                    auto_roles TEXT,
                    stack_roles BOOLEAN DEFAULT FALSE,
                    decay_enabled BOOLEAN DEFAULT FALSE,
                    decay_days INTEGER DEFAULT 30
                )
            """)
            
            # User reputation
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_reputation (
                    user_id TEXT,
                    guild_id TEXT,
                    reputation INTEGER DEFAULT 0,
                    total_given INTEGER DEFAULT 0,
                    total_received INTEGER DEFAULT 0,
                    last_given DATETIME,
                    last_received DATETIME,
                    PRIMARY KEY (user_id, guild_id)
                )
            """)
            
            # Reputation history
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reputation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    giver_id TEXT NOT NULL,
                    receiver_id TEXT NOT NULL,
                    message_id TEXT,
                    reason TEXT,
                    amount INTEGER DEFAULT 1,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Reputation roles
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reputation_roles (
                    guild_id TEXT,
                    reputation INTEGER,
                    role_id TEXT,
                    PRIMARY KEY (guild_id, reputation)
                )
            """)
            
            conn.commit()
            logger.info("Reputation tables created successfully")
            
        except sqlite3.Error as e:
            logger.error(f"Failed to setup reputation tables: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()

    def register_handlers(self):
        """Register event handlers"""
        event_dispatcher.register('rep_give', self.log_reputation)
        event_dispatcher.register('rep_remove', self.log_reputation)
        event_dispatcher.register('rep_reset', self.log_reputation)

    async def get_settings(self, guild_id: int) -> Dict:
        """Get reputation settings for a guild"""
        async with self.db_lock:
            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT * FROM reputation_settings WHERE guild_id = ?
                """, (str(guild_id),))
                data = cursor.fetchone()
                
                if not data:
                    default_settings = {
                        'cooldown': 43200,  # 12 hours in seconds
                        'max_daily': 3,
                        'min_message_age': 1800,  # 30 minutes in seconds
                        'required_role': None,
                        'blacklisted_roles': '',
                        'log_channel': None,
                        'auto_roles': '',
                        'stack_roles': False,
                        'decay_enabled': False,
                        'decay_days': 30
                    }
                    
                    cursor.execute("""
                        INSERT INTO reputation_settings
                        (guild_id, cooldown, max_daily)
                        VALUES (?, ?, ?)
                    """, (str(guild_id), 43200, 3))
                    conn.commit()
                    return default_settings
                    
                return dict(data)
                
            except sqlite3.Error as e:
                logger.error(f"Failed to get reputation settings: {e}")
                raise
            finally:
                if conn:
                    conn.close()

    async def check_reputation_roles(self, member: discord.Member, reputation: int):
        """Check and update reputation roles"""
        if not await self.acquire_lock(self.role_lock):
            return

        try:
            settings = await self.get_settings(member.guild.id)
            
            async with self.db_lock:
                conn = None
                try:
                    conn = get_connection()
                    cursor = conn.cursor()
                    
                    cursor.execute("""
                        SELECT role_id, reputation FROM reputation_roles
                        WHERE guild_id = ? AND reputation <= ?
                        ORDER BY reputation DESC
                    """, (str(member.guild.id), reputation))
                    role_data = cursor.fetchall()
                    
                    if not role_data:
                        return
                        
                    try:
                        if settings['stack_roles']:
                            # Add all roles up to current reputation
                            for data in role_data:
                                role = member.guild.get_role(int(data['role_id']))
                                if role and role not in member.roles:
                                    await member.add_roles(role)
                        else:
                            # Only add highest role
                            highest_role = member.guild.get_role(int(role_data[0]['role_id']))
                            if highest_role:
                                # Remove other reputation roles
                                for data in role_data[1:]:
                                    role = member.guild.get_role(int(data['role_id']))
                                    if role and role in member.roles:
                                        await member.remove_roles(role)
                                # Add highest role
                                if highest_role not in member.roles:
                                    await member.add_roles(highest_role)
                    except discord.Forbidden:
                        logger.error(f"Failed to update roles for {member.id}: Missing permissions")
                        
                except sqlite3.Error as e:
                    logger.error(f"Failed to check reputation roles: {e}")
                finally:
                    if conn:
                        conn.close()
        finally:
            self.role_lock.release()

    async def log_reputation(self, guild: discord.Guild, giver: discord.Member, 
                           receiver: discord.Member, action: str, amount: int, 
                           reason: str = None):
        """Log reputation changes"""
        async with self.db_lock:
            settings = await self.get_settings(guild.id)
            if not settings['log_channel']:
                return
                
            channel = guild.get_channel(int(settings['log_channel']))
            if not channel:
                return
                
            embed = Embed.create(
                title="⭐ Reputation Update",
                color=discord.Color.gold(),
                field_Action=action,
                field_From=f"{giver} ({giver.id})",
                field_To=f"{receiver} ({receiver.id})",
                field_Amount=str(amount)
            )
            
            if reason:
                embed.add_field(name="Reason", value=reason)
                
            await channel.send(embed=embed)

    @commands.group(name="rep")
    async def rep(self, ctx):
        """⭐ Reputation commands"""
        if ctx.invoked_subcommand is None:
            await self.send_response_once(ctx, await ctx.send_help(ctx.command))

    @rep.command(name="give", aliases=["+"])
    async def give_rep(self, ctx, member: discord.Member, *, reason: str = None):
        """Give reputation to a member"""
        if member == ctx.author:
            return await self.send_response_once(ctx, "❌ You can't give reputation to yourself!")
            
        if member.bot:
            return await self.send_response_once(ctx, "❌ You can't give reputation to bots!")

        if not await self.acquire_lock(self.cooldown_lock):
            return await self.send_response_once(ctx, "❌ System is busy, please try again later")
            
        try:
            async with self.db_lock:
                settings = await self.get_settings(ctx.guild.id)
                
                # Check required role
                if settings['required_role']:
                    required_role = ctx.guild.get_role(int(settings['required_role']))
                    if required_role and required_role not in ctx.author.roles:
                        return await self.send_response_once(
                            ctx,
                            f"❌ You need the {required_role.mention} role to give reputation!"
                        )
                
                # Check blacklisted roles
                if settings['blacklisted_roles']:
                    blacklisted = settings['blacklisted_roles'].split(',')
                    for role_id in blacklisted:
                        role = ctx.guild.get_role(int(role_id))
                        if role and role in member.roles:
                            return await self.send_response_once(
                                ctx,
                                f"❌ Members with {role.mention} can't receive reputation!"
                            )
                
                # Check cooldown
                cooldown_key = f"{ctx.guild.id}-{ctx.author.id}"
                if cooldown_key in self.cooldowns:
                    remaining = self.cooldowns[cooldown_key] - datetime.utcnow()
                    if remaining.total_seconds() > 0:
                        return await self.send_response_once(
                            ctx,
                            f"❌ You must wait {int(remaining.total_seconds() // 60)} minutes before giving reputation again!"
                        )
                
                conn = None
                try:
                    conn = get_connection()
                    cursor = conn.cursor()
                    
                    # Check daily limit
                    cursor.execute("""
                        SELECT COUNT(*) as count FROM reputation_history
                        WHERE guild_id = ? AND giver_id = ? 
                        AND timestamp > datetime('now', '-1 day')
                    """, (str(ctx.guild.id), str(ctx.author.id)))
                    data = cursor.fetchone()
                    
                    if data['count'] >= settings['max_daily']:
                        return await self.send_response_once(ctx, "❌ You've reached your daily reputation limit!")
                    
                    # Update reputation
                    cursor.execute("""
                        INSERT INTO user_reputation (user_id, guild_id, reputation, total_received)
                        VALUES (?, ?, 1, 1)
                        ON CONFLICT(user_id, guild_id) DO UPDATE SET
                        reputation = reputation + 1,
                        total_received = total_received + 1,
                        last_received = CURRENT_TIMESTAMP
                    """, (str(member.id), str(ctx.guild.id)))
                    
                    # Update giver stats
                    cursor.execute("""
                        INSERT INTO user_reputation (user_id, guild_id, total_given)
                        VALUES (?, ?, 1)
                        ON CONFLICT(user_id, guild_id) DO UPDATE SET
                        total_given = total_given + 1,
                        last_given = CURRENT_TIMESTAMP
                    """, (str(ctx.author.id), str(ctx.guild.id)))
                    
                    # Record history
                    cursor.execute("""
                        INSERT INTO reputation_history
                        (guild_id, giver_id, receiver_id, message_id, reason, amount)
                        VALUES (?, ?, ?, ?, ?, 1)
                    """, (
                        str(ctx.guild.id),
                        str(ctx.author.id),
                        str(member.id),
                        str(ctx.message.id),
                        reason
                    ))
                    
                    conn.commit()
                    
                    # Set cooldown
                    self.cooldowns[cooldown_key] = datetime.utcnow() + timedelta(seconds=settings['cooldown'])
                    
                    # Get new reputation
                    cursor.execute("""
                        SELECT reputation FROM user_reputation
                        WHERE user_id = ? AND guild_id = ?
                    """, (str(member.id), str(ctx.guild.id)))
                    data = cursor.fetchone()
                    new_rep = data['reputation']
                    
                    await self.check_reputation_roles(member, new_rep)
                    await self.log_reputation(ctx.guild, ctx.author, member, "Give", 1, reason)
                    await self.send_response_once(
                        ctx,
                        f"✅ Gave reputation to {member.mention}! Their new reputation is {new_rep} ⭐"
                    )
                    
                except sqlite3.Error as e:
                    logger.error(f"Failed to give reputation: {e}")
                    await self.send_response_once(ctx, "❌ An error occurred while giving reputation")
                    if conn:
                        conn.rollback()
                finally:
                    if conn:
                        conn.close()
        finally:
            self.cooldown_lock.release()

    @rep.command(name="remove", aliases=["-"])
    @commands.has_permissions(manage_guild=True)
    async def remove_rep(self, ctx, member: discord.Member, amount: int = 1, *, reason: str = None):
        """Remove reputation from a member"""
        if amount < 1:
            return await self.send_response_once(ctx, "❌ Amount must be positive!")
            
        async with self.db_lock:
            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                
                cursor.execute("""
                    UPDATE user_reputation
                    SET reputation = MAX(0, reputation - ?)
                    WHERE user_id = ? AND guild_id = ?
                """, (amount, str(member.id), str(ctx.guild.id)))
                
                # Record history
                cursor.execute("""
                    INSERT INTO reputation_history
                    (guild_id, giver_id, receiver_id, reason, amount)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    str(ctx.guild.id),
                    str(ctx.author.id),
                    str(member.id),
                    reason,
                    -amount
                ))
                
                conn.commit()
                
                # Get new reputation
                cursor.execute("""
                    SELECT reputation FROM user_reputation
                    WHERE user_id = ? AND guild_id = ?
                """, (str(member.id), str(ctx.guild.id)))
                data = cursor.fetchone()
                new_rep = data['reputation'] if data else 0
                
                await self.check_reputation_roles(member, new_rep)
                await self.log_reputation(ctx.guild, ctx.author, member, "Remove", amount, reason)
                await self.send_response_once(
                    ctx,
                    f"✅ Removed {amount} reputation from {member.mention}! Their new reputation is {new_rep} ⭐"
                )
                
            except sqlite3.Error as e:
                logger.error(f"Failed to remove reputation: {e}")
                await self.send_response_once(ctx, "❌ An error occurred while removing reputation")
                if conn:
                    conn.rollback()
            finally:
                if conn:
                    conn.close()

    @rep.command(name="check")
    async def check_rep(self, ctx, member: discord.Member = None):
        """Check your or someone else's reputation"""
        member = member or ctx.author
        
        async with self.db_lock:
            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT * FROM user_reputation
                    WHERE user_id = ? AND guild_id = ?
                """, (str(member.id), str(ctx.guild.id)))
                data = cursor.fetchone()
                
                if not data:
                    return await self.send_response_once(ctx, "❌ This user has no reputation yet!")
                    
                # Get rank
                cursor.execute("""
                    SELECT COUNT(*) as rank
                    FROM user_reputation
                    WHERE guild_id = ? AND reputation > ?
                """, (str(ctx.guild.id), data['reputation']))
                rank_data = cursor.fetchone()
                
                rank = rank_data['rank'] + 1
                
                embed = Embed.create(
                    title=f"⭐ Reputation - {member.display_name}",
                    color=member.color,
                    field_Reputation=str(data['reputation']),
                    field_Rank=f"#{rank}",
                    field_Given=str(data['total_given']),
                    field_Received=str(data['total_received'])
                )
                
                if data['last_received']:
                    embed.add_field(
                        name="Last Received",
                        value=f"<t:{int(datetime.strptime(data['last_received'], '%Y-%m-%d %H:%M:%S').timestamp())}:R>"
                    )
                    
                await self.send_response_once(ctx, embed=embed)
                
            except sqlite3.Error as e:
                logger.error(f"Failed to check reputation: {e}")
                await self.send_response_once(ctx, "❌ An error occurred while checking reputation")
            finally:
                if conn:
                    conn.close()

    @rep.command(name="top")
    async def top_rep(self, ctx):
        """Show reputation leaderboard"""
        async with self.db_lock:
            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT user_id, reputation
                    FROM user_reputation
                    WHERE guild_id = ?
                    ORDER BY reputation DESC
                    LIMIT 10
                """, (str(ctx.guild.id),))
                top_users = cursor.fetchall()
                
                if not top_users:
                    return await self.send_response_once(ctx, "❌ No one has any reputation yet!")
                    
                embed = Embed.create(
                    title=f"⭐ {ctx.guild.name}'s Top Members",
                    color=discord.Color.gold()
                )
                
                for idx, user_data in enumerate(top_users, 1):
                    member = ctx.guild.get_member(int(user_data['user_id']))
                    if member:
                        embed.add_field(
                            name=f"#{idx} {member.display_name}",
                            value=f"{user_data['reputation']} ⭐",
                            inline=False
                        )
                        
                await self.send_response_once(ctx, embed=embed)
                
            except sqlite3.Error as e:
                logger.error(f"Failed to get reputation leaderboard: {e}")
                await self.send_response_once(ctx, "❌ An error occurred while getting the leaderboard")
            finally:
                if conn:
                    conn.close()

    @rep.command(name="history")
    async def rep_history(self, ctx, member: discord.Member = None):
        """View reputation history"""
        member = member or ctx.author
        
        async with self.db_lock:
            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT * FROM reputation_history
                    WHERE (giver_id = ? OR receiver_id = ?) AND guild_id = ?
                    ORDER BY timestamp DESC LIMIT 10
                """, (str(member.id), str(member.id), str(ctx.guild.id)))
                history = cursor.fetchall()
                
                if not history:
                    return await self.send_response_once(ctx, "❌ No reputation history found!")
                    
                embed = Embed.create(
                    title=f"⭐ Reputation History - {member.display_name}",
                    color=member.color
                )
                
                for entry in history:
                    giver = ctx.guild.get_member(int(entry['giver_id']))
                    receiver = ctx.guild.get_member(int(entry['receiver_id']))
                    
                    if giver and receiver:
                        timestamp = datetime.strptime(entry['timestamp'], '%Y-%m-%d %H:%M:%S')
                        action = "Received" if entry['receiver_id'] == str(member.id) else "Gave"
                        target = giver if action == "Received" else receiver
                        
                        embed.add_field(
                            name=f"{action} {abs(entry['amount'])} ⭐ {discord.utils.format_dt(timestamp, 'R')}",
                            value=f"{'From' if action == 'Received' else 'To'}: {target.mention}\n"
                                  f"Reason: {entry['reason'] or 'No reason provided'}",
                            inline=False
                        )
                        
                await self.send_response_once(ctx, embed=embed)
                
            except sqlite3.Error as e:
                logger.error(f"Failed to get reputation history: {e}")
                await self.send_response_once(ctx, "❌ An error occurred while getting the history")
            finally:
                if conn:
                    conn.close()

    @commands.group(name="repset")
    @commands.has_permissions(manage_guild=True)
    async def repset(self, ctx):
        """⚙️ Reputation system settings"""
        if ctx.invoked_subcommand is None:
            settings = await self.get_settings(ctx.guild.id)
            
            embed = Embed.create(
                title="⚙️ Reputation Settings",
                color=discord.Color.blue(),
                field_Cooldown=f"{settings['cooldown'] // 3600} hours",
                field_Daily_Limit=str(settings['max_daily']),
                field_Min_Message_Age=f"{settings['min_message_age'] // 60} minutes",
                field_Required_Role=f"<@&{settings['required_role']}>" if settings['required_role'] else "None",
                field_Log_Channel=f"<#{settings['log_channel']}>" if settings['log_channel'] else "None",
                field_Stack_Roles=str(settings['stack_roles']),
                field_Decay_Enabled=str(settings['decay_enabled']),
                field_Decay_Days=str(settings['decay_days'])
            )
            
            await self.send_response_once(ctx, embed=embed)

    @repset.command(name="cooldown")
    async def set_cooldown(self, ctx, hours: int):
        """Set reputation cooldown in hours"""
        if hours < 1:
            return await self.send_response_once(ctx, "❌ Cooldown must be at least 1 hour!")
            
        async with self.db_lock:
            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                
                cursor.execute("""
                    UPDATE reputation_settings
                    SET cooldown = ?
                    WHERE guild_id = ?
                """, (hours * 3600, str(ctx.guild.id)))
                conn.commit()
                
                await self.send_response_once(ctx, f"✅ Reputation cooldown set to {hours} hours")
                
            except sqlite3.Error as e:
                logger.error(f"Failed to set cooldown: {e}")
                await self.send_response_once(ctx, "❌ An error occurred while setting the cooldown")
                if conn:
                    conn.rollback()
            finally:
                if conn:
                    conn.close()

    @repset.command(name="maxdaily")
    async def set_max_daily(self, ctx, amount: int):
        """Set maximum daily reputation gives"""
        if amount < 1:
            return await self.send_response_once(ctx, "❌ Amount must be positive!")
            
        async with self.db_lock:
            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                
                cursor.execute("""
                    UPDATE reputation_settings
                    SET max_daily = ?
                    WHERE guild_id = ?
                """, (amount, str(ctx.guild.id)))
                conn.commit()
                
                await self.send_response_once(ctx, f"✅ Maximum daily reputation gives set to {amount}")
                
            except sqlite3.Error as e:
                logger.error(f"Failed to set max daily: {e}")
                await self.send_response_once(ctx, "❌ An error occurred while setting the daily limit")
                if conn:
                    conn.rollback()
            finally:
                if conn:
                    conn.close()

    @repset.command(name="addrole")
    async def add_rep_role(self, ctx, role: discord.Role, required_rep: int):
        """Add a reputation role reward"""
        if required_rep < 0:
            return await self.send_response_once(ctx, "❌ Required reputation must be positive!")
            
        async with self.db_lock:
            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                
                cursor.execute("""
                    INSERT OR REPLACE INTO reputation_roles
                    (guild_id, reputation, role_id)
                    VALUES (?, ?, ?)
                """, (str(ctx.guild.id), required_rep, str(role.id)))
                conn.commit()
                
                await self.send_response_once(ctx, f"✅ {role.mention} will be given at {required_rep} reputation")
                
            except sqlite3.Error as e:
                logger.error(f"Failed to add reputation role: {e}")
                await self.send_response_once(ctx, "❌ An error occurred while adding the role")
                if conn:
                    conn.rollback()
            finally:
                if conn:
                    conn.close()

    @repset.command(name="removerole")
    async def remove_rep_role(self, ctx, role: discord.Role):
        """Remove a reputation role reward"""
        async with self.db_lock:
            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                
                cursor.execute("""
                    DELETE FROM reputation_roles
                    WHERE guild_id = ? AND role_id = ?
                """, (str(ctx.guild.id), str(role.id)))
                conn.commit()
                
                await self.send_response_once(ctx, f"✅ Removed {role.mention} from reputation rewards")
                
            except sqlite3.Error as e:
                logger.error(f"Failed to remove reputation role: {e}")
                await self.send_response_once(ctx, "❌ An error occurred while removing the role")
                if conn:
                    conn.rollback()
            finally:
                if conn:
                    conn.close()

    @repset.command(name="stackroles")
    async def toggle_stack_roles(self, ctx):
        """Toggle stacking of reputation roles"""
        async with self.db_lock:
            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                
                cursor.execute("""
                    UPDATE reputation_settings
                    SET stack_roles = NOT stack_roles
                    WHERE guild_id = ?
                """, (str(ctx.guild.id),))
                conn.commit()
                
                cursor.execute("""
                    SELECT stack_roles FROM reputation_settings
                    WHERE guild_id = ?
                """, (str(ctx.guild.id),))
                data = cursor.fetchone()
                
                enabled = data['stack_roles']
                await self.send_response_once(ctx, f"✅ Role stacking {'enabled' if enabled else 'disabled'}")
                
            except sqlite3.Error as e:
                logger.error(f"Failed to toggle role stacking: {e}")
                await self.send_response_once(ctx, "❌ An error occurred while toggling role stacking")
                if conn:
                    conn.rollback()
            finally:
                if conn:
                    conn.close()

async def setup(bot):
    """Setup the Reputation cog"""
    cog = Reputation(bot)
    cog.setup_tables()  # Not async anymore since using sqlite3
    await bot.add_cog(cog)