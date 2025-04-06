import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import io
import aiohttp
from datetime import datetime
from typing import Optional
from .utils import Embed, event_dispatcher
from database import get_connection
import sqlite3
import asyncio
from asyncio import Lock
import logging

logger = logging.getLogger(__name__)

class Welcome(commands.Cog):
    """👋 Sistem Welcome Advanced"""
    
    def __init__(self, bot):
        self.bot = bot
        self.font_path = "assets/fonts/"
        self.background_path = "assets/backgrounds/"
        # Initialize locks
        self.db_lock = Lock()  # For database operations
        self.welcome_lock = Lock()  # For welcome card creation and sending
        self.response_lock = Lock()  # For preventing multiple responses
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

    async def send_response_once(self, ctx, message, *, embed=None, file=None):
        """Helper method to ensure only one response is sent"""
        if not await self.acquire_lock(self.response_lock, timeout=5.0):
            return
        
        try:
            if file and embed:
                await ctx.send(content=message, embed=embed, file=file)
            elif embed:
                await ctx.send(embed=embed)
            elif file:
                await ctx.send(content=message, file=file)
            else:
                await ctx.send(message)
        except Exception as e:
            logger.error(f"Error sending response: {e}")
        finally:
            self.response_lock.release()

    def register_handlers(self):
        """Register event handlers"""
        event_dispatcher.register('member_join', self.handle_member_join)
        event_dispatcher.register('reaction_add', self.handle_verification)

    async def get_guild_settings(self, guild_id: int) -> dict:
        """Get welcome settings for a guild"""
        async with self.db_lock:
            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT * FROM welcome_settings WHERE guild_id = ?
                """, (str(guild_id),))
                data = cursor.fetchone()
                
                if not data:
                    return {
                        'channel_id': None,
                        'message': 'Welcome {user} to {server}!',
                        'embed_color': 3447003,
                        'auto_role_id': None,
                        'verification_required': False,
                        'custom_background': None,
                        'custom_font': None
                    }
                    
                return dict(data)
            except sqlite3.Error as e:
                logger.error(f"Failed to get guild settings: {e}")
                raise
            finally:
                if conn:
                    conn.close()

    async def create_welcome_card(self, member: discord.Member, settings: dict) -> io.BytesIO:
        """Create a customized welcome card"""
        if not await self.acquire_lock(self.welcome_lock):
            logger.error("Failed to acquire welcome lock for card creation")
            return None

        try:
            # Load background
            try:
                if settings['custom_background']:
                    background = Image.open(f"{self.background_path}{settings['custom_background']}")
                else:
                    background = Image.open(f"{self.background_path}welcome_bg.png")
            except Exception as e:
                logger.error(f"Failed to load background: {e}")
                return None
                
            # Apply blur effect to background
            background = background.filter(ImageFilter.GaussianBlur(5))
            
            # Create drawing context
            draw = ImageDraw.Draw(background)
            
            try:
                # Load fonts
                title_font = ImageFont.truetype(
                    f"{self.font_path}{settings.get('custom_font', 'title.ttf')}", 
                    60
                )
                subtitle_font = ImageFont.truetype(
                    f"{self.font_path}{settings.get('custom_font', 'subtitle.ttf')}", 
                    40
                )
            except Exception as e:
                logger.error(f"Failed to load fonts: {e}")
                return None
            
            # Download and process avatar
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(str(member.display_avatar.url)) as resp:
                        if resp.status != 200:
                            logger.error(f"Failed to download avatar: {resp.status}")
                            return None
                        avatar_bytes = await resp.read()
            except Exception as e:
                logger.error(f"Failed to download avatar: {e}")
                return None
                    
            try:
                with Image.open(io.BytesIO(avatar_bytes)) as avatar:
                    # Create circular mask
                    mask = Image.new("L", avatar.size, 0)
                    draw_mask = ImageDraw.Draw(mask)
                    draw_mask.ellipse((0, 0, *avatar.size), fill=255)
                    
                    # Apply mask and resize
                    avatar = avatar.resize((200, 200))
                    mask = mask.resize((200, 200))
                    
                    # Create circular border
                    border = Image.new("RGBA", (220, 220), (255, 255, 255, 0))
                    draw_border = ImageDraw.Draw(border)
                    draw_border.ellipse((0, 0, 219, 219), outline=(255, 255, 255, 255), width=3)
                    
                    # Composite images
                    background.paste(avatar, (340, 50), mask)
                    background.paste(border, (330, 40), border)
            except Exception as e:
                logger.error(f"Failed to process avatar: {e}")
                return None
                
            # Add text with shadow effect
            def draw_text_with_shadow(text, position, font, fill, shadow_color=(0, 0, 0)):
                # Draw shadow
                draw.text((position[0]+2, position[1]+2), text, font=font, fill=shadow_color)
                # Draw main text
                draw.text(position, text, font=font, fill=fill)
                
            # Welcome text
            draw_text_with_shadow(
                f"Welcome {member.name}!",
                (450, 280),
                title_font,
                "white"
            )
            
            # Member count
            draw_text_with_shadow(
                f"Member #{len(member.guild.members)}",
                (450, 340),
                subtitle_font,
                "lightgray"
            )
            
            # Server name
            draw_text_with_shadow(
                member.guild.name,
                (450, 400),
                subtitle_font,
                "white"
            )
            
            # Convert to bytes
            buffer = io.BytesIO()
            background.save(buffer, format="PNG")
            buffer.seek(0)
            
            return buffer

        except Exception as e:
            logger.error(f"Error creating welcome card: {e}")
            return None
        finally:
            self.welcome_lock.release()

    async def handle_member_join(self, member: discord.Member):
        """Handle new member joins"""
        if not await self.acquire_lock(self.welcome_lock):
            logger.error("Failed to acquire welcome lock for member join")
            return

        try:
            settings = await self.get_guild_settings(member.guild.id)
            
            if not settings['channel_id']:
                return
                
            channel = self.bot.get_channel(int(settings['channel_id']))
            if not channel:
                return
                
            # Create welcome card
            card_buffer = await self.create_welcome_card(member, settings)
            if not card_buffer:
                return
            
            # Create embed
            embed = Embed.create(
                title="👋 Welcome to the Server!",
                description=settings['message'].format(
                    user=member.mention,
                    server=member.guild.name
                ),
                color=settings['embed_color'],
                field_Account_Created={
                    "value": f"<t:{int(member.created_at.timestamp())}:R>",
                    "inline": True
                },
                field_Member_Count={
                    "value": str(len(member.guild.members)),
                    "inline": True
                }
            )
            
            if settings['verification_required']:
                embed.add_field(
                    name="✅ Verification",
                    value="Please react with ✅ to gain access to the server",
                    inline=False
                )
                
            # Send welcome message
            file = discord.File(card_buffer, "welcome.png")
            embed.set_image(url="attachment://welcome.png")
            
            welcome_msg = await channel.send(
                content=member.mention,
                embed=embed,
                file=file
            )
            
            # Add verification reaction if required
            if settings['verification_required']:
                await welcome_msg.add_reaction("✅")
                
            # Add auto role if configured
            if settings['auto_role_id'] and not settings['verification_required']:
                role = member.guild.get_role(int(settings['auto_role_id']))
                if role:
                    try:
                        await member.add_roles(role)
                    except discord.Forbidden:
                        logger.error(f"Failed to add auto-role to {member.id}: Missing permissions")
                        
            # Log welcome
            await self.log_welcome(member.guild.id, member.id, 'join')
        
        except Exception as e:
            logger.error(f"Error handling member join: {e}")
        finally:
            self.welcome_lock.release()

    async def handle_verification(self, payload):
        """Handle verification reactions"""
        if str(payload.emoji) != "✅":
            return

        if not await self.acquire_lock(self.welcome_lock):
            return
            
        try:
            settings = await self.get_guild_settings(payload.guild_id)
            if not settings['verification_required']:
                return
                
            if not settings['auto_role_id']:
                return
                
            guild = self.bot.get_guild(payload.guild_id)
            if not guild:
                return
                
            member = guild.get_member(payload.user_id)
            if not member or member.bot:
                return
                
            role = guild.get_role(int(settings['auto_role_id']))
            if role:
                try:
                    await member.add_roles(role)
                    await self.log_welcome(guild.id, member.id, 'verify')
                except discord.Forbidden:
                    logger.error(f"Failed to add verification role to {member.id}: Missing permissions")
        finally:
            self.welcome_lock.release()

    async def log_welcome(self, guild_id: int, user_id: int, action_type: str):
        """Log welcome events"""
        async with self.db_lock:
            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                
                cursor.execute("""
                    INSERT INTO welcome_logs (guild_id, user_id, action_type)
                    VALUES (?, ?, ?)
                """, (str(guild_id), str(user_id), action_type))
                conn.commit()
            except sqlite3.Error as e:
                logger.error(f"Failed to log welcome event: {e}")
            finally:
                if conn:
                    conn.close()

    @commands.group(name="welcome")
    @commands.has_permissions(administrator=True)
    async def welcome(self, ctx):
        """⚙️ Welcome system settings"""
        if ctx.invoked_subcommand is None:
            await self.send_response_once(ctx, await ctx.send_help(ctx.command))

    @welcome.command(name="setchannel")
    async def set_welcome_channel(self, ctx, channel: discord.TextChannel):
        """Set welcome channel"""
        async with self.db_lock:
            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                
                cursor.execute("""
                    INSERT OR REPLACE INTO welcome_settings 
                    (guild_id, channel_id) VALUES (?, ?)
                """, (str(ctx.guild.id), str(channel.id)))
                conn.commit()
                
                await self.send_response_once(ctx, f"✅ Welcome channel set to {channel.mention}")
            except sqlite3.Error as e:
                logger.error(f"Failed to set welcome channel: {e}")
                await self.send_response_once(ctx, "❌ Failed to set welcome channel")
            finally:
                if conn:
                    conn.close()

    @welcome.command(name="setmessage")
    async def set_welcome_message(self, ctx, *, message: str):
        """Set custom welcome message"""
        async with self.db_lock:
            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                
                cursor.execute("""
                    INSERT OR REPLACE INTO welcome_settings 
                    (guild_id, message) VALUES (?, ?)
                """, (str(ctx.guild.id), message))
                conn.commit()
                
                await self.send_response_once(ctx, "✅ Welcome message updated!")
            except sqlite3.Error as e:
                logger.error(f"Failed to set welcome message: {e}")
                await self.send_response_once(ctx, "❌ Failed to set welcome message")
            finally:
                if conn:
                    conn.close()

    @welcome.command(name="setrole")
    async def set_auto_role(self, ctx, role: discord.Role):
        """Set auto-role for new members"""
        async with self.db_lock:
            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                
                cursor.execute("""
                    INSERT OR REPLACE INTO welcome_settings 
                    (guild_id, auto_role_id) VALUES (?, ?)
                """, (str(ctx.guild.id), str(role.id)))
                conn.commit()
                
                await self.send_response_once(ctx, f"✅ Auto-role set to {role.mention}")
            except sqlite3.Error as e:
                logger.error(f"Failed to set auto-role: {e}")
                await self.send_response_once(ctx, "❌ Failed to set auto-role")
            finally:
                if conn:
                    conn.close()

    @welcome.command(name="toggleverify")
    async def toggle_verification(self, ctx):
        """Toggle verification requirement"""
        async with self.db_lock:
            try:
                settings = await self.get_guild_settings(ctx.guild.id)
                new_state = not settings['verification_required']
                
                conn = None
                try:
                    conn = get_connection()
                    cursor = conn.cursor()
                    
                    cursor.execute("""
                        INSERT OR REPLACE INTO welcome_settings 
                        (guild_id, verification_required) VALUES (?, ?)
                    """, (str(ctx.guild.id), new_state))
                    conn.commit()
                    
                    await self.send_response_once(
                        ctx, 
                        f"✅ Verification requirement {'enabled' if new_state else 'disabled'}"
                    )
                except sqlite3.Error as e:
                    logger.error(f"Failed to toggle verification: {e}")
                    await self.send_response_once(ctx, "❌ Failed to toggle verification")
                finally:
                    if conn:
                        conn.close()
            except Exception as e:
                logger.error(f"Error toggling verification: {e}")
                await self.send_response_once(ctx, "❌ An error occurred")

    @welcome.command(name="test")
    async def test_welcome(self, ctx):
        """Test welcome message"""
        if not await self.acquire_lock(self.welcome_lock):
            return await self.send_response_once(ctx, "❌ System is busy, please try again later")
            
        try:
            await self.handle_member_join(ctx.author)
            await self.send_response_once(ctx, "✅ Test welcome message sent!")
        except Exception as e:
            logger.error(f"Error testing welcome message: {e}")
            await self.send_response_once(ctx, "❌ Failed to send test welcome message")
        finally:
            self.welcome_lock.release()

async def setup(bot):
    """Setup the Welcome cog"""
    await bot.add_cog(Welcome(bot))