"""
Live Buttons Manager with Shop Integration
Version: 2.1.1
Author: fdygt
Created at: 2025-03-16 17:27:53 UTC
Last Modified: 2025-04-08 08:36:45 UTC

Dependencies:
- ext.product_manager: For product operations
- ext.balance_manager: For balance operations
- ext.trx: For transaction operations
- ext.admin_service: For maintenance mode
- ext.base_handler: For lock and response handling
- ext.constants: For configuration and responses
"""

import discord
from discord.ext import commands, tasks
from discord import ui
import logging
import asyncio
from datetime import datetime
from typing import List, Dict, Optional, Union
from discord.ui import Select, Button, View, Modal, TextInput

from .constants import (
    COLORS,
    MESSAGES,
    BUTTON_IDS,
    CACHE_TIMEOUT,
    Stock,
    Status,
    CURRENCY_RATES,
    UPDATE_INTERVAL,
    COG_LOADED,
    TransactionType,
    Balance
)

from .base_handler import BaseLockHandler, BaseResponseHandler
from .cache_manager import CacheManager
from .product_manager import ProductManagerService
from .balance_manager import BalanceManagerService
from .trx import TransactionManager
from .admin_service import AdminService

logger = logging.getLogger(__name__)

class PurchaseModal(discord.ui.Modal, BaseResponseHandler):
    def __init__(self, products: List[Dict], balance_service, product_service, trx_manager, cache_manager):
        super().__init__(title="🛍️ Pembelian Produk")
        self.products_cache = {p['code']: p for p in products}
        self.balance_service = balance_service
        self.product_service = product_service
        self.trx_manager = trx_manager
        self.cache_manager = cache_manager
        BaseResponseHandler.__init__(self)

        product_list = "\n".join([
            f"{p['name']} ({p['code']}) - {p['price']:,} WL | Stok: {p['stock']}"
            for p in products
        ])

        self.product_info = discord.ui.TextInput(
            label="Daftar Produk",
            style=discord.TextStyle.paragraph,
            default=product_list,
            required=False,
            custom_id="product_info"
        )

        self.product_code = discord.ui.TextInput(
            label="Kode Produk",
            style=discord.TextStyle.short,
            placeholder="Masukkan kode produk",
            required=True,
            min_length=1,
            max_length=10,
            custom_id="product_code"
        )

        self.quantity = discord.ui.TextInput(
            label="Jumlah",
            style=discord.TextStyle.short,
            placeholder="Masukkan jumlah (1-999)",
            required=True,
            min_length=1,
            max_length=3,
            custom_id="quantity"
        )

        self.add_item(self.product_info)
        self.add_item(self.product_code)
        self.add_item(self.quantity)

    async def on_submit(self, interaction: discord.Interaction):
        response_sent = False
        try:
            # Rate limit check with improved error handling
            rate_limit_key = f"purchase_limit_{interaction.user.id}"
            if await self.cache_manager.get(rate_limit_key):
                raise ValueError(MESSAGES.ERROR['RATE_LIMIT'])

            await interaction.response.defer(ephemeral=True)
            response_sent = True

            # Set rate limit with retry mechanism
            retry_count = 0
            while retry_count < 3:
                try:
                    await self.cache_manager.set(
                        rate_limit_key,
                        True,
                        expires_in=300  # 5 menit cooldown
                    )
                    break
                except Exception as e:
                    retry_count += 1
                    if retry_count == 3:
                        self.logger.error(f"Failed to set rate limit after 3 attempts: {e}")
                    await asyncio.sleep(1)

            # Validate input with improved validation
            product_code = self.product_code.value.strip().upper()
            try:
                quantity = int(self.quantity.value)
                if quantity <= 0 or quantity > 999:
                    raise ValueError(MESSAGES.ERROR['INVALID_AMOUNT'])
            except ValueError:
                raise ValueError(MESSAGES.ERROR['INVALID_AMOUNT'])

            # Get GrowID with timeout
            try:
                async with asyncio.timeout(10):
                    growid_response = await self.balance_service.get_growid(str(interaction.user.id))
                    if not growid_response.success:
                        raise ValueError(growid_response.error)
                    growid = growid_response.data
            except asyncio.TimeoutError:
                raise ValueError(MESSAGES.ERROR['TIMEOUT'])

            # Process purchase dengan queue untuk transaksi besar
            if quantity > 10:  # Threshold untuk queue
                await self.trx_manager.transaction_queue.add_transaction({
                    'type': TransactionType.PURCHASE.value,
                    'user_id': str(interaction.user.id),
                    'product_code': product_code,
                    'quantity': quantity,
                    'timestamp': datetime.utcnow().isoformat()
                })
                embed = discord.Embed(
                    title="⏳ Transaksi Diproses",
                    description="Transaksi Anda sedang diproses. Anda akan mendapat notifikasi setelah selesai.",
                    color=COLORS.WARNING
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Process purchase untuk transaksi normal dengan timeout
            try:
                async with asyncio.timeout(30):
                    purchase_response = await self.trx_manager.process_purchase(
                        buyer_id=str(interaction.user.id),
                        product_code=product_code,
                        quantity=quantity
                    )
            except asyncio.TimeoutError:
                raise ValueError(MESSAGES.ERROR['TRANSACTION_TIMEOUT'])

            if not purchase_response.success:
                raise ValueError(purchase_response.error)

            # Create success embed
            embed = discord.Embed(
                title="✅ Pembelian Berhasil",
                color=COLORS.SUCCESS,
                timestamp=datetime.utcnow()
            )

            # Add transaction details
            if purchase_response.data:
                product_data = purchase_response.data.get('product', {})
                embed.description = (
                    f"Berhasil membeli {quantity}x {product_data.get('name', '')}\n"
                    f"Total: {purchase_response.data.get('total_price', 0):,} WL"
                )

                if 'content' in purchase_response.data:
                    content_text = "\n".join(purchase_response.data['content'])
                    embed.add_field(
                        name="Detail Produk",
                        value=f"```\n{content_text}\n```",
                        inline=False
                    )

            # Add balance info
            if purchase_response.balance_response and purchase_response.balance_response.data:
                embed.add_field(
                    name="Saldo Tersisa",
                    value=f"```yml\n{purchase_response.balance_response.data.format()}```",
                    inline=False
                )

            # Add performance info if available
            if hasattr(purchase_response, 'performance') and purchase_response.performance:
                perf_data = purchase_response.performance
                perf_text = f"Processing Time: {perf_data.get('total_time', 0):.2f}s"
                embed.set_footer(text=perf_text)

            # Invalidate related caches with retry mechanism
            cache_keys = [
                f"balance_{growid}",
                f"stock_{product_code}",
                f"history_{interaction.user.id}"
            ]
            
            for key in cache_keys:
                retry_count = 0
                while retry_count < 3:
                    try:
                        await self.cache_manager.delete(key)
                        break
                    except Exception as e:
                        retry_count += 1
                        if retry_count == 3:
                            self.logger.error(f"Failed to invalidate cache {key}: {e}")
                        await asyncio.sleep(1)

            await interaction.followup.send(embed=embed, ephemeral=True)

        except ValueError as e:
            error_embed = discord.Embed(
                title="❌ Error",
                description=str(e),
                color=COLORS.ERROR,
                timestamp=datetime.utcnow()
            )
            if response_sent:
                await interaction.followup.send(embed=error_embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=error_embed, ephemeral=True)

        except Exception as e:
            self.logger.error(f"Error processing purchase: {e}")

            # Error recovery untuk transaksi gagal dengan timeout protection
            if 'purchase_response' in locals() and hasattr(purchase_response, 'data'):
                try:
                    async with asyncio.timeout(10):
                        await self.trx_manager.recover_failed_transaction(
                            purchase_response.data.get('transaction_id')
                        )
                except asyncio.TimeoutError:
                    self.logger.error("Transaction recovery timed out")
                except Exception as recovery_error:
                    self.logger.error(f"Error in transaction recovery: {recovery_error}")

            error_embed = discord.Embed(
                title="❌ Error",
                description=MESSAGES.ERROR['TRANSACTION_FAILED'],
                color=COLORS.ERROR
            )
            if response_sent:
                await interaction.followup.send(embed=error_embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=error_embed, ephemeral=True)

class RegisterModal(Modal, BaseResponseHandler):
    def __init__(self, balance_service: BalanceManagerService, existing_growid=None):
        title = "📝 Update GrowID" if existing_growid else "📝 Pendaftaran GrowID"
        super().__init__(title=title)
        BaseResponseHandler.__init__(self)

        self.balance_service = balance_service
        self.existing_growid = existing_growid
        self.logger = logging.getLogger("RegisterModal")

        self.growid = TextInput(
            label="GrowID Anda",
            placeholder="Contoh: NAMA_GROW_ID (3-30 karakter)" if not existing_growid else f"GrowID saat ini: {existing_growid}",
            min_length=3,
            max_length=30,
            required=True,
            style=discord.TextStyle.short
        )
        self.add_item(self.growid)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Defer response dengan timeout protection
            try:
                async with asyncio.timeout(5):
                    await interaction.response.defer(ephemeral=True)
            except asyncio.TimeoutError:
                raise ValueError(MESSAGES.ERROR['TIMEOUT'])

            # Basic validation dengan improved checking
            growid = str(self.growid.value).strip()
            if not growid.replace('_', '').isalnum():
                raise ValueError(MESSAGES.ERROR['INVALID_GROWID_FORMAT'])

            # Register user dengan timeout protection
            try:
                async with asyncio.timeout(20):
                    register_response = await self.balance_service.register_user(
                        str(interaction.user.id),
                        growid
                    )
            except asyncio.TimeoutError:
                raise ValueError(MESSAGES.ERROR['REGISTRATION_TIMEOUT'])

            if not register_response.success:
                # Handle specific error cases from balance_manager
                if register_response.error == MESSAGES.ERROR['LOCK_ACQUISITION_FAILED']:
                    await interaction.followup.send(
                        embed=discord.Embed(
                            title="⏳ Mohon Tunggu",
                            description="Sistem sedang memproses registrasi lain. Silakan coba beberapa saat lagi.",
                            color=COLORS.WARNING
                        ),
                        ephemeral=True
                    )
                    return

                raise ValueError(register_response.error)

            # Format success embed
            embed = discord.Embed(
                title="✅ GrowID Berhasil " + ("Diperbarui" if self.existing_growid else "Didaftarkan"),
                description=register_response.message or self.format_success_message(growid),
                color=COLORS.SUCCESS,
                timestamp=datetime.utcnow()
            )

            # Add balance info if available in response
            if register_response.data:
                if isinstance(register_response.data, dict):
                    if 'balance' in register_response.data:
                        embed.add_field(
                            name="Saldo Awal",
                            value=f"```yml\n{register_response.data['balance'].format()}```",
                            inline=False
                        )

            embed.set_footer(text="Gunakan tombol 💰 Saldo untuk melihat saldo Anda")
            await interaction.followup.send(embed=embed, ephemeral=True)

        except ValueError as e:
            error_embed = discord.Embed(
                title="❌ Error",
                description=str(e),
                color=COLORS.ERROR,
                timestamp=datetime.utcnow()
            )
            await interaction.followup.send(embed=error_embed, ephemeral=True)
            self.logger.warning(f"Registration failed for user {interaction.user.id}: {e}")

        except Exception as e:
            self.logger.error(f"Error in register modal for user {interaction.user.id}: {e}")
            error_embed = discord.Embed(
                title="❌ Error",
                description=MESSAGES.ERROR['REGISTRATION_FAILED'],
                color=COLORS.ERROR,
                timestamp=datetime.utcnow()
            )
            await interaction.followup.send(embed=error_embed, ephemeral=True)

class ShopView(View, BaseLockHandler, BaseResponseHandler):
    def __init__(self, bot):
        View.__init__(self, timeout=None)
        BaseLockHandler.__init__(self)
        BaseResponseHandler.__init__(self)

        self.bot = bot
        self.balance_service = BalanceManagerService(bot)
        self.product_service = ProductManagerService(bot)
        self.trx_manager = TransactionManager(bot)
        self.admin_service = AdminService(bot)
        self.cache_manager = CacheManager()
        self.logger = logging.getLogger("ShopView")

    async def _handle_interaction_error(self, interaction: discord.Interaction, error_msg: str, ephemeral: bool = True):
        """Helper untuk menangani interaction error dengan improved error handling"""
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="❌ Error",
                        description=error_msg,
                        color=COLORS.ERROR
                    ),
                    ephemeral=ephemeral
                )
            else:
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="❌ Error",
                        description=error_msg,
                        color=COLORS.ERROR
                    ),
                    ephemeral=ephemeral
                )
        except Exception as e:
            self.logger.error(f"Error sending error message: {e}")

    @discord.ui.button(
        style=discord.ButtonStyle.primary,
        label="📝 Set GrowID",
        custom_id=BUTTON_IDS.REGISTER
    )
    async def register_callback(self, interaction: discord.Interaction, button: Button):
        """Callback untuk tombol registrasi/update GrowID"""
        if not await self.acquire_response_lock(interaction):
            await self._handle_interaction_error(
                interaction, 
                MESSAGES.INFO['COOLDOWN']
            )
            return

        try:
            # Check maintenance mode
            if await self.admin_service.is_maintenance_mode():
                raise ValueError(MESSAGES.INFO['MAINTENANCE'])

            # Rate limit check dengan cache (5 menit cooldown)
            rate_limit_key = f"register_limit_{interaction.user.id}"
            if await self.cache_manager.get(rate_limit_key):
                raise ValueError(MESSAGES.ERROR['RATE_LIMIT'])

            # Check user blacklist
            blacklist_check = await self.admin_service.check_blacklist(str(interaction.user.id))
            if blacklist_check and blacklist_check.success and blacklist_check.data:
                self.logger.warning(f"Blacklisted user {interaction.user.id} attempted registration")
                raise ValueError(MESSAGES.ERROR['USER_BLACKLISTED'])

            # Get existing GrowID if any
            growid_response = await self.balance_service.get_growid(str(interaction.user.id))
            existing_growid = None

            if growid_response.success and growid_response.data:
                existing_growid = growid_response.data
                self.logger.info(f"Update GrowID attempt from {interaction.user.id} (Current: {existing_growid})")
            else:
                self.logger.info(f"New registration attempt from {interaction.user.id}")

            # Create and send modal
            modal = RegisterModal(
                balance_service=self.balance_service,
                existing_growid=existing_growid
            )

            # Set rate limit
            await self.cache_manager.set(
                rate_limit_key,
                True,
                expires_in=300  # 5 menit
            )

            await interaction.response.send_modal(modal)

        except ValueError as e:
            await self._handle_interaction_error(interaction, str(e))
        except Exception as e:
            self.logger.error(f"Error in register callback for user {interaction.user.id}: {e}")
            await self._handle_interaction_error(
                interaction, 
                MESSAGES.ERROR['REGISTRATION_FAILED']
            )
        finally:
            self.release_response_lock(interaction)

    @discord.ui.button(
        style=discord.ButtonStyle.success,
        label="💰 Saldo",
        custom_id=BUTTON_IDS.BALANCE
    )
    async def balance_callback(self, interaction: discord.Interaction, button: Button):
        """Callback untuk tombol cek saldo"""
        if not await self.acquire_response_lock(interaction):
            await self._handle_interaction_error(
                interaction, 
                MESSAGES.INFO['COOLDOWN']
            )
            return

        response_sent = False
        try:
            # Check maintenance mode
            if await self.admin_service.is_maintenance_mode():
                raise ValueError(MESSAGES.INFO['MAINTENANCE'])

            await interaction.response.defer(ephemeral=True)
            response_sent = True

            # Get GrowID
            growid_response = await self.balance_service.get_growid(str(interaction.user.id))
            if not growid_response.success:
                raise ValueError(growid_response.error)

            growid = growid_response.data
            if not growid:
                raise ValueError(MESSAGES.ERROR['NOT_REGISTERED'])

            # Get balance with timeout protection
            try:
                async with asyncio.timeout(10):
                    balance_response = await self.balance_service.get_balance(growid)
                    if not balance_response.success:
                        raise ValueError(balance_response.error)
            except asyncio.TimeoutError:
                raise ValueError(MESSAGES.ERROR['TIMEOUT'])

            balance = balance_response.data

            # Create embed
            embed = discord.Embed(
                title="💰 Informasi Saldo",
                description=f"Saldo untuk `{growid}`",
                color=COLORS.INFO,
                timestamp=datetime.utcnow()
            )

            embed.add_field(
                name="Saldo Saat Ini",
                value=f"```yml\n{balance.format()}```",
                inline=False
            )

            # Get transaction history
            try:
                async with asyncio.timeout(10):
                    history_response = await self.balance_service.get_transaction_history(
                        growid,
                        limit=3
                    )

                    if history_response.success and history_response.data:
                        transactions = []
                        for trx in history_response.data:
                            type_emoji = {
                                TransactionType.DEPOSIT.value: '💰',
                                TransactionType.PURCHASE.value: '🛒',
                                TransactionType.WITHDRAWAL.value: '💸',
                                TransactionType.TRANSFER.value: '↔️',
                                TransactionType.ADMIN_ADD.value: '⚡',
                                TransactionType.ADMIN_REMOVE.value: '❌',
                            }.get(trx['type'], '💱')

                            transactions.append(
                                f"{type_emoji} {trx['type']}: {trx.get('amount_wl', 0):,} WL - {trx['details']}"
                            )

                        if transactions:
                            embed.add_field(
                                name="Transaksi Terakhir",
                                value="```yml\n" + "\n".join(transactions) + "\n```",
                                inline=False
                            )
            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout getting transaction history for {growid}")
            except Exception as e:
                self.logger.error(f"Error getting transaction history: {e}")

            # Get daily limit info
            try:
                daily_limit = await self.balance_service.get_daily_limit(growid)
                daily_usage = await self.balance_service.get_daily_usage(growid)

                embed.add_field(
                    name="Limit Harian",
                    value=f"```yml\nDigunakan: {daily_usage:,}/{daily_limit:,} WL```",
                    inline=False
                )

                embed.set_footer(text=f"Diperbarui • Sisa limit: {daily_limit - daily_usage:,} WL")
            except Exception as e:
                self.logger.error(f"Error getting daily limits: {e}")
                embed.set_footer(text="Diperbarui")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except ValueError as e:
            await self._handle_interaction_error(interaction, str(e), response_sent)
        except Exception as e:
            self.logger.error(f"Error in balance callback: {e}")
            await self._handle_interaction_error(
                interaction,
                MESSAGES.ERROR['BALANCE_FAILED'],
                response_sent
            )
        finally:
            self.release_response_lock(interaction)

    @discord.ui.button(
        style=discord.ButtonStyle.secondary,
        label="🌎 World Info",
        custom_id=BUTTON_IDS.WORLD_INFO
    )
    async def world_info_callback(self, interaction: discord.Interaction, button: Button):
        """Callback untuk tombol world info"""
        if not await self.acquire_response_lock(interaction):
            await self._handle_interaction_error(
                interaction,
                MESSAGES.INFO['COOLDOWN']
            )
            return

        response_sent = False
        try:
            # Check maintenance mode
            if await self.admin_service.is_maintenance_mode():
                raise ValueError(MESSAGES.INFO['MAINTENANCE'])

            await interaction.response.defer(ephemeral=True)
            response_sent = True

            # Get world info with timeout protection
            try:
                async with asyncio.timeout(10):
                    world_response = await self.product_service.get_world_info()
                    if not world_response.success:
                        raise ValueError(world_response.error)
            except asyncio.TimeoutError:
                raise ValueError(MESSAGES.ERROR['TIMEOUT'])

            world_info = world_response.data

            # Create embed with proper formatting
            embed = discord.Embed(
                title="🌎 World Information",
                color=COLORS.INFO,
                timestamp=datetime.utcnow()
            )

            # Status emoji mapping
            status_emoji = {
                'online': '🟢',
                'offline': '🔴', 
                'maintenance': '🔧',
                'busy': '🟡',
                'full': '🔵'
            }

            # Get current status with emoji
            status = world_info.get('status', '').lower()
            status_display = f"{status_emoji.get(status, '❓')} {status.upper()}"

            # Format world details with proper spacing
            world_details = [
                f"{'World':<12}: {world_info.get('world', 'N/A')}",
                f"{'Owner':<12}: {world_info.get('owner', 'N/A')}",
                f"{'Bot':<12}: {world_info.get('bot', 'N/A')}",
                f"{'Status':<12}: {status_display}"
            ]

            embed.add_field(
                name="World Details",
                value="```\n" + "\n".join(world_details) + "\n```",
                inline=False
            )

            # Add additional info if available
            if features := world_info.get('features'):
                embed.add_field(
                    name="Features",
                    value="```yml\n" + "\n".join(features) + "\n```",
                    inline=False
                )

            # Add last update info
            if updated_at := world_info.get('updated_at'):
                try:
                    dt = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
                    last_update = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
                    embed.set_footer(text=f"Last Updated: {last_update}")
                except:
                    embed.set_footer(text="Last Updated: Unknown")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except ValueError as e:
            await self._handle_interaction_error(interaction, str(e), response_sent)
        except Exception as e:
            self.logger.error(f"Error in world info callback: {e}")
            await self._handle_interaction_error(
                interaction,
                MESSAGES.ERROR['WORLD_INFO_FAILED'],
                response_sent
            )
        finally:
            self.release_response_lock(interaction)

    @discord.ui.button(
        style=discord.ButtonStyle.success,
        label="🛒 Buy",
        custom_id=BUTTON_IDS.BUY
    )
    async def buy_callback(self, interaction: discord.Interaction, button: Button):
        """Callback untuk tombol pembelian"""
        if not await self.acquire_response_lock(interaction):
            await self._handle_interaction_error(
                interaction,
                MESSAGES.INFO['COOLDOWN']
            )
            return

        try:
            # Rate limit check
            rate_limit_key = f"buy_button_{interaction.user.id}"
            if await self.cache_manager.get(rate_limit_key):
                raise ValueError(MESSAGES.ERROR['RATE_LIMIT'])

            # Queue check
            queue_size = self.trx_manager.transaction_queue.queue.qsize()
            if queue_size > 50:  # Max queue size
                raise ValueError(MESSAGES.ERROR['SYSTEM_BUSY'])

            # Check maintenance mode
            if await self.admin_service.is_maintenance_mode():
                raise ValueError(MESSAGES.INFO['MAINTENANCE'])

            # Check blacklist
            blacklist_check = await self.admin_service.check_blacklist(str(interaction.user.id))
            if blacklist_check.success and blacklist_check.data:
                raise ValueError(MESSAGES.ERROR['USER_BLACKLISTED'])

            # Verify user registration first
            growid_response = await self.balance_service.get_growid(str(interaction.user.id))
            if not growid_response.success:
                raise ValueError(growid_response.error)

            # Get and validate available products
            try:
                async with asyncio.timeout(10):
                    product_response = await self.product_service.get_all_products()
                    if not product_response.success or not product_response.data:
                        raise ValueError(MESSAGES.ERROR['NO_PRODUCTS'])
            except asyncio.TimeoutError:
                raise ValueError(MESSAGES.ERROR['TIMEOUT'])

            # Filter available products with stock
            available_products = []
            for product in product_response.data:
                try:
                    async with asyncio.timeout(5):
                        stock_response = await self.product_service.get_stock_count(product['code'])
                        if stock_response.success and stock_response.data > 0:
                            product['stock'] = stock_response.data
                            available_products.append(product)
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    self.logger.error(f"Error checking stock for {product['code']}: {e}")
                    continue

            if not available_products:
                raise ValueError(MESSAGES.ERROR['OUT_OF_STOCK'])

            # Show purchase modal
            modal = PurchaseModal(
                available_products,
                self.balance_service,
                self.product_service,
                self.trx_manager,
                self.cache_manager
            )

            # Set rate limit
            await self.cache_manager.set(
                rate_limit_key,
                True,
                expires_in=60  # 1 menit cooldown
            )

            await interaction.response.send_modal(modal)

        except ValueError as e:
            await self._handle_interaction_error(interaction, str(e))
        except Exception as e:
            self.logger.error(f"Error in buy callback: {e}")
            await self._handle_interaction_error(
                interaction,
                MESSAGES.ERROR['TRANSACTION_FAILED']
            )
        finally:
            self.release_response_lock(interaction)

    @discord.ui.button(
        style=discord.ButtonStyle.secondary,
        label="📜 Riwayat",
        custom_id=BUTTON_IDS.HISTORY
    )
    async def history_callback(self, interaction: discord.Interaction, button: Button):
        """Callback untuk tombol riwayat transaksi"""
        if not await self.acquire_response_lock(interaction):
            await self._handle_interaction_error(
                interaction,
                MESSAGES.INFO['COOLDOWN']
            )
            return

        response_sent = False
        try:
            # Check maintenance mode
            if await self.admin_service.is_maintenance_mode():
                raise ValueError(MESSAGES.INFO['MAINTENANCE'])

            await interaction.response.defer(ephemeral=True)
            response_sent = True

            # Get user's GrowID
            growid_response = await self.balance_service.get_growid(str(interaction.user.id))
            if not growid_response.success:
                raise ValueError(growid_response.error)

            growid = growid_response.data

            # Get transaction history with timeout protection
            try:
                async with asyncio.timeout(10):
                    history_response = await self.balance_service.get_transaction_history(
                        growid,
                        limit=5
                    )
                    if not history_response.success:
                        raise ValueError(history_response.error)
            except asyncio.TimeoutError:
                raise ValueError(MESSAGES.ERROR['TIMEOUT'])

            transactions = history_response.data
            if not transactions:
                raise ValueError(MESSAGES.ERROR['NO_HISTORY'])

            # Create embed
            embed = discord.Embed(
                title="📊 Riwayat Transaksi",
                description=f"Transaksi terakhir untuk `{growid}`",
                color=COLORS.INFO,
                timestamp=datetime.utcnow()
            )

            # Add transaction details
            for i, trx in enumerate(transactions, 1):
                try:
                    # Get emoji based on transaction type
                    emoji = {
                        TransactionType.DEPOSIT.value: '💰',
                        TransactionType.PURCHASE.value: '🛒',
                        TransactionType.WITHDRAWAL.value: '💸',
                        TransactionType.TRANSFER_IN.value: '↙️',
                        TransactionType.TRANSFER_OUT.value: '↗️',
                        TransactionType.ADMIN_ADD.value: '⚡',
                        TransactionType.ADMIN_REMOVE.value: '❌'
                    }.get(trx['type'], '💱')

                    # Format date
                    date = datetime.fromisoformat(trx['created_at'].replace('Z', '+00:00'))
                    formatted_date = date.strftime("%Y-%m-%d %H:%M:%S")

                    embed.add_field(
                        name=f"{emoji} Transaksi #{i}",
                        value=(
                            f"```yml\n"
                            f"Tanggal : {formatted_date}\n"
                            f"Tipe    : {trx['type']}\n"
                            f"Jumlah  : {trx['amount_wl']:,} WL\n"
                            f"Detail  : {trx['details']}\n"
                            "```"
                        ),
                        inline=False
                    )
                except Exception as e:
                    self.logger.error(f"Error formatting transaction {i}: {e}")
                    continue

            embed.set_footer(text=f"Menampilkan {len(transactions)} transaksi terakhir")
            await interaction.followup.send(embed=embed, ephemeral=True)

        except ValueError as e:
            await self._handle_interaction_error(interaction, str(e), response_sent)
        except Exception as e:
            self.logger.error(f"Error in history callback: {e}")
            await self._handle_interaction_error(
                interaction,
                MESSAGES.ERROR['HISTORY_FAILED'],
                response_sent
            )
        finally:
            self.release_response_lock(interaction)

