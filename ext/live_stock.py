"""
Live Stock Manager
Author: fdyytu2
Created at: 2025-03-07 18:30:16 UTC
Last Modified: 2025-03-16 14:08:13 UTC
"""

import discord
from discord.ext import commands, tasks
import logging
import asyncio
from datetime import datetime
from typing import Optional, Dict
from discord import ui

from .constants import (
    COLORS,
    MESSAGES,
    UPDATE_INTERVAL,
    CACHE_TIMEOUT,
    Stock,
    Status,
    CURRENCY_RATES,
    COG_LOADED
)
from .base_handler import BaseLockHandler
from .cache_manager import CacheManager
from .product_manager import ProductManagerService
from .balance_manager import BalanceManagerService 
from .trx import TransactionManager, TransactionMonitor
from .admin_service import AdminService

class LiveStockStats:
    def __init__(self):
        self.total_updates = 0
        self.failed_updates = 0
        self.last_update_time = None
        self.average_update_time = 0
        self.performance_history = []
        self.max_history = 100

    def record_update(self, success: bool, update_time: float):
        self.total_updates += 1
        if not success:
            self.failed_updates += 1
        
        self.last_update_time = datetime.utcnow()
        self.performance_history.append(update_time)
        if len(self.performance_history) > self.max_history:
            self.performance_history.pop(0)
            
        self.average_update_time = sum(self.performance_history) / len(self.performance_history)

    def get_stats(self) -> Dict:
        return {
            'total_updates': self.total_updates,
            'failed_updates': self.failed_updates,
            'success_rate': ((self.total_updates - self.failed_updates) / self.total_updates * 100) if self.total_updates > 0 else 0,
            'last_update': self.last_update_time.strftime("%Y-%m-%d %H:%M:%S UTC") if self.last_update_time else None,
            'average_time': f"{self.average_update_time:.2f}s",
            'uptime': self.get_uptime()
        }

    def get_uptime(self) -> str:
        if not self.last_update_time:
            return "N/A"
        
        delta = datetime.utcnow() - self.last_update_time
        hours = delta.total_seconds() // 3600
        minutes = (delta.total_seconds() % 3600) // 60
        return f"{int(hours)}h {int(minutes)}m"

