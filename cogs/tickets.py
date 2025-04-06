"""
Ticket System for Store DC Bot
Author: fdyytu1
Created at: 2025-03-13 11:49:51 UTC
"""

import discord
from discord.ext import commands
from discord.ui import Button, View, Modal, Select
import logging
import asyncio
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, List

from .utils import (
    Embed, 
    get_connection,
    logger, 
    EventDispatcher,
    Permissions,
    transaction
)
from ext.constants import (
    COLORS,
    MESSAGES,
    EVENTS,
    CACHE_TIMEOUT,
    BUTTON_IDS
)
from ext.base_handler import BaseLockHandler
from ext.cache_manager import CacheManager

logger = logging.getLogger(__name__)

# Views
class TicketView(View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
        
    @discord.ui.button(
        label="Create Ticket",
        style=discord.ButtonStyle.primary,
        emoji="🎫",
        custom_id=BUTTON_IDS.TICKET_CREATE
    )
    async def create_ticket(self, interaction: discord.Interaction, button: Button):
        # Prevent double clicks
        await interaction.response.defer(ephemeral=True)
        
        # Check if user already has modal open
        if hasattr(interaction.user, 'ticket_modal_open'):
            return await interaction.followup.send(
                "You already have a ticket creation window open!",
                ephemeral=True
            )
            
        setattr(interaction.user, 'ticket_modal_open', True)
        try:
            await interaction.followup.send_modal(TicketModal(self.bot))
        finally:
            delattr(interaction.user, 'ticket_modal_open')

class TicketModal(discord.ui.Modal):
    def __init__(self, bot):
        super().__init__(title="Create Support Ticket")
        self.bot = bot
        
        self.topic = discord.ui.TextInput(
            label="Topic",
            placeholder="E.g. Payment Issue, Technical Support, etc.",
            required=True,
            max_length=100
        )
        
        self.description = discord.ui.TextInput(
            label="Description",
            style=discord.TextStyle.paragraph,
            placeholder="Please describe your issue in detail",
            required=True,
            max_length=1000
        )
        
        self.add_item(self.topic)
        self.add_item(self.description)

    async def on_submit(self, interaction: discord.Interaction):
        # Prevent double submission
        if hasattr(interaction, 'ticket_submitted'):
            return
        setattr(interaction, 'ticket_submitted', True)
        
        await interaction.response.defer(ephemeral=True)
        
        ticket_system = self.bot.get_cog("TicketSystem")
        if not ticket_system:
            return await interaction.followup.send(
                "Ticket system is not available",
                ephemeral=True
            )
            
        await ticket_system.create_ticket(
            interaction,
            str(self.topic),
            str(self.description)
        )

class TicketControlView(View):
    def __init__(self, bot, ticket_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.ticket_id = ticket_id
        
    @discord.ui.button(
        label="Close",
        style=discord.ButtonStyle.red,
        emoji="🔒",
        custom_id=f"{BUTTON_IDS.TICKET_CLOSE}_{self.ticket_id}"
    )
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        # Prevent double clicks
        await interaction.response.defer(ephemeral=True)
        
        ticket_system = self.bot.get_cog("TicketSystem")
        if not ticket_system:
            return await interaction.followup.send(
                "Ticket system is not available",
                ephemeral=True
            )
        
        # Check if ticket is already being closed
        if hasattr(interaction.channel, 'closing'):
            return await interaction.followup.send(
                "This ticket is already being closed!",
                ephemeral=True
            )
            
        setattr(interaction.channel, 'closing', True)
        try:
            await ticket_system.close_ticket(interaction, self.ticket_id)
        finally:
            if hasattr(interaction.channel, 'closing'):
                delattr(interaction.channel, 'closing')
        
    @discord.ui.button(
        label="Priority",
        style=discord.ButtonStyle.secondary,
        emoji="⭐",
        custom_id=f"{BUTTON_IDS.TICKET_PRIORITY}_{self.ticket_id}"
    )
    async def set_priority(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        
        ticket_system = self.bot.get_cog("TicketSystem")
        if not ticket_system:
            return await interaction.followup.send(
                "Ticket system is not available",
                ephemeral=True
            )
        
        options = [
            discord.SelectOption(label="Low", value="low", emoji="🟢"),
            discord.SelectOption(label="Medium", value="medium", emoji="🟡"),
            discord.SelectOption(label="High", value="high", emoji="🔴"),
            discord.SelectOption(label="Urgent", value="urgent", emoji="⚡")
        ]
        
        select = discord.ui.Select(
            placeholder="Select ticket priority",
            options=options,
            custom_id=f"priority_select_{self.ticket_id}"
        )
        
        async def priority_callback(interaction: discord.Interaction):
            if hasattr(interaction, 'priority_set'):
                return
            setattr(interaction, 'priority_set', True)
            
            await ticket_system.set_ticket_priority(
                interaction,
                self.ticket_id,
                select.values[0]
            )
            
        select.callback = priority_callback
        view = View()
        view.add_item(select)
        await interaction.followup.send(
            "Select ticket priority:",
            view=view,
            ephemeral=True
        )

# Main Ticket System
class TicketSystem(commands.Cog, BaseLockHandler):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.cache_manager = CacheManager()
        self.active_tickets = {}
        self.ticket_cooldowns = {}
        self.setup_tasks = {}
        self.auto_close_task = self.bot.loop.create_task(self.check_inactive_tickets())
        
    def cog_unload(self):
        """Cleanup when cog is unloaded"""
        self.auto_close_task.cancel()
        for task in self.setup_tasks.values():
            task.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        """Setup ticket channels and load active tickets"""
        logger.info("Setting up ticket system...")
        
        try:
            # Load active tickets first
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT id, channel_id FROM tickets 
                WHERE status = 'open'
            """)
            
            for row in cursor.fetchall():
                self.active_tickets[int(row['channel_id'])] = row['id']
                
            logger.info(f"Loaded {len(self.active_tickets)} active tickets")
            
            # Setup channels for each guild
            for guild in self.bot.guilds:
                if guild.id not in self.setup_tasks:
                    self.setup_tasks[guild.id] = self.bot.loop.create_task(
                        self.setup_guild_tickets(guild)
                    )
            
        except Exception as e:
            logger.error(f"Error in ticket system setup: {e}")
        finally:
            if 'conn' in locals():
                conn.close()

    async def setup_guild_tickets(self, guild):
        """Setup ticket channel for a specific guild"""
        try:
            # Get settings
            settings = await self.get_guild_settings(guild.id)
            
            # Find or create ticket channel
            ticket_channel = None
            for channel in guild.text_channels:
                if channel.name.lower() == "ticket":
                    ticket_channel = channel
                    break
                    
            if not ticket_channel:
                # Create ticket channel with proper permissions
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(
                        send_messages=False,
                        send_messages_in_threads=False
                    ),
                    guild.me: discord.PermissionOverwrite(
                        send_messages=True,
                        manage_channels=True,
                        manage_messages=True
                    )
                }
                
                if settings.get('support_role_id'):
                    support_role = guild.get_role(int(settings['support_role_id']))
                    if support_role:
                        overwrites[support_role] = discord.PermissionOverwrite(
                            send_messages=True,
                            manage_messages=True
                        )
                
                ticket_channel = await guild.create_text_channel(
                    "ticket",
                    overwrites=overwrites
                )
                logger.info(f"Created ticket channel in {guild.name}")
            
            # Check if button exists
            button_exists = False
            async for message in ticket_channel.history(limit=100):
                if message.author == self.bot.user and any(
                    component.custom_id == BUTTON_IDS.TICKET_CREATE 
                    for component in message.components
                ):
                    button_exists = True
                    break
            
            if not button_exists:
                # Create ticket button
                embed = Embed.create(
                    title="🎫 Support Ticket System",
                    description=(
                        "Need help? Click the button below to create a support ticket!\n\n"
                        "**Guidelines:**\n"
                        "• One ticket per issue\n"
                        "• Be patient and respectful\n"
                        "• Provide clear information\n"
                        "• Follow server rules"
                    ),
                    color=COLORS.INFO
                )
                
                view = TicketView(self.bot)
                await ticket_channel.send(embed=embed, view=view)
                logger.info(f"Created ticket button in {guild.name}")
                
        except Exception as e:
            logger.error(f"Error setting up tickets for {guild.name}: {e}")
        finally:
            if guild.id in self.setup_tasks:
                del self.setup_tasks[guild.id]

    async def get_guild_settings(self, guild_id: int) -> Dict:
        """Get ticket settings for a guild"""
        cache_key = f"ticket_settings_{guild_id}"
        
        # Try cache first
        settings = await self.cache_manager.get(cache_key)
        if settings:
            return settings
            
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM ticket_settings 
                WHERE guild_id = ?
            """, (str(guild_id),))
            
            data = cursor.fetchone()
            
            if not data:
                # Use default settings
                settings = {
                    'category_id': None,
                    'log_channel_id': None,
                    'support_role_id': None,
                    'max_tickets': 1,
                    'ticket_format': 'ticket-{user}-{number}',
                    'auto_close_hours': 48,
                    'notification_channel': None,
                    'allow_user_close': True,
                    'ticket_welcome': "Support team will assist you shortly."
                }
                
                # Save default settings
                cursor.execute("""
                    INSERT INTO ticket_settings (guild_id)
                    VALUES (?)
                """, (str(guild_id),))
                
                conn.commit()
            else:
                settings = dict(data)
            
            # Cache settings
            await self.cache_manager.set(
                cache_key,
                settings,
                expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.MEDIUM)
            )
            
            return settings
            
        except Exception as e:
            logger.error(f"Error getting guild settings: {e}")
            return {}
        finally:
            if conn:
                conn.close()

    async def create_ticket(
        self,
        interaction: discord.Interaction,
        title: str,
        description: str
    ):
        """Create a new ticket"""
        settings = await self.get_guild_settings(interaction.guild_id)
        
        # Check rate limiting
        user_id = str(interaction.user.id)
        if user_id in self.ticket_cooldowns:
            remaining = self.ticket_cooldowns[user_id] - datetime.utcnow()
            if remaining.total_seconds() > 0:
                return await interaction.followup.send(
                    f"Please wait {int(remaining.total_seconds())} seconds before creating another ticket",
                    ephemeral=True
                )

        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Check max tickets
            cursor.execute("""
                SELECT COUNT(*) as count FROM tickets 
                WHERE guild_id = ? AND user_id = ? AND status = 'open'
            """, (str(interaction.guild_id), user_id))
            
            count = cursor.fetchone()['count']
            if count >= settings['max_tickets']:
                return await interaction.followup.send(
                    "You have reached the maximum number of open tickets!",
                    ephemeral=True
                )
                
            # Get or create category
            category_id = settings.get('category_id')
            category = interaction.guild.get_channel(int(category_id)) if category_id else None
            
            if not category:
                category = await interaction.guild.create_category("Tickets")
                cursor.execute("""
                    UPDATE ticket_settings 
                    SET category_id = ? 
                    WHERE guild_id = ?
                """, (str(category.id), str(interaction.guild_id)))
                conn.commit()
            
            # Set channel permissions
            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            
            if settings['support_role_id']:
                support_role = interaction.guild.get_role(int(settings['support_role_id']))
                if support_role:
                    overwrites[support_role] = discord.PermissionOverwrite(
                        read_messages=True,
                        send_messages=True,
                        manage_messages=True
                    )
                    
            # Create channel
            channel_name = settings['ticket_format'].format(
                user=interaction.user.name.lower(),
                number=count + 1
            )
            
            channel = await category.create_text_channel(
                channel_name,
                overwrites=overwrites
            )
            
            # Save ticket to database
            cursor.execute("""
                INSERT INTO tickets (
                    guild_id, channel_id, user_id,
                    title, description, last_activity
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                str(interaction.guild_id),
                str(channel.id),
                user_id,
                title,
                description,
                datetime.utcnow()
            ))
            
            ticket_id = cursor.lastrowid
            self.active_tickets[channel.id] = ticket_id
            
            # Create ticket embed
            embed = Embed.create(
                title=f"Ticket: {title}",
                description=description,
                color=COLORS.DEFAULT
            )
            embed.add_field(name="Created By", value=interaction.user.mention)
            embed.add_field(name="Status", value="🟢 Open")
            embed.add_field(name="Priority", value="🟢 Low")
            embed.set_footer(text=f"Ticket ID: {ticket_id}")
            
            # Add ticket controls
            control_view = TicketControlView(self.bot, ticket_id)
            
            # Send welcome message
            welcome_msg = settings.get('ticket_welcome', "Support team will assist you shortly.")
            await channel.send(
                f"{interaction.user.mention} {welcome_msg}",
                embed=embed,
                view=control_view
            )
            
            # Send notification
            if settings.get('notification_channel'):
                notif_channel = interaction.guild.get_channel(
                    int(settings['notification_channel'])
                )
                if notif_channel:
                    await notif_channel.send(
                        f"New ticket created by {interaction.user.mention}: {title}"
                    )
            
            # Set cooldown
            self.ticket_cooldowns[user_id] = datetime.utcnow() + timedelta(minutes=5)
            
            await interaction.followup.send(
                f"Ticket created! Head to {channel.mention}",
                ephemeral=True
            )
            
            conn.commit()
            
        except Exception as e:
            logger.error(f"Error creating ticket: {e}")
            if conn:
                conn.rollback()
            await interaction.followup.send(
                "An error occurred while creating the ticket",
                ephemeral=True
            )
        finally:
            if conn:
                conn.close()

    async def close_ticket(self, interaction: discord.Interaction, ticket_id: int):
        """Close a ticket"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Get ticket info
            cursor.execute("""
                SELECT * FROM tickets WHERE id = ?
            """, (ticket_id,))
            
            ticket = cursor.fetchone()
            if not ticket:
                return await interaction.followup.send(
                    "Ticket not found!",
                    ephemeral=True
                )
                
            # Check permissions
            settings = await self.get_guild_settings(interaction.guild_id)
            is_support = False
            if settings['support_role_id']:
                support_role = interaction.guild.get_role(int(settings['support_role_id']))
                if support_role in interaction.user.roles:
                    is_support = True
                    
            if not (is_support or str(interaction.user.id) == ticket['user_id']):
                return await interaction.followup.send(
                    "You don't have permission to close this ticket!",
                    ephemeral=True
                )
            
            # Ask for feedback
            embed = Embed.create(
                title="Ticket Feedback",
                description="Please rate your support experience:",
                color=COLORS.DEFAULT
            )
            
            view = View()
            options = [
                discord.SelectOption(label="Excellent", value="5", emoji="⭐⭐⭐⭐⭐"),
                discord.SelectOption(label="Good", value="4", emoji="⭐⭐⭐⭐"),
                discord.SelectOption(label="Okay", value="3", emoji="⭐⭐⭐"),
                discord.SelectOption(label="Poor", value="2", emoji="⭐⭐"),
                discord.SelectOption(label="Very Poor", value="1", emoji="⭐")
            ]
            
            select = discord.ui.Select(
                placeholder="Select rating",
                options=options,
                custom_id=f"feedback_select_{ticket_id}"
            )
            
            async def feedback_callback(interaction: discord.Interaction):
                if hasattr(interaction, 'feedback_submitted'):
                    return
                setattr(interaction, 'feedback_submitted', True)
                
                rating = int(select.values[0])
                
                # Update ticket
                cursor.execute("""
                    UPDATE tickets 
                    SET feedback_score = ?,
                        status = 'closed',
                        closed_at = CURRENT_TIMESTAMP,
                        closed_by = ?
                    WHERE id = ?
                """, (rating, str(interaction.user.id), ticket_id))
                
                # Create transcript
                transcript = await self.create_transcript(interaction.channel)
                
                # Log closure
                if settings['log_channel_id']:
                    log_channel = interaction.guild.get_channel(
                        int(settings['log_channel_id'])
                    )
                    if log_channel:
                        log_embed = Embed.create(
                            title="Ticket Closed",
                            color=COLORS.ERROR
                        )
                        log_embed.add_field(name="Ticket ID", value=str(ticket_id))
                        log_embed.add_field(name="Closed By", value=interaction.user.mention)
                        log_embed.add_field(name="Rating", value=f"{rating} ⭐")
                        
                        if transcript:
                            log_embed.add_field(
                                name="Transcript",
                                value=transcript[:1000] + "..." if len(transcript) > 1000 else transcript,
                                inline=False
                            )
                            
                        await log_channel.send(embed=log_embed)
                
                # Delete channel after delay
                await interaction.response.send_message("Closing ticket in 5 seconds...")
                await asyncio.sleep(5)
                await interaction.channel.delete()
                
                # Remove from active tickets
                if interaction.channel.id in self.active_tickets:
                    del self.active_tickets[interaction.channel.id]
                
                conn.commit()
            
            select.callback = feedback_callback
            view.add_item(select)
            await interaction.followup.send(embed=embed, view=view)
            
        except Exception as e:
            logger.error(f"Error closing ticket: {e}")
            if conn:
                conn.rollback()
            await interaction.followup.send(
                "An error occurred while closing the ticket",
                ephemeral=True
            )
        finally:
            if conn:
                conn.close()

    async def set_ticket_priority(
        self,
        interaction: discord.Interaction,
        ticket_id: int,
        priority: str
    ):
        """Set ticket priority"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Check permissions
            settings = await self.get_guild_settings(interaction.guild_id)
            if settings['support_role_id']:
                support_role = interaction.guild.get_role(int(settings['support_role_id']))
                if support_role not in interaction.user.roles:
                    return await interaction.followup.send(
                        "You don't have permission to set ticket priority!",
                        ephemeral=True
                    )
            
            # Update priority
            cursor.execute("""
                UPDATE tickets 
                SET priority = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (priority, ticket_id))
            
            # Update embed
            async for message in interaction.channel.history(limit=1):
                if message.author == self.bot.user and message.embeds:
                    embed = message.embeds[0]
                    
                    # Set color based on priority
                    colors = {
                        'low': COLORS.SUCCESS,
                        'medium': COLORS.WARNING,
                        'high': COLORS.ERROR,
                        'urgent': discord.Color.dark_red()
                    }
                    embed.color = colors.get(priority, COLORS.DEFAULT)
                    
                    # Update priority field
                    for i, field in enumerate(embed.fields):
                        if field.name == "Priority":
                            embed.remove_field(i)
                            break
                            
                    emoji = {
                        'low': '🟢',
                        'medium': '🟡',
                        'high': '🔴',
                        'urgent': '⚡'
                    }
                    
                    embed.add_field(
                        name="Priority",
                        value=f"{emoji.get(priority, '❓')} {priority.title()}",
                        inline=True
                    )
                    
                    await message.edit(embed=embed)
            
            # Send notification for high/urgent priority
            if priority in ['high', 'urgent'] and settings.get('notification_channel'):
                notif_channel = interaction.guild.get_channel(
                    int(settings['notification_channel'])
                )
                if notif_channel:
                    await notif_channel.send(
                        f"⚠️ Ticket {ticket_id} priority set to {priority.upper()}\n"
                        f"Channel: {interaction.channel.mention}"
                    )
            
            conn.commit()
            await interaction.followup.send(
                f"Ticket priority set to {priority}",
                ephemeral=True
            )
            
        except Exception as e:
            logger.error(f"Error setting priority: {e}")
            if conn:
                conn.rollback()
            await interaction.followup.send(
                "An error occurred while setting priority",
                ephemeral=True
            )
        finally:
            if conn:
                conn.close()

    async def check_inactive_tickets(self):
        """Auto-close inactive tickets"""
        while True:
            try:
                await asyncio.sleep(3600)  # Check every hour
                
                conn = get_connection()
                cursor = conn.cursor()
                
                # Get all guilds' settings
                cursor.execute("SELECT * FROM ticket_settings")
                guild_settings = cursor.fetchall()
                
                for settings in guild_settings:
                    auto_close_hours = settings['auto_close_hours']
                    guild_id = settings['guild_id']
                    
                    # Find inactive tickets
                    cursor.execute("""
                        SELECT id, channel_id 
                        FROM tickets 
                        WHERE guild_id = ? 
                          AND status = 'open'
                          AND last_activity < ?
                    """, (
                        guild_id,
                        datetime.utcnow() - timedelta(hours=auto_close_hours)
                    ))
                    
                    inactive_tickets = cursor.fetchall()
                    
                    for ticket in inactive_tickets:
                        try:
                            channel = self.bot.get_channel(int(ticket['channel_id']))
                            if channel:
                                await channel.send(
                                    "⚠️ This ticket has been inactive for "
                                    f"{auto_close_hours} hours and will be closed automatically."
                                )
                                await asyncio.sleep(5)
                                await channel.delete()
                                
                                # Update database
                                cursor.execute("""
                                    UPDATE tickets 
                                    SET status = 'closed',
                                        closed_at = CURRENT_TIMESTAMP,
                                        closed_by = ?,
                                        resolution = 'Auto-closed due to inactivity'
                                    WHERE id = ?
                                """, (str(self.bot.user.id), ticket['id']))
                                
                                # Remove from active tickets
                                if int(ticket['channel_id']) in self.active_tickets:
                                    del self.active_tickets[int(ticket['channel_id'])]
                                
                                # Log auto-close
                                if settings['log_channel_id']:
                                    log_channel = self.bot.get_channel(
                                        int(settings['log_channel_id'])
                                    )
                                    if log_channel:
                                        embed = Embed.create(
                                            title="Ticket Auto-Closed",
                                            description=f"Ticket {ticket['id']} was closed due to inactivity",
                                            color=COLORS.WARNING
                                        )
                                        await log_channel.send(embed=embed)
                                        
                        except Exception as e:
                            logger.error(f"Error auto-closing ticket {ticket['id']}: {e}")
                            continue
                            
                conn.commit()
                
            except Exception as e:
                logger.error(f"Error in inactive ticket check: {e}")
            finally:
                if conn:
                    conn.close()

    async def create_transcript(self, channel: discord.TextChannel) -> str:
        """Create a transcript of the ticket"""
        messages = []
        async for message in channel.history(limit=None, oldest_first=True):
            messages.append({
                'author': str(message.author),
                'content': message.content,
                'timestamp': message.created_at.strftime('%Y-%m-%d %H:%M:%S')
            })

        return json.dumps(messages, indent=2)

async def setup(bot):
    """Setup the Ticket cog"""
    await bot.add_cog(TicketSystem(bot))