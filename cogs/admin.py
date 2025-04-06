"""
Admin Commands Cog
Author: fdyytu1
Created at: 2025-03-14 01:59:43 UTC
Last Modified: 2025-03-14 02:10:00 UTC
"""

import discord
from discord.ext import commands
import logging
from datetime import datetime, timezone
import json
import asyncio
from typing import Optional, List, Dict, Any
import io
import os
import psutil
import platform
import hashlib

from ext.constants import (
    Status,              
    TransactionType,     
    Balance,            
    COLORS,             
    MESSAGES,           
    CURRENCY_RATES,     
    MAX_STOCK_FILE_SIZE,
    VALID_STOCK_FORMATS,
    Permissions,
    Stock 
)

# Import services
from ext.admin_service import AdminService
from ext.balance_manager import BalanceManagerService
from ext.product_manager import ProductManagerService
from ext.trx import TransactionManager
from ext.cache_manager import CacheManager
from ext.base_handler import BaseLockHandler, BaseResponseHandler
from utils.command_handler import AdvancedCommandHandler

class AdminCog(commands.Cog, BaseLockHandler, BaseResponseHandler):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.logger = logging.getLogger("AdminCog")
        self.PREFIX = "!"
        self.command_handler = AdvancedCommandHandler(bot)
        
        # Initialize services with proper error handling
        try:
            self.admin_service = AdminService(bot)
            self.balance_service = BalanceManagerService(bot)
            self.product_service = ProductManagerService(bot)
            self.trx_manager = TransactionManager(bot)
            self.cache_manager = CacheManager()
        except Exception as e:
            self.logger.critical(f"Failed to initialize services: {e}")
            raise

        # Load admin configuration
        self._load_config()

    def _load_config(self):
        """Load configuration with proper error handling"""
        try:
            with open('config.json') as f:
                config = json.load(f)
                self.admin_id = int(config.get('admin_id'))
                self.PREFIX = config.get('prefix', '!')
                if not self.admin_id:
                    raise ValueError("admin_id not found in config.json")
                self.logger.info(f"Admin ID loaded: {self.admin_id}")
        except Exception as e:
            self.logger.critical(f"Failed to load admin configuration: {e}")
            raise

    async def _process_command(self, ctx: commands.Context, command_name: str, execute_func) -> None:
        """Process command dengan error handling dan locking"""
        if not await self.acquire_response_lock(ctx):
            return
    
        try:
            # Execute the command function
            await execute_func()
            # Log command success 
            await self.command_handler.handle_command(ctx, command_name)  # Hapus parameter send_response
        except Exception as e:
            self.logger.error(f"Error executing {command_name}: {str(e)}")
            error_msg = str(e) if isinstance(e, ValueError) else "An error occurred while processing the command"
            await self.send_response_once(
                ctx,
                embed=discord.Embed(
                    title="❌ Error",
                    description=error_msg,
                    color=COLORS.ERROR
                )
            )
        finally:
            self.release_response_lock(ctx)
        
    @commands.command(name="addproduct")
    async def add_product(self, ctx, code: str, name: str, price: int, *, description: str = None):
        """Add new product"""
        async def execute():
            if price < Stock.MIN_PRICE:
                raise ValueError(f"Price cannot be lower than {Stock.MIN_PRICE}")
            
            if price > Stock.MAX_PRICE:
                raise ValueError(f"Price cannot be higher than {Stock.MAX_PRICE:,}")
                
            response = await self.product_service.create_product(
                code=code.upper(),
                name=name,
                price=price,
                description=description
            )
            
            if not response.success:
                raise ValueError(response.error)
                
            embed = discord.Embed(
                title="✅ Product Added",
                color=COLORS.SUCCESS,
                timestamp=datetime.now(timezone.utc)
            )
            
            embed.add_field(
                name="Details",
                value=(
                    f"```yml\n"
                    f"Code: {code.upper()}\n"
                    f"Name: {name}\n"
                    f"Price: {price:,} WLS\n"
                    f"Description: {description or 'N/A'}\n"
                    f"```"
                ),
                inline=False
            )
            
            embed.set_footer(text=f"Added by {ctx.author}")
            await self.send_response_once(ctx, embed=embed)
            
        await self._process_command(ctx, "addproduct", execute)

    @commands.command(name="editproduct")
    async def edit_product(self, ctx, code: str, field: str, *, value: str):
        """Edit product details"""
        async def execute():
            # Check if product exists
            product = await self.product_service.get_product(code.upper())
            if not product:
                raise ValueError(f"Product with code {code.upper()} not found")
                
            valid_fields = ['name', 'price', 'description']
            field = field.lower()
            
            if field not in valid_fields:
                raise ValueError(f"Invalid field. Use: {', '.join(valid_fields)}")
                
            if field == 'price':
                try:
                    price = int(value)
                    if price < Stock.MIN_PRICE:
                        raise ValueError(f"Price cannot be lower than {Stock.MIN_PRICE}")
                    if price > Stock.MAX_PRICE:
                        raise ValueError(f"Price cannot be higher than {Stock.MAX_PRICE:,}")
                except ValueError:
                    raise ValueError("Price must be a number")
                    
            response = await self.product_service.update_product(
                code=code.upper(),
                field=field,
                value=value if field != 'price' else int(value),
                updated_by=str(ctx.author)
            )
            
            if not response.success:
                raise ValueError(response.error)
                
            embed = discord.Embed(
                title="✅ Product Updated",
                color=COLORS.SUCCESS,
                timestamp=datetime.now(timezone.utc)
            )
            
            embed.add_field(
                name="Details",
                value=(
                    f"```yml\n"
                    f"Code: {code.upper()}\n"
                    f"Updated Field: {field}\n"
                    f"New Value: {value}\n"
                    f"```"
                ),
                inline=False
            )
            
            embed.set_footer(text=f"Updated by {ctx.author}")
            await self.send_response_once(ctx, embed=embed)
            
        await self._process_command(ctx, "editproduct", execute)

    @commands.command(name="deleteproduct")
    async def delete_product(self, ctx, code: str):
        """Delete product"""
        async def execute():
            if not await self._confirm_action(
                ctx,
                f"Are you sure you want to delete product {code.upper()}?"
            ):
                raise ValueError("Operation cancelled by user")
                
            response = await self.product_service.delete_product(
                code=code.upper(),
                deleted_by=str(ctx.author)
            )
            
            if not response.success:
                raise ValueError(response.error)
                
            embed = discord.Embed(
                title="✅ Product Deleted",
                description=f"Product {code.upper()} has been deleted",
                color=COLORS.SUCCESS,
                timestamp=datetime.now(timezone.utc)
            )
            
            embed.set_footer(text=f"Deleted by {ctx.author}")
            await self.send_response_once(ctx, embed=embed)
            
        await self._process_command(ctx, "deleteproduct", execute)

    @commands.command(name="addstock")
    async def add_stock(self, ctx, code: str):
        """Add stock with file attachment"""
        async def execute():
            # Check if product exists
            product = await self.product_service.get_product(code.upper())
            if not product:
                raise ValueError(f"Product with code {code.upper()} not found")
                
            if not ctx.message.attachments:
                raise ValueError("Please attach a file containing stock items")
                
            attachment = ctx.message.attachments[0]
            if attachment.size > MAX_STOCK_FILE_SIZE:
                raise ValueError(f"File too large. Maximum size: {MAX_STOCK_FILE_SIZE/1024/1024}MB")
                
            if not any(attachment.filename.endswith(ext) for ext in VALID_STOCK_FORMATS):
                raise ValueError(f"Invalid file format. Use: {', '.join(VALID_STOCK_FORMATS)}")
                
            content = await attachment.read()
            content = content.decode('utf-8').strip().split('\n')
            
            # Process each stock item
            added_count = 0
            failed_items = []
            
            for item in content:
                response = await self.product_service.add_stock_item(
                    product_code=code.upper(),
                    content=item.strip(),
                    added_by=str(ctx.author)
                )
                
                if response.success:
                    added_count += 1
                else:
                    failed_items.append(f"{item}: {response.error}")
            
            # Get current stock count
            stock_count = await self.product_service.get_stock_count(code.upper())
            if not stock_count.success:
                raise ValueError(stock_count.error)
            
            embed = discord.Embed(
                title="✅ Stock Added",
                color=COLORS.SUCCESS,
                timestamp=datetime.now(timezone.utc)
            )
            
            embed.add_field(
                name="Summary",
                value=(
                    f"```yml\n"
                    f"Product: {code.upper()}\n"
                    f"Successfully Added: {added_count}/{len(content)}\n"
                    f"Current Total Stock: {stock_count.data}\n"
                    f"```"
                ),
                inline=False
            )
            
            if failed_items:
                failed_text = "\n".join(failed_items[:5])  # Show first 5 failures
                if len(failed_items) > 5:
                    failed_text += f"\n... and {len(failed_items) - 5} more"
                
                embed.add_field(
                    name="Failed Items",
                    value=f"```\n{failed_text}\n```",
                    inline=False
                )
            
            embed.set_footer(text=f"Added by {ctx.author}")
            await self.send_response_once(ctx, embed=embed)
            
        await self._process_command(ctx, "addstock", execute)

    @commands.command(name="addworld")
    async def add_world(self, ctx, name: str, *, description: str = None):
        """Add/Update world information"""
        async def execute():
            response = await self.product_service.update_world_info(
                world=name.upper(),
                owner=str(ctx.author),
                bot=str(self.bot.user)
            )
            
            if not response.success:
                raise ValueError(response.error)
                
            embed = discord.Embed(
                title="✅ World Updated",
                color=COLORS.SUCCESS,
                timestamp=datetime.now(timezone.utc)
            )
            
            embed.add_field(
                name="Details",
                value=(
                    f"```yml\n"
                    f"World: {name.upper()}\n"
                    f"Owner: {ctx.author}\n"
                    f"Bot: {self.bot.user}\n"
                    f"```"
                ),
                inline=False
            )
            
            embed.set_footer(text=f"Updated by {ctx.author}")
            await self.send_response_once(ctx, embed=embed)
            
        await self._process_command(ctx, "addworld", execute)
    
    @commands.command(name="addbal")
    async def add_balance(self, ctx, growid: str, amount: str, currency: str):
        """Add balance to user"""
        async def execute():
            try:
                # Validasi currency
                currency_upper = currency.upper()
                if currency_upper not in CURRENCY_RATES.SUPPORTED:
                    raise ValueError(f"Mata uang tidak valid. Gunakan: {', '.join(CURRENCY_RATES.SUPPORTED)}")
                
                # Convert amount ke integer
                try:
                    amount_int = int(amount.replace(',', ''))
                except ValueError:
                    raise ValueError("Jumlah harus berupa angka")
                
                # Validasi jumlah
                if amount_int <= 0:
                    raise ValueError("Jumlah harus positif!")
    
                # Validasi batasan jumlah berdasarkan currency
                if amount_int < CURRENCY_RATES.MIN_AMOUNTS[currency_upper]:
                    raise ValueError(f"Minimal {CURRENCY_RATES.MIN_AMOUNTS[currency_upper]:,} {currency_upper}")
                    
                if amount_int > CURRENCY_RATES.MAX_AMOUNTS[currency_upper]:
                    raise ValueError(f"Maksimal {CURRENCY_RATES.MAX_AMOUNTS[currency_upper]:,} {currency_upper}")
    
                # Setup balance parameters berdasarkan currency
                balance_params = {
                    'wl': 0,
                    'dl': 0,
                    'bgl': 0
                }
                
                if currency_upper == 'WL':
                    balance_params['wl'] = amount_int
                elif currency_upper == 'DL':
                    balance_params['dl'] = amount_int
                elif currency_upper == 'BGL':
                    balance_params['bgl'] = amount_int
                    
                # Update balance dengan parameter yang sesuai
                response = await self.balance_service.update_balance(
                    growid=growid,
                    **balance_params,  # Unpack parameters wl, dl, bgl
                    details=f"Added {amount_int:,} {currency_upper} by admin {ctx.author}",
                    transaction_type=TransactionType.ADMIN_ADD.value  # Tambahkan .value di sini
                )
                
                if not response.success:
                    raise ValueError(response.error)
                
                # Buat embed response
                embed = discord.Embed(
                    title="✅ Balance Added",
                    color=COLORS.SUCCESS,
                    timestamp=datetime.now(timezone.utc)
                )
                
                embed.add_field(
                    name="💰 Balance Details",
                    value=(
                        f"```yml\n"
                        f"GrowID: {growid}\n"
                        f"Added: {amount_int:,} {currency_upper}\n"
                        f"New Balance: {response.data.format()}\n"
                        f"```"
                    ),
                    inline=False
                )
                
                embed.set_footer(text=f"Added by {ctx.author}")
                await self.send_response_once(ctx, embed=embed)
                
            except Exception as e:
                raise ValueError(str(e))
    
        await self._process_command(ctx, "addbal", execute)
        
        @commands.command(name="removebal")
        async def remove_balance(self, ctx, growid: str, amount: str, currency: str):
            """Remove balance from user"""
            async def execute():
                try:
                    currency_upper = currency.upper()
                    if currency_upper not in CURRENCY_RATES.SUPPORTED:
                        raise ValueError(f"Invalid currency. Use: {', '.join(CURRENCY_RATES.SUPPORTED)}")
                    
                    try:
                        amount = int(amount.replace(',', ''))
                    except ValueError:
                        raise ValueError("Amount must be a number")
                        
                    if amount <= 0:
                        raise ValueError("Amount must be positive!")
    
                    # Get current balance first
                    balance_check = await self.balance_service.get_balance(growid)
                    if not balance_check.success:
                        raise ValueError(balance_check.error)
                    
                    # Convert to WL using CURRENCY_RATES method
                    wls = CURRENCY_RATES.to_wl(amount, currency_upper)
                    
                    # Check if user has enough balance
                    current_balance = balance_check.data
                    if wls > current_balance.total_wl():
                        raise ValueError(MESSAGES.ERROR['INSUFFICIENT_BALANCE'])
    
                    response = await self.balance_service.update_balance(
                        growid=growid,
                        wl=-wls,  # Use negative value for removal
                        details=f"Removed {amount:,} {currency_upper} by admin {ctx.author}",
                        transaction_type=TransactionType.ADMIN_REMOVE
                    )
                    
                    if not response.success:
                        raise ValueError(response.error)
                        
                    embed = discord.Embed(
                        title="✅ Balance Removed",
                        color=COLORS.SUCCESS,
                        timestamp=datetime.now(timezone.utc)
                    )
                    
                    embed.add_field(
                        name="💰 Balance Details",
                        value=(
                            f"```yml\n"
                            f"GrowID: {growid}\n"
                            f"Removed: {amount:,} {currency_upper}\n"
                            f"New Balance: {response.data.format()}\n"
                            f"```"
                        ),
                        inline=False
                    )
                    
                    embed.set_footer(text=f"Removed by {ctx.author}")
                    await self.send_response_once(ctx, embed=embed)
                    
                except Exception as e:
                    raise ValueError(str(e))
                    
            await self._process_command(ctx, "removebal", execute)

    @commands.command(name="checkbal")
    async def check_balance(self, ctx, growid: str):
        """Check user balance"""
        async def execute():
            balance_response = await self.balance_service.get_balance(growid)
            if not balance_response.success:
                raise ValueError(balance_response.error)

            # Get transaction history
            trx_response = await self.trx_manager.get_transaction_history(
                user_id=growid,
                limit=5
            )

            embed = discord.Embed(
                title=f"👤 User Information - {growid}",
                color=COLORS.INFO,
                timestamp=datetime.now(timezone.utc)
            )
            
            embed.add_field(
                name="💰 Current Balance",
                value=f"```yml\n{balance_response.data.format()}\n```",
                inline=False
            )

            if trx_response.success and trx_response.data:
                transactions = trx_response.data['transactions']
                recent_tx = "\n".join([
                    f"• {tx['type']} - {tx['formatted_date']}: {tx['amount_display']}"
                    for tx in transactions[:5]
                ])
                embed.add_field(
                    name="📝 Recent Transactions",
                    value=f"```yml\n{recent_tx}\n```",
                    inline=False
                )

            embed.set_footer(text=f"Checked by {ctx.author}")
            await self.send_response_once(ctx, embed=embed)
            
        await self._process_command(ctx, "checkbal", execute)

    @commands.command(name="resetuser")
    async def reset_user(self, ctx, growid: str):
        """Reset user balance"""
        async def execute():
            if not await self._confirm_action(
                ctx,
                f"Are you sure you want to reset {growid}'s balance? This action cannot be undone."
            ):
                raise ValueError("Operation cancelled by user")

            response = await self.balance_service.update_balance(
                growid=growid,
                wl=0,
                dl=0,
                bgl=0,
                details=f"Balance reset by admin {ctx.author}",
                transaction_type=TransactionType.ADMIN_RESET
            )

            if not response.success:
                raise ValueError(response.error)
                
            embed = discord.Embed(
                title="✅ Balance Reset",
                color=COLORS.SUCCESS,
                timestamp=datetime.now(timezone.utc)
            )
            
            embed.add_field(
                name="Details",
                value=f"```yml\nGrowID: {growid}\nNew Balance: 0 WL\n```",
                inline=False
            )
            
            embed.set_footer(text=f"Reset by {ctx.author}")
            await self.send_response_once(ctx, embed=embed)
            
        await self._process_command(ctx, "resetuser", execute)

    @commands.command(name="trxhistory")
    async def transaction_history(self, ctx, growid: str, limit: int = 10):
        """View transaction history"""
        async def execute():
            if limit < 1 or limit > 50:
                limit = min(max(1, limit), 50)

            response = await self.trx_manager.get_transaction_history(
                user_id=growid,
                limit=limit
            )

            if not response.success:
                raise ValueError(response.error)

            if not response.data['transactions']:
                raise ValueError("No transactions found")

            transactions = response.data['transactions']
            current_page = response.data['current_page']
            total_pages = response.data['total_pages']

            embed = discord.Embed(
                title=f"📜 Transaction History - {growid}",
                color=COLORS.INFO,
                timestamp=datetime.now(timezone.utc)
            )

            for tx in transactions:
                embed.add_field(
                    name=f"{tx['type']} - {tx['formatted_date']}",
                    value=(
                        f"```yml\n"
                        f"Amount: {tx['amount_display']}\n"
                        f"Details: {tx.get('details', 'No details')}\n"
                        f"```"
                    ),
                    inline=False
                )

            embed.set_footer(text=f"Page {current_page}/{total_pages} • Showing {len(transactions)} transactions")
            await self.send_response_once(ctx, embed=embed)
            
        await self._process_command(ctx, "trxhistory", execute)

    @commands.command(name="stockhistory")
    async def stock_history(self, ctx, code: str, limit: int = 10):
        """View stock history"""
        async def execute():
            if limit < 1 or limit > 50:
                limit = min(max(1, limit), 50)

            response = await self.product_service.get_stock_history(
                code=code.upper(),
                limit=limit
            )

            if not response.success:
                raise ValueError(response.error)

            history = response.data
            if not history:
                raise ValueError("No stock history found")

            embed = discord.Embed(
                title=f"📦 Stock History - {code.upper()}",
                color=COLORS.INFO,
                timestamp=datetime.now(timezone.utc)
            )

            for entry in history:
                status_emoji = {
                    Status.AVAILABLE.value: "🟢",
                    Status.SOLD.value: "💰",
                    Status.DELETED.value: "🗑️"
                }.get(entry['status'], "❓")
                
                embed.add_field(
                    name=f"{status_emoji} {entry['action']} - {entry['timestamp']}",
                    value=(
                        f"```yml\n"
                        f"Status: {entry['status']}\n"
                        f"By: {entry['by']}\n"
                        f"Details: {entry.get('details', 'No details')}\n"
                        f"```"
                    ),
                    inline=False
                )

            embed.set_footer(text=f"Showing {len(history)} entries • Current time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
            await self.send_response_once(ctx, embed=embed)
            
        await self._process_command(ctx, "stockhistory", execute)

    @commands.command(name="systeminfo")
    async def system_info(self, ctx):
        """Show bot system information"""
        async def execute():
            # Get system info
            cpu_usage = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            # Get bot info
            uptime = datetime.now(timezone.utc) - self.bot.start_time
            
            embed = discord.Embed(
                title="🤖 System Information",
                color=COLORS.INFO,
                timestamp=datetime.now(timezone.utc)
            )
            
            # System Stats
            embed.add_field(
                name="💻 System Resources",
                value=(
                    f"```yml\n"
                    f"OS: {platform.system()} {platform.release()}\n"
                    f"CPU Usage: {cpu_usage}%\n"
                    f"Memory: {memory.used/1024/1024/1024:.1f}GB/{memory.total/1024/1024/1024:.1f}GB ({memory.percent}%)\n"
                    f"Disk: {disk.used/1024/1024/1024:.1f}GB/{disk.total/1024/1024/1024:.1f}GB ({disk.percent}%)\n"
                    f"Python: {platform.python_version()}\n"
                    f"```"
                ),
                inline=False
            )
            
            # Bot Stats
            embed.add_field(
                name="🤖 Bot Status",
                value=(
                    f"```yml\n"
                    f"Uptime: {str(uptime).split('.')[0]}\n"
                    f"Latency: {round(self.bot.latency * 1000)}ms\n"
                    f"Servers: {len(self.bot.guilds)}\n"
                    f"Commands: {len(self.bot.commands)}\n"
                    f"```"
                ),
                inline=False
            )
            
            # Cache Stats
            cache_stats = await self.cache_manager.get_stats()
            embed.add_field(
                name="📊 Cache Statistics",
                value=(
                    f"```yml\n"
                    f"Items: {cache_stats['items']}\n"
                    f"Hit Rate: {cache_stats['hit_rate']:.1f}%\n"
                    f"Memory Usage: {cache_stats['memory_usage']:.1f}MB\n"
                    f"```"
                ),
                inline=False
            )
            
            await self.send_response_once(ctx, embed=embed)
            
        await self._process_command(ctx, "systeminfo", execute)

    @commands.command(name="maintenance")
    async def maintenance(self, ctx, mode: str):
        """Toggle maintenance mode"""
        async def execute():
            mode_lower = mode.lower()
            if mode_lower not in ['on', 'off']:
                raise ValueError("Please specify 'on' or 'off'")

            enabled = mode_lower == 'on'
            if enabled and not await self._confirm_action(
                ctx,
                "Are you sure you want to enable maintenance mode? This will restrict user access."
            ):
                raise ValueError("Operation cancelled by user")

            response = await self.admin_service.set_maintenance_mode(
                enabled=enabled,
                reason="System maintenance" if enabled else None,
                admin=str(ctx.author)
            )

            if not response.success:
                raise ValueError(response.error)

            embed = discord.Embed(
                title="🔧 Maintenance Mode",
                description=f"Maintenance mode has been turned **{mode_lower.upper()}**",
                color=COLORS.WARNING if enabled else COLORS.SUCCESS,
                timestamp=datetime.now(timezone.utc)
            )
            
            embed.set_footer(text=f"Changed by {ctx.author}")
            await self.send_response_once(ctx, embed=embed)

            if enabled:
                await self._notify_maintenance(ctx)

        await self._process_command(ctx, "maintenance", execute)

    @commands.command(name="blacklist")
    async def blacklist(self, ctx, action: str, growid: str):
        """Manage blacklisted users"""
        async def execute():
            action_lower = action.lower()
            if action_lower not in ['add', 'remove']:
                raise ValueError("Please specify 'add' or 'remove'")

            if action_lower == 'add':
                if not await self._confirm_action(
                    ctx,
                    f"Are you sure you want to blacklist {growid}?"
                ):
                    raise ValueError("Operation cancelled by user")

                response = await self.admin_service.add_to_blacklist(
                    growid=growid,
                    added_by=str(ctx.author)
                )
            else:
                response = await self.admin_service.remove_from_blacklist(
                    growid=growid,
                    removed_by=str(ctx.author)
                )

            if not response.success:
                raise ValueError(response.error)

            embed = discord.Embed(
                title="⛔ Blacklist Updated",
                description=(
                    f"User {growid} has been "
                    f"{'added to' if action_lower == 'add' else 'removed from'} "
                    f"the blacklist."
                ),
                color=COLORS.ERROR if action_lower == 'add' else COLORS.SUCCESS,
                timestamp=datetime.now(timezone.utc)
            )
            
            embed.set_footer(text=f"Updated by {ctx.author}")
            await self.send_response_once(ctx, embed=embed)

        await self._process_command(ctx, "blacklist", execute)

    async def _notify_maintenance(self, ctx):
        """Notify online users about maintenance mode"""
        for guild in self.bot.guilds:
            for member in guild.members:
                if not member.bot and member.status != discord.Status.offline:
                    try:
                        await member.send(
                            embed=discord.Embed(
                                title="⚠️ Maintenance Mode",
                                description=(
                                    "The bot is entering maintenance mode. "
                                    "Some features may be unavailable. "
                                    "We'll notify you when service is restored."
                                ),
                                color=COLORS.WARNING
                            )
                        )
                    except Exception as e:
                        self.logger.error(f"Failed to notify member {member.id}: {e}")

    async def _confirm_action(self, ctx: commands.Context, message: str) -> bool:
        """Ask for confirmation before proceeding with action"""
        embed = discord.Embed(
            title="⚠️ Confirmation Required",
            description=message,
            color=COLORS.WARNING
        )
        embed.set_footer(text="Reply with 'yes' to confirm or 'no' to cancel")
        
        confirm_msg = await ctx.send(embed=embed)
        
        try:
            response = await self.bot.wait_for(
                'message',
                check=lambda m: (
                    m.author == ctx.author and
                    m.channel == ctx.channel and
                    m.content.lower() in ['yes', 'no']
                ),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            await confirm_msg.delete()
            raise ValueError("Confirmation timed out")
            
        await confirm_msg.delete()
        return response.content.lower() == 'yes'

async def setup(bot):
    """Setup the Admin cog"""
    try:
        await bot.add_cog(AdminCog(bot))
        logging.info(
            f'Admin cog loaded successfully at {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")} UTC'
        )
    except Exception as e:
        logging.error(f"Failed to load Admin cog: {e}")
        raise