class LiveButtonManager(BaseLockHandler, BaseResponseHandler):
    def __init__(self, bot):
        if not hasattr(self, 'initialized') or not self.initialized:
            BaseLockHandler.__init__(self)
            BaseResponseHandler.__init__(self)

            self.bot = bot
            self.logger = logging.getLogger("LiveButtonManager")
            self.cache_manager = CacheManager()
            self.admin_service = AdminService(bot)
            self.stock_channel_id = int(self.bot.config.get('id_live_stock', 0))
            self.current_message = None
            self.stock_manager = None
            self._ready = asyncio.Event()
            self._initialization_lock = asyncio.Lock()
            self.initialized = True
            self.initialization_retries = 0
            self.max_initialization_retries = 3
            self.logger.info("LiveButtonManager initialized")

    async def initialize(self) -> bool:
        """Initialize the button manager with improved error handling and retries"""
        try:
            self.logger.info("Starting LiveButtonManager initialization...")

            while self.initialization_retries < self.max_initialization_retries:
                try:
                    async with asyncio.timeout(20):  # 20 second timeout per attempt
                        if await self.setup_dependencies():
                            self._ready.set()
                            self.logger.info("LiveButtonManager initialized successfully")
                            return True
                except asyncio.TimeoutError:
                    self.initialization_retries += 1
                    if self.initialization_retries < self.max_initialization_retries:
                        self.logger.warning(f"Initialization attempt {self.initialization_retries} timed out, retrying...")
                        await asyncio.sleep(5)
                    continue
                except Exception as e:
                    self.initialization_retries += 1
                    if self.initialization_retries < self.max_initialization_retries:
                        self.logger.error(f"Initialization attempt {self.initialization_retries} failed: {e}")
                        await asyncio.sleep(5)
                    continue

            self.logger.error("Failed to initialize after maximum retries")
            return False

        except Exception as e:
            self.logger.error(f"Error in LiveButtonManager initialization: {e}")
            return False

    async def setup_dependencies(self) -> bool:
        """Setup required dependencies"""
        try:
            # Verify stock manager
            stock_cog = self.bot.get_cog('LiveStockCog')
            if not stock_cog or not hasattr(stock_cog, 'stock_manager'):
                self.logger.error("LiveStockCog or stock_manager not found")
                return False

            self.stock_manager = stock_cog.stock_manager
            if not await self.wait_for_stock_manager_ready():
                return False

            # Verify channel
            channel = self.bot.get_channel(self.stock_channel_id)
            if not channel:
                self.logger.error(f"Channel {self.stock_channel_id} not found")
                return False

            return True

        except Exception as e:
            self.logger.error(f"Error setting up dependencies: {e}")
            return False

    async def wait_for_stock_manager_ready(self) -> bool:
        """Wait for stock manager to be ready"""
        try:
            if not self.stock_manager:
                return False

            async with asyncio.timeout(10):
                await self.stock_manager._ready.wait()
                return True
        except asyncio.TimeoutError:
            return False
        except Exception as e:
            self.logger.error(f"Error waiting for stock manager ready: {e}")
            return False

    # Lanjutan class LiveButtonManager

    def create_view(self):
        """Create shop view with buttons"""
        return ShopView(self.bot)

    async def set_stock_manager(self, stock_manager):
        """Set stock manager untuk integrasi dengan improved error handling"""
        try:
            self.logger.info("Setting up stock manager integration...")
            self.stock_manager = stock_manager
            
            if not self.stock_manager:
                raise ValueError("Stock manager cannot be None")
                
            await self.force_update()  # Update display setelah set stock manager
            self._ready.set()
            self.logger.info("Stock manager set successfully")
            
        except Exception as e:
            self.logger.error(f"Error setting stock manager: {e}")
            raise

    async def get_or_create_message(self) -> Optional[discord.Message]:
        """Create or get existing message with improved error handling"""
        async with self._lock:
            try:
                self.logger.info("Getting or creating shop message...")
                
                # Verify channel first
                channel = self.bot.get_channel(self.stock_channel_id)
                if not channel:
                    self.logger.error(f"Channel {self.stock_channel_id} not found")
                    return None

                # Check existing message from stock manager
                if self.stock_manager and self.stock_manager.current_stock_message:
                    try:
                        self.current_message = self.stock_manager.current_stock_message
                        view = self.create_view()
                        await self.current_message.edit(view=view)
                        self.logger.info("Updated existing message from stock manager")
                        return self.current_message
                    except discord.NotFound:
                        self.logger.warning("Existing message not found, will create new")
                        self.current_message = None
                    except Exception as e:
                        self.logger.error(f"Error updating existing message: {e}")
                        self.current_message = None

                # Find last valid message
                if self.stock_manager:
                    try:
                        async with asyncio.timeout(10):
                            existing_message = await self.stock_manager.find_last_message()
                            if existing_message:
                                self.current_message = existing_message
                                self.stock_manager.current_stock_message = existing_message
                                
                                # Update both embed and view
                                embed = await self.stock_manager.create_stock_embed()
                                view = self.create_view()
                                await existing_message.edit(embed=embed, view=view)
                                self.logger.info("Found and updated last valid message")
                                return existing_message
                    except asyncio.TimeoutError:
                        self.logger.warning("Timeout finding last message")
                    except Exception as e:
                        self.logger.error(f"Error finding last message: {e}")

                # Create new message if needed
                try:
                    self.logger.info("Creating new shop message...")
                    if self.stock_manager:
                        embed = await self.stock_manager.create_stock_embed()
                    else:
                        embed = discord.Embed(
                            title="🏪 Live Stock",
                            description=MESSAGES.INFO['INITIALIZING'],
                            color=COLORS.WARNING,
                            timestamp=datetime.utcnow()
                        )

                    view = self.create_view()
                    self.current_message = await channel.send(embed=embed, view=view)

                    if self.stock_manager:
                        self.stock_manager.current_stock_message = self.current_message
                    
                    self.logger.info("New shop message created successfully")
                    return self.current_message
                    
                except Exception as e:
                    self.logger.error(f"Error creating new message: {e}")
                    return None

            except Exception as e:
                self.logger.error(f"Error in get_or_create_message: {e}")
                return None

    async def force_update(self) -> bool:
        """Force update shop display with improved error handling"""
        try:
            async with asyncio.timeout(30):
                async with self._lock:
                    self.logger.info("Starting forced update...")
                    
                    # Get or create message if needed
                    if not self.current_message:
                        self.current_message = await self.get_or_create_message()
                        if not self.current_message:
                            self.logger.error("Failed to get or create message")
                            return False

                    # Check maintenance mode
                    try:
                        is_maintenance = await self.admin_service.is_maintenance_mode()
                        if is_maintenance:
                            self.logger.info("System is in maintenance mode")
                            embed = discord.Embed(
                                title="🔧 Maintenance Mode",
                                description=MESSAGES.INFO['MAINTENANCE'],
                                color=COLORS.WARNING,
                                timestamp=datetime.utcnow()
                            )
                            await self.current_message.edit(embed=embed, view=None)
                            return True
                    except Exception as e:
                        self.logger.error(f"Error checking maintenance mode: {e}")
                        return False

                    # Update stock display if available
                    if self.stock_manager:
                        try:
                            self.logger.info("Updating stock display...")
                            await self.stock_manager.update_stock_display()
                        except Exception as e:
                            self.logger.error(f"Error updating stock display: {e}")

                    # Update view
                    try:
                        self.logger.info("Updating shop view...")
                        view = self.create_view()
                        await self.current_message.edit(view=view)
                        self.logger.info("Forced update completed successfully")
                        return True
                    except discord.NotFound:
                        self.logger.error("Message not found during view update")
                        self.current_message = None
                        return False
                    except Exception as e:
                        self.logger.error(f"Error updating view: {e}")
                        return False

        except asyncio.TimeoutError:
            self.logger.error("Force update timed out")
            return False
        except Exception as e:
            self.logger.error(f"Error in force update: {e}")
            return False

    async def cleanup(self):
        """Cleanup resources with improved error handling"""
        try:
            self.logger.info("Starting LiveButtonManager cleanup...")
            
            # Cleanup base handlers
            await super().cleanup()

            # Update message for maintenance if exists
            if self.current_message:
                try:
                    embed = discord.Embed(
                        title="🛠️ Maintenance",
                        description=MESSAGES.INFO['MAINTENANCE'],
                        color=COLORS.WARNING,
                        timestamp=datetime.utcnow()
                    )
                    await self.current_message.edit(embed=embed, view=None)
                    self.logger.info("Updated message for maintenance mode")
                except discord.NotFound:
                    self.logger.warning("Message not found during cleanup")
                except Exception as e:
                    self.logger.error(f"Error updating message during cleanup: {e}")

            # Clear caches
            patterns = [
                'live_stock_message_id',
                'world_info',
                'available_products',
                'button_*',
                'shop_*'
            ]

            for pattern in patterns:
                try:
                    await self.cache_manager.delete_pattern(pattern)
                    self.logger.info(f"Cleared cache pattern: {pattern}")
                except Exception as e:
                    self.logger.error(f"Error clearing cache {pattern}: {e}")

            # Reset internal state
            self._ready.clear()
            self.current_message = None
            self.stock_manager = None
            
            self.logger.info("LiveButtonManager cleanup completed successfully")

        except Exception as e:
            self.logger.error(f"Error in cleanup: {e}")
            raise

    async def check_health(self) -> Dict:
        """Check health status of the button manager"""
        try:
            status = {
                'ready': self._ready.is_set(),
                'has_message': self.current_message is not None,
                'has_stock_manager': self.stock_manager is not None,
                'channel_accessible': self.bot.get_channel(self.stock_channel_id) is not None,
                'last_update': datetime.utcnow().isoformat(),
                'status': 'healthy'
            }
            
            if not all([status['ready'], status['has_message'], status['has_stock_manager'], status['channel_accessible']]):
                status['status'] = 'degraded'
                
            return status
            
        except Exception as e:
            self.logger.error(f"Error checking health: {e}")
            return {
                'status': 'error',
                'error': str(e),
                'timestamp': datetime.utcnow().isoformat()
            }

    async def refresh_components(self) -> bool:
        """Refresh all components and connections"""
        try:
            self.logger.info("Starting components refresh...")
            
            # Reset ready state
            self._ready.clear()
            
            # Reinitialize
            success = await self.initialize()
            if not success:
                raise ValueError("Failed to reinitialize components")
                
            # Force update display
            if not await self.force_update():
                raise ValueError("Failed to update display")
                
            self.logger.info("Components refresh completed successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Error refreshing components: {e}")
            return False

class LiveButtonsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.button_manager = LiveButtonManager(bot)
        self.logger = logging.getLogger("LiveButtonsCog")
        self._ready = asyncio.Event()
        self._initialization_lock = asyncio.Lock()
        self._cleanup_lock = asyncio.Lock()
        self.logger.info("LiveButtonsCog initialized")

    async def initialize_dependencies(self) -> bool:
        """Initialize all dependencies with improved error handling"""
        try:
            async with self._initialization_lock:
                self.logger.info("Starting dependency initialization...")

                if self._ready.is_set():
                    self.logger.info("Dependencies already initialized")
                    return True

                # Initialize button manager
                if not await self.button_manager.initialize():
                    self.logger.error("Failed to initialize button manager")
                    return False

                self._ready.set()
                self.logger.info("Dependencies initialized successfully")
                return True

        except Exception as e:
            self.logger.error(f"Error initializing dependencies: {e}")
            return False

    async def cog_load(self):
        """Setup when cog is loaded with improved error handling"""
        try:
            self.logger.info("LiveButtonsCog loading...")

            # Initialize dependencies with timeout
            try:
                async with asyncio.timeout(60):  # Increased timeout
                    success = await self.initialize_dependencies()
                    if not success:
                        raise RuntimeError("Failed to initialize dependencies")
                    self.logger.info("Dependencies initialized successfully")
            except asyncio.TimeoutError:
                self.logger.error("Initialization timed out")
                raise RuntimeError("Initialization timed out")

            self.logger.info("LiveButtonsCog loaded successfully")

        except Exception as e:
            self.logger.error(f"Error in cog_load: {e}")
            raise

    async def cog_unload(self):
        """Cleanup when cog is unloaded with improved error handling"""
        async with self._cleanup_lock:
            try:
                await self.button_manager.cleanup()
                self.logger.info("LiveButtonsCog unloaded successfully")
            except Exception as e:
                self.logger.error(f"Error in cog_unload: {e}")