class LiveStockManager(BaseLockHandler):
    def __init__(self, bot):
        if not hasattr(self, 'initialized') or not self.initialized:
            super().__init__()
            self.bot = bot
            self.logger = logging.getLogger("LiveStockManager")
            self.cache_manager = CacheManager()
            self.stats = LiveStockStats()
            self.monitor = TransactionMonitor()

            self.product_service = ProductManagerService(bot)
            self.balance_service = BalanceManagerService(bot)
            self.trx_manager = TransactionManager(bot)
            self.admin_service = AdminService(bot)

            self.stock_channel_id = int(self.bot.config.get('id_live_stock', 0))
            self.current_stock_message = None
            self.button_manager = None
            self._ready = asyncio.Event()
            self.initialized = True
            self.logger.info("LiveStockManager initialized")

    async def initialize(self):
        if not self._ready.is_set():
            self._ready.set()
            self.logger.info("LiveStockManager is ready")

    async def find_last_message(self) -> Optional[discord.Message]:
        try:
            channel = self.bot.get_channel(self.stock_channel_id)
            if not channel:
                return None
                
            async for message in channel.history(limit=50):
                if (message.author == self.bot.user and 
                    len(message.embeds) > 0 and 
                    message.embeds[0].title and 
                    "Growtopia Shop Status" in message.embeds[0].title):
                    return message
            return None
        except Exception as e:
            self.logger.error(f"Error finding last message: {e}")
            return None

    async def set_button_manager(self, button_manager):
        self.button_manager = button_manager
        self.logger.info("Button manager set successfully")

    async def create_stock_embed(self) -> discord.Embed:
        try:
            monitor = TransactionMonitor()
            monitor.start()
            monitor.add_step("initializing_display")

            if await self.admin_service.is_maintenance_mode():
                return discord.Embed(
                    title="🔧 Sistem dalam Maintenance",
                    description=MESSAGES.INFO['MAINTENANCE'],
                    color=COLORS.WARNING,
                    timestamp=datetime.utcnow()
                )

            monitor.add_step("fetching_products")
            cache_key = 'all_products_display'
            cached_products = await self.cache_manager.get(cache_key)

            if not cached_products:
                product_response = await self.product_service.get_all_products()
                if not product_response.success:
                    raise ValueError(product_response.error)
                products = product_response.data
                await self.cache_manager.set(
                    cache_key,
                    products,
                    expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.SHORT)
                )
            else:
                products = cached_products

            monitor.add_step("creating_embed")
            embed = discord.Embed(
                title="🏪 Growtopia Shop Status",
                description=(
                    "```ansi\n"
                    "\u001b[0;37mSelamat datang di \u001b[0;33mGrowtopia Shop\u001b[0m!\n"
                    "\u001b[0;90mReal-time stock monitoring system\u001b[0m\n"
                    "```"
                ),
                color=discord.Color.from_rgb(32, 34, 37)
            )

            current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            embed.add_field(
                name="⏰ Server Time",
                value=f"```ansi\n\u001b[0;36m{current_time} UTC\u001b[0m```",
                inline=False
            )

            try:
                monitor.add_step("processing_categories")
                categories = {}
                for product in products:
                    category = product.get('category', 'Other')
                    if category not in categories:
                        categories[category] = []
                    categories[category].append(product)

                priority_order = ['Premium', 'DL/BGL', 'Items', 'Other']
                sorted_categories = sorted(
                    categories.items(),
                    key=lambda x: priority_order.index(x[0]) if x[0] in priority_order else len(priority_order)
                )

                for category, category_products in sorted_categories:
                    monitor.add_step(f"processing_category_{category}")
                    category_header = f"\n__**{category}**__\n"
                    category_items = []

                    sorted_products = sorted(
                        category_products,
                        key=lambda x: (x.get('priority', 999), -x.get('price', 0))
                    )

                    for product in sorted_products:
                        try:
                            product_code = product.get('code')
                            if not product_code:
                                continue

                            stock_cache_key = f'stock_count_{product_code}'
                            stock_count = await self.cache_manager.get(stock_cache_key)

                            if stock_count is None:
                                stock_response = await self.product_service.get_stock_count(product_code)
                                if not stock_response.success:
                                    continue
                                stock_count = stock_response.data
                                await self.cache_manager.set(
                                    stock_cache_key,
                                    stock_count,
                                    expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.SHORT)
                                )

                            if stock_count > Stock.ALERT_THRESHOLD:
                                status_color = "32"
                                status_emoji = "🟢"
                                stock_status = "In Stock"
                            elif stock_count > 0:
                                status_color = "33"
                                status_emoji = "🟡"
                                stock_status = "Limited"
                            else:
                                status_color = "31"
                                status_emoji = "🔴"
                                stock_status = "Out of Stock"

                            price = float(product['price'])
                            price_display = self._format_price(price)

                            product_code_display = f"[{product_code}]" if product.get('show_code', True) else ""
                            
                            product_info = (
                                f"```ansi\n"
                                f"{status_emoji} \u001b[0;{status_color}m{product['name']} {product_code_display}\u001b[0m\n"
                                f"└─ Price  : {price_display}\n"
                                f"└─ Stock  : {stock_count} unit ({stock_status})\n"
                            )

                            if product.get('description'):
                                product_info += f"└─ Info   : {product['description']}\n"
                            if product.get('discount'):
                                product_info += f"└─ Diskon : {product['discount']}% OFF!\n"
                            if product.get('limited'):
                                product_info += f"└─ Status : ⭐ Limited Edition!\n"
                            if product.get('bundle'):
                                product_info += f"└─ Bundle : 📦 {product['bundle']}\n"
                            if product.get('bonus'):
                                product_info += f"└─ Bonus  : 🎁 {product['bonus']}\n"

                            product_info += "```"
                            category_items.append(product_info)

                        except Exception as e:
                            self.logger.error(f"Error processing product {product.get('name', 'Unknown')}: {e}")
                            continue

                    if category_items:
                        items_text = "\n".join(category_items)
                        embed.add_field(
                            name=category_header,
                            value=items_text,
                            inline=False
                        )

                    monitor.add_step(f"completed_category_{category}")

            except Exception as e:
                self.logger.error(f"Error processing categories: {e}")
                raise

            monitor.add_step("finalizing")
            perf_data = monitor.get_report()
            total_time = perf_data.get('total_time', 0)
            
            embed.set_footer(
                text=(
                    f"Auto-update every {int(UPDATE_INTERVAL.LIVE_STOCK)} seconds • "
                    f"Generated in {total_time:.2f}s"
                )
            )
            embed.timestamp = datetime.utcnow()

            return embed

        except Exception as e:
            self.logger.error(f"Error creating stock embed: {e}")
            return discord.Embed(
                title="❌ System Error",
                description=MESSAGES.ERROR['DISPLAY_ERROR'],
                color=COLORS.ERROR
            )

    def _format_price(self, price: float) -> str:
        try:
            if not isinstance(price, (int, float)):
                raise ValueError(f"Invalid price type: {type(price)}")

            if price >= CURRENCY_RATES.RATES['BGL']:
                return f"\u001b[0;35m{price/CURRENCY_RATES.RATES['BGL']:.1f} BGL\u001b[0m"
            elif price >= CURRENCY_RATES.RATES['DL']:
                return f"\u001b[0;34m{price/CURRENCY_RATES.RATES['DL']:.0f} DL\u001b[0m"
            return f"\u001b[0;32m{int(price)} WL\u001b[0m"
        except Exception as e:
            self.logger.error(f"Error formatting price {price}: {e}")
            return "Invalid Price"

    async def update_stock_display(self) -> bool:
        self.monitor.start()
        success = False
        update_time = 0
        
        try:
            self.monitor.add_step("starting_update")
            channel = self.bot.get_channel(self.stock_channel_id)
            if not channel:
                raise ValueError(f"Channel stock dengan ID {self.stock_channel_id} tidak ditemukan")

            self.monitor.add_step("creating_embed")
            embed = await self.create_stock_embed()

            self.monitor.add_step("updating_message")
            if not self.current_stock_message:
                self.current_stock_message = await self.find_last_message()

            if not self.current_stock_message:
                view = self.button_manager.create_view() if self.button_manager else None
                self.current_stock_message = await channel.send(embed=embed, view=view)
                success = True
            else:
                try:
                    await self.current_stock_message.edit(embed=embed)
                    success = True
                except discord.NotFound:
                    self.logger.warning(MESSAGES.WARNING['MESSAGE_NOT_FOUND'])
                    self.current_stock_message = None
                    view = self.button_manager.create_view() if self.button_manager else None
                    self.current_stock_message = await channel.send(embed=embed, view=view)
                    success = True

            self.monitor.add_step("update_complete")
            perf_data = self.monitor.get_report()
            update_time = perf_data['total_time']
            
        except Exception as e:
            self.logger.error(f"Error updating stock display: {e}")
            success = False
        finally:
            self.stats.record_update(success, update_time)
            return success

    async def get_performance_embed(self) -> discord.Embed:
        stats = self.stats.get_stats()
        embed = discord.Embed(
            title="📊 LiveStock Performance Stats",
            color=COLORS.INFO,
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(
            name="Updates",
            value=f"```\nTotal: {stats['total_updates']}\nFailed: {stats['failed_updates']}\nSuccess Rate: {stats['success_rate']:.1f}%\n```",
            inline=False
        )
        
        embed.add_field(
            name="Timing",
            value=f"```\nAverage Time: {stats['average_time']}\nLast Update: {stats['last_update']}\nUptime: {stats['uptime']}\n```",
            inline=False
        )
        
        return embed

    async def cleanup(self):
        try:
            final_stats = self.stats.get_stats()
            self.logger.info(f"Final LiveStock stats: {final_stats}")
            
            if self.current_stock_message:
                embed = discord.Embed(
                    title="🔧 Maintenance",
                    description=MESSAGES.INFO['MAINTENANCE'],
                    color=COLORS.WARNING
                )
                await self.current_stock_message.edit(embed=embed)

            patterns = [
                'live_stock_*',
                'stock_count_*',
                'all_products_display'
            ]
            for pattern in patterns:
                await self.cache_manager.delete_pattern(pattern)

            self.logger.info("LiveStockManager cleanup completed")

        except Exception as e:
            self.logger.error(f"Error in cleanup: {e}")

class LiveStockCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.stock_manager = LiveStockManager(bot)
        self.logger = logging.getLogger("LiveStockCog")
        self._ready = asyncio.Event()
        self.update_stock_task = None
        self.stats_task = None
        self.logger.info("LiveStockCog instance created")
        asyncio.create_task(self.stock_manager.initialize())

    @commands.command(name="stockstats")
    @commands.has_permissions(administrator=True)
    async def stock_stats(self, ctx):
        try:
            embed = await self.stock_manager.get_performance_embed()
            await ctx.send(embed=embed)
        except Exception as e:
            self.logger.error(f"Error showing stats: {e}")
            await ctx.send("Error retrieving stats")

    async def start_tasks(self):
        try:
            self.logger.info("Starting stock update and stats tasks...")
            self.update_stock_task = self.update_stock.start()
            self.stats_task = self.update_stats.start()
            self.logger.info("All tasks started successfully")
        except Exception as e:
            self.logger.error(f"Failed to start tasks: {e}")
            raise

    async def cog_load(self):
        try:
            self.logger.info("LiveStockCog loading...")
            self._ready.set()
            self.bot.loop.create_task(self.delayed_setup())
            self.logger.info("LiveStockCog base loading complete")
        except Exception as e:
            self.logger.error(f"Error in cog_load: {e}")
            raise

    async def delayed_setup(self):
        try:
            self.logger.info("Starting delayed setup...")
            
            try:
                async with asyncio.timeout(30):
                    self.logger.info("Waiting for bot to be ready...")
                    await self.bot.wait_until_ready()
                    self.logger.info("Bot is ready, proceeding with initialization...")
            except asyncio.TimeoutError:
                self.logger.error("Timeout waiting for bot ready")
                return
    
            channel = self.bot.get_channel(self.stock_manager.stock_channel_id)
            if not channel:
                self.logger.error(f"Stock channel {self.stock_manager.stock_channel_id} not found")
                return
            
            await self.start_tasks()
            self.logger.info("LiveStockCog fully initialized")
    
        except Exception as e:
            self.logger.error(f"Error in delayed setup: {e}")

    async def cog_unload(self):
        try:
            if self.update_stock_task:
                self.update_stock_task.cancel()
            if self.stats_task:
                self.stats_task.cancel()
            await self.stock_manager.cleanup()
            self.logger.info("LiveStockCog unloaded")
        except Exception as e:
            self.logger.error(f"Error in cog_unload: {e}")

    @tasks.loop(seconds=UPDATE_INTERVAL.LIVE_STOCK)
    async def update_stock(self):
        if not self._ready.is_set():
            return

        try:
            await self.stock_manager.update_stock_display()
        except Exception as e:
            self.logger.error(f"Error in stock update loop: {e}")

    @tasks.loop(minutes=5.0)
    async def update_stats(self):
        if not self._ready.is_set():
            return

        try:
            stats = self.stock_manager.stats.get_stats()
            self.logger.info(f"LiveStock stats update: {stats}")
        except Exception as e:
            self.logger.error(f"Error updating stats: {e}")

    @update_stock.before_loop
    async def before_update_stock(self):
        await self.bot.wait_until_ready()

    @update_stats.before_loop
    async def before_update_stats(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    try:
        if not hasattr(bot, COG_LOADED['LIVE_STOCK']):
            cog = LiveStockCog(bot)
            await bot.add_cog(cog)
            
            try:
                async with asyncio.timeout(5):
                    await cog.stock_manager._ready.wait()
            except asyncio.TimeoutError:
                logging.error("Timeout waiting for stock manager initialization")
                raise RuntimeError("Stock manager initialization timeout")
            setattr(bot, COG_LOADED['LIVE_STOCK'], True)
            logging.info(f'LiveStock cog loaded at {datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")} UTC')
            return True
    except Exception as e:
        logging.error(f"Failed to load LiveStock cog: {e}")
        if hasattr(bot, COG_LOADED['LIVE_STOCK']):
            delattr(bot, COG_LOADED['LIVE_STOCK'])
        raise

async def teardown(bot):
    try:
        if hasattr(bot, COG_LOADED['LIVE_STOCK']):
            cog = bot.get_cog('LiveStockCog')
            if cog:
                await bot.remove_cog('LiveStockCog')
                if hasattr(cog, 'stock_manager'):
                    await cog.stock_manager.cleanup()
            delattr(bot, COG_LOADED['LIVE_STOCK'])
            logging.info("LiveStock extension unloaded successfully")
    except Exception as e:
        logging.error(f"Error unloading LiveStock extension: {e}")