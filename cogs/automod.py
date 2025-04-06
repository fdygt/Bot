import discord
from discord.ext import commands
from datetime import datetime, timedelta
import json
import asyncio
from .utils import Embed, Permissions, event_dispatcher
from database import get_connection
import sqlite3
from asyncio import Lock
import os
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class AutoMod(commands.Cog):
    """🛡️ Sistem Moderasi Otomatis"""
    
    def __init__(self, bot):
        self.bot = bot
        self.spam_check = {}
        # Pastikan direktori config ada
        Path('config').mkdir(exist_ok=True)
        self.config = self.load_config()
        self.register_handlers()
        self.locks = {}
        self.spam_locks = {}
        self.mute_locks = {}
        self.config_lock = Lock()
        # Cache untuk banned words
        self._banned_words_cache = set(word.lower() for word in self.config["banned_words"]["words"])
        # Task untuk cleanup
        self.cleanup_task = self.bot.loop.create_task(self.periodic_cleanup())
        # Setup database
        self.setup_database()

    def setup_database(self):
        """Setup database tables for automod"""
        conn = get_connection()
        try:
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS automod_warnings
                        (id INTEGER PRIMARY KEY AUTOINCREMENT,
                         user_id TEXT,
                         guild_id TEXT,
                         warning_type TEXT,
                         reason TEXT,
                         timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
            conn.commit()
        except Exception as e:
            logger.error(f"Error setting up database: {e}")
        finally:
            conn.close()

    def register_handlers(self):
        """Register event handlers with dispatcher"""
        event_dispatcher.register('message', self.handle_message, priority=1)
        event_dispatcher.register('automod_violation', self.handle_violation, priority=1)

    async def periodic_cleanup(self):
        """Periodic cleanup of old data"""
        while not self.bot.is_closed():
            try:
                # Cleanup spam checks older than 1 hour
                current_time = datetime.utcnow()
                for user_id in list(self.spam_check.keys()):
                    self.spam_check[user_id] = [
                        msg_time for msg_time in self.spam_check[user_id]
                        if current_time - msg_time < timedelta(hours=1)
                    ]
                    if not self.spam_check[user_id]:
                        del self.spam_check[user_id]
                
                # Cleanup old locks
                for dict_locks in [self.locks, self.spam_locks, self.mute_locks]:
                    for key in list(dict_locks.keys()):
                        if not dict_locks[key].locked():
                            del dict_locks[key]
                
                # Cleanup old warnings from database
                conn = get_connection()
                try:
                    c = conn.cursor()
                    c.execute('''DELETE FROM automod_warnings 
                               WHERE timestamp < datetime('now', '-30 days')''')
                    conn.commit()
                finally:
                    conn.close()
                    
            except Exception as e:
                logger.error(f"Error in cleanup: {e}")
            
            await asyncio.sleep(3600)  # Run every hour

    def cog_unload(self):
        """Cleanup when cog is unloaded"""
        self.cleanup_task.cancel()

    async def get_user_lock(self, user_id: int) -> Lock:
        """Get a lock for a specific user"""
        if user_id not in self.locks:
            self.locks[user_id] = Lock()
        return self.locks[user_id]

    async def get_spam_lock(self, user_id: int) -> Lock:
        """Get a spam check lock for a specific user"""
        if user_id not in self.spam_locks:
            self.spam_locks[user_id] = Lock()
        return self.spam_locks[user_id]

    async def get_mute_lock(self, guild_id: int) -> Lock:
        """Get a mute lock for a specific guild"""
        if guild_id not in self.mute_locks:
            self.mute_locks[guild_id] = Lock()
        return self.mute_locks[guild_id]

    def load_config(self, force_default: bool = False) -> dict:
        """Load automod configuration"""
        default = {
            "enabled": True,
            "spam": {
                "enabled": True,
                "threshold": 5,
                "timeframe": 5
            },
            "caps": {
                "enabled": True,
                "threshold": 0.7,
                "min_length": 10
            },
            "banned_words": {
                "enabled": True,
                "words": [],
                "wildcards": []
            },
            "punishments": {
                "warn_threshold": 3,
                "mute_duration": 10
            }
        }

        if force_default:
            with open('config/automod.json', 'w') as f:
                json.dump(default, f, indent=4)
            return default

        try:
            with open('config/automod.json', 'r') as f:
                config = json.load(f)
                # Validate and update missing keys
                self._validate_config(config, default)
                return config
        except (FileNotFoundError, json.JSONDecodeError):
            # Write default config
            with open('config/automod.json', 'w') as f:
                json.dump(default, f, indent=4)
            return default

    def _validate_config(self, config: dict, default: dict) -> None:
        """Recursively validate and update config with missing default values"""
        for key, default_value in default.items():
            if key not in config:
                config[key] = default_value
            elif isinstance(default_value, dict):
                if not isinstance(config[key], dict):
                    config[key] = default_value
                else:
                    self._validate_config(config[key], default_value)

    async def save_config(self, config: dict = None):
        """Save automod configuration"""
        async with self.config_lock:
            if config is None:
                config = self.config
            with open('config/automod.json', 'w') as f:
                json.dump(config, f, indent=4)
            # Update cache
            self._banned_words_cache = set(word.lower() for word in config["banned_words"]["words"])

    async def handle_message(self, message: discord.Message):
        """Main message handler for automod"""
        if not self.config["enabled"] or message.author.bot:
            return

        if not isinstance(message.channel, discord.TextChannel):
            return

        async with await self.get_user_lock(message.author.id):
            violations = []

            # Check for spam
            if self.config["spam"]["enabled"]:
                if await self.check_spam(message):
                    violations.append(("spam", "Sending messages too quickly"))

            # Check for excessive caps
            if self.config["caps"]["enabled"]:
                if await self.check_caps(message):
                    violations.append(("caps", "Excessive use of caps"))

            # Check for banned words
            if self.config["banned_words"]["enabled"]:
                if word := await self.check_banned_words(message):
                    violations.append(("banned_word", f"Used banned word: {word}"))

            # Handle any violations
            for violation_type, reason in violations:
                await event_dispatcher.dispatch('automod_violation', message, violation_type, reason)

    async def check_spam(self, message: discord.Message) -> bool:
        """Check for spam messages"""
        author_id = str(message.author.id)
        current_time = datetime.utcnow()
        threshold = self.config["spam"]["threshold"]
        timeframe = self.config["spam"]["timeframe"]

        async with await self.get_spam_lock(message.author.id):
            if author_id not in self.spam_check:
                self.spam_check[author_id] = []

            # Remove old messages
            self.spam_check[author_id] = [
                msg_time for msg_time in self.spam_check[author_id]
                if current_time - msg_time < timedelta(seconds=timeframe)
            ]

            # Add new message
            self.spam_check[author_id].append(current_time)

            # Check if threshold is exceeded
            return len(self.spam_check[author_id]) >= threshold

    async def check_caps(self, message: discord.Message) -> bool:
        """Check for excessive caps use"""
        if len(message.content) < self.config["caps"]["min_length"]:
            return False
            
        caps_count = sum(1 for c in message.content if c.isupper())
        caps_ratio = caps_count / len(message.content)
        
        return caps_ratio > self.config["caps"]["threshold"]

    async def check_banned_words(self, message: discord.Message) -> str:
        """Check for banned words using cached set"""
        content_lower = message.content.lower()
        
        # Check exact matches from cache
        for word in self._banned_words_cache:
            if word in content_lower:
                return word
                
        # Check wildcards
        for pattern in self.config["banned_words"]["wildcards"]:
            if pattern.lower() in content_lower:
                return pattern
                
        return ""

    async def handle_violation(self, message: discord.Message, violation_type: str, reason: str):
        """Handle automod violations"""
        try:
            async with await self.get_user_lock(message.author.id):
                # Create warning embed
                embed = Embed.create(
                    title="⚠️ AutoMod Warning",
                    description=f"Violation detected in {message.channel.mention}",
                    color=discord.Color.orange(),
                    field_User=message.author.mention,
                    field_Type=violation_type.title(),
                    field_Reason=reason
                )

                # Delete violating message
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    pass

                # Send warning
                try:
                    warning_msg = await message.channel.send(embed=embed)
                    await warning_msg.delete(delay=5)
                except discord.Forbidden:
                    pass

                # Log warning to database
                conn = get_connection()
                try:
                    cursor = conn.cursor()
                    
                    cursor.execute("""
                        INSERT INTO automod_warnings (user_id, guild_id, warning_type, reason)
                        VALUES (?, ?, ?, ?)
                    """, (str(message.author.id), str(message.guild.id), violation_type, reason))
                    
                    # Check warning threshold
                    cursor.execute("""
                        SELECT COUNT(*) FROM automod_warnings
                        WHERE user_id = ? AND guild_id = ?
                        AND timestamp > datetime('now', '-1 day')
                    """, (str(message.author.id), str(message.guild.id)))
                    
                    warning_count = cursor.fetchone()[0]
                    conn.commit()

                    if warning_count >= self.config["punishments"]["warn_threshold"]:
                        await self.mute_user(message.author)
                finally:
                    conn.close()

        except Exception as e:
            logger.error(f"Error handling violation: {e}")
            await event_dispatcher.dispatch('error', None, e)

    async def mute_user(self, member: discord.Member):
        """Mute a user for the configured duration"""
        async with await self.get_mute_lock(member.guild.id):
            muted_role = discord.utils.get(member.guild.roles, name="Muted")
            if not muted_role:
                try:
                    muted_role = await member.guild.create_role(
                        name="Muted",
                        reason="AutoMod: Created muted role"
                    )
                    # Set permissions for all channels
                    for channel in member.guild.channels:
                        await channel.set_permissions(muted_role, send_messages=False)
                except discord.Forbidden:
                    logger.error("Failed to create muted role - insufficient permissions")
                    return

            try:
                # Apply mute
                await member.add_roles(muted_role, reason="AutoMod: Exceeded warning threshold")
                
                # Create notification embed
                embed = Embed.create(
                    title="🔇 User Muted",
                    description=f"{member.mention} has been muted for {self.config['punishments']['mute_duration']} minutes",
                    color=discord.Color.red()
                )
                
                # Send notification
                log_channel = member.guild.system_channel
                if log_channel:
                    await log_channel.send(embed=embed)

                # Schedule unmute
                await asyncio.sleep(self.config["punishments"]["mute_duration"] * 60)
                await member.remove_roles(muted_role, reason="AutoMod: Mute duration expired")

            except discord.Forbidden:
                logger.error(f"Failed to mute user {member.id} - insufficient permissions")
            except Exception as e:
                logger.error(f"Error muting user {member.id}: {e}")

    @commands.group(name="automod", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def automod(self, ctx):
        """Show AutoMod status and settings"""
        embed = discord.Embed(
            title="📊 AutoMod Status",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="Status", 
            value="✅ Enabled" if self.config["enabled"] else "❌ Disabled"
        )
        
        embed.add_field(
            name="Spam Protection",
            value=f"""{'✅' if self.config['spam']['enabled'] else '❌'} Enabled
                     Threshold: {self.config['spam']['threshold']} msgs
                     Timeframe: {self.config['spam']['timeframe']}s""",
            inline=False
        )
        
        embed.add_field(
            name="Caps Protection",
            value=f"""{'✅' if self.config['caps']['enabled'] else '❌'} Enabled
                     Threshold: {self.config['caps']['threshold']*100}%
                     Min Length: {self.config['caps']['min_length']} chars""",
            inline=False
        )
        
        banned_words = len(self.config["banned_words"]["words"])
        wildcards = len(self.config["banned_words"]["wildcards"])
        
        embed.add_field(
            name="Word Filter",
            value=f"""{'✅' if self.config['banned_words']['enabled'] else '❌'} Enabled
                     Banned Words: {banned_words}
                     Wildcards: {wildcards}""",
            inline=False
        )
        
        await ctx.send(embed=embed)

    @automod.command(name="toggle")
    async def toggle_automod(self, ctx, feature: str = None, state: bool = None):
        """Toggle AutoMod features on/off"""
        if feature is None:
            # Toggle entire AutoMod
            self.config["enabled"] = not self.config["enabled"]
            state_str = "enabled" if self.config["enabled"] else "disabled"
            await ctx.send(f"✅ AutoMod has been {state_str}")
        else:
            feature = feature.lower()
            if feature in ["spam", "caps", "words"]:
                config_key = "banned_words" if feature == "words" else feature
                if state is None:
                    # Toggle current state
                    self.config[config_key]["enabled"] = not self.config[config_key]["enabled"]
                else:
                    # Set to specific state
                    self.config[config_key]["enabled"] = state
                
                state_str = "enabled" if self.config[config_key]["enabled"] else "disabled"
                await ctx.send(f"✅ {feature.title()} protection has been {state_str}")
            else:
                await ctx.send("❌ Invalid feature. Available features: spam, caps, words")
        
        await self.save_config()

    @automod.command(name="settings")
    async def view_settings(self, ctx):
        """View detailed AutoMod settings"""
        formatted_config = json.dumps(self.config, indent=2)
        chunks = [formatted_config[i:i+1990] for i in range(0, len(formatted_config), 1990)]
        
        for i, chunk in enumerate(chunks):
            await ctx.send(f"```json\n{chunk}\n```")

    @automod.command(name="addword")
    async def add_banned_word(self, ctx, *, word: str):
        """Add a word to the banned list"""
        async with self.config_lock:
            word = word.lower()
            if word in self.config["banned_words"]["words"]:
                await ctx.send("❌ Word is already in the banned list!")
                return
                
            self.config["banned_words"]["words"].append(word)
            await self.save_config()
            
            # Update cache
            self._banned_words_cache.add(word)
            
        await ctx.send(f"✅ Added '{word}' to banned words")
        try:
            await ctx.message.delete()  # Delete command message to hide the banned word
        except:
            pass

    @automod.command(name="removeword")
    async def remove_banned_word(self, ctx, *, word: str):
        """Remove a word from the banned list"""
        async with self.config_lock:
            word = word.lower()
            try:
                self.config["banned_words"]["words"].remove(word)
                await self.save_config()
                
                # Update cache
                self._banned_words_cache.remove(word)
                
                await ctx.send(f"✅ Removed '{word}' from banned words")
            except ValueError:
                await ctx.send("❌ Word not found in banned words list")
        try:
            await ctx.message.delete()  # Delete command message to hide the banned word
        except:
            pass

    @automod.command(name="addwildcard")
    async def add_wildcard(self, ctx, *, pattern: str):
        """Add a wildcard pattern to the banned list"""
        async with self.config_lock:
            pattern = pattern.lower()
            if pattern in self.config["banned_words"]["wildcards"]:
                await ctx.send("❌ Pattern is already in the wildcards list!")
                return
                
            self.config["banned_words"]["wildcards"].append(pattern)
            await self.save_config()
            
        await ctx.send(f"✅ Added '{pattern}' to wildcards")
        try:
            await ctx.message.delete()
        except:
            pass

    @automod.command(name="removewildcard")
    async def remove_wildcard(self, ctx, *, pattern: str):
        """Remove a wildcard pattern from the banned list"""
        async with self.config_lock:
            pattern = pattern.lower()
            try:
                self.config["banned_words"]["wildcards"].remove(pattern)
                await self.save_config()
                await ctx.send(f"✅ Removed '{pattern}' from wildcards")
            except ValueError:
                await ctx.send("❌ Pattern not found in wildcards list")
        try:
            await ctx.message.delete()
        except:
            pass

    @automod.command(name="threshold")
    async def set_threshold(self, ctx, feature: str, value: float):
        """Set threshold for spam or caps protection"""
        async with self.config_lock:
            if feature.lower() == "spam":
                if 1 <= value <= 20:
                    self.config["spam"]["threshold"] = int(value)
                    await ctx.send(f"✅ Spam threshold set to {int(value)} messages")
                else:
                    await ctx.send("❌ Spam threshold must be between 1 and 20")
            elif feature.lower() == "caps":
                if 0.1 <= value <= 1.0:
                    self.config["caps"]["threshold"] = value
                    await ctx.send(f"✅ Caps threshold set to {value*100}%")
                else:
                    await ctx.send("❌ Caps threshold must be between 0.1 and 1.0")
            else:
                await ctx.send("❌ Invalid feature. Available features: spam, caps")
            
            await self.save_config()

    @automod.command(name="timeframe")
    async def set_timeframe(self, ctx, seconds: int):
        """Set timeframe for spam detection (in seconds)"""
        async with self.config_lock:
            if 1 <= seconds <= 60:
                self.config["spam"]["timeframe"] = seconds
                await self.save_config()
                await ctx.send(f"✅ Spam timeframe set to {seconds} seconds")
            else:
                await ctx.send("❌ Timeframe must be between 1 and 60 seconds")

    @automod.command(name="reset")
    async def reset_settings(self, ctx):
        """Reset AutoMod settings to default"""
        confirm_msg = await ctx.send("⚠️ Are you sure you want to reset all AutoMod settings? React with ✅ to confirm.")
        await confirm_msg.add_reaction("✅")
        
        try:
            def check(reaction, user):
                return user == ctx.author and str(reaction.emoji) == "✅"
                
            await self.bot.wait_for('reaction_add', timeout=30.0, check=check)
            
            # Reset to default
            self.config = self.load_config(force_default=True)
            self._banned_words_cache = set(word.lower() for word in self.config["banned_words"]["words"])
            
            await ctx.send("✅ AutoMod settings have been reset to default!")
            
        except asyncio.TimeoutError:
            await ctx.send("❌ Reset cancelled - no confirmation received.")
            
        try:
            await confirm_msg.delete()
        except:
            pass

async def setup(bot):
    """Setup the AutoMod cog"""
    await bot.add_cog(AutoMod(bot))