async def setup(bot):
    try:
        if not hasattr(bot, COG_LOADED['LIVE_BUTTONS']):
            # Load required extensions with proper delays
            required_extensions = [
                'ext.live_stock',
                'ext.balance_manager',
                'ext.product_manager',
                'ext.trx'
            ]

            for ext in required_extensions:
                if ext not in bot.extensions:
                    logging.info(f"Loading required extension: {ext}")
                    await bot.load_extension(ext)
                    # Give more time for LiveStockCog
                    await asyncio.sleep(5 if ext == 'ext.live_stock' else 1)

            # Create and add cog
            cog = LiveButtonsCog(bot)
            await bot.add_cog(cog)

            # Wait for initialization with increased timeout
            try:
                async with asyncio.timeout(60):  # Increased timeout
                    await cog._ready.wait()
            except asyncio.TimeoutError:
                logging.error("LiveButtonsCog initialization timed out")
                await bot.remove_cog('LiveButtonsCog')
                raise RuntimeError("Initialization timed out")

            setattr(bot, COG_LOADED['LIVE_BUTTONS'], True)
            logging.info("LiveButtons cog loaded successfully")

    except Exception as e:
        logging.error(f"Failed to load LiveButtonsCog: {e}")
        if hasattr(bot, COG_LOADED['LIVE_BUTTONS']):
            delattr(bot, COG_LOADED['LIVE_BUTTONS'])
        raise

async def teardown(bot):
    try:
        if hasattr(bot, COG_LOADED['LIVE_BUTTONS']):
            cog = bot.get_cog('LiveButtonsCog')
            if cog:
                await bot.remove_cog('LiveButtonsCog')
                if hasattr(cog, 'button_manager'):
                    await cog.button_manager.cleanup()
            delattr(bot, COG_LOADED['LIVE_BUTTONS'])
            logging.info("LiveButtons cog unloaded successfully")
    except Exception as e:
        logging.error(f"Error unloading LiveButtonsCog: {e}")