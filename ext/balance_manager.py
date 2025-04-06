"""
Balance Manager Service
Author: fdyyuk
Created at: 2025-03-07 18:04:56 UTC
Last Modified: 2025-03-15 16:56:01 UTC

Dependencies:
- database.py: For database connections
- base_handler.py: For lock management
- cache_manager.py: For caching functionality
- constants.py: For configuration and responses
"""

import logging
import asyncio
from typing import Dict, Optional, Union, Callable, Any, List
from datetime import datetime, timedelta

import discord
from discord.ext import commands

from .constants import (
    Balance,
    TransactionType,
    TransactionError,
    CURRENCY_RATES,
    MESSAGES,
    CACHE_TIMEOUT,
    COLORS,
    NOTIFICATION_CHANNELS,
    EVENTS,
    LIMITS
)
from database import get_connection
from .base_handler import BaseLockHandler
from .cache_manager import CacheManager

class BalanceCallbackManager:
    """Manager untuk mengelola callbacks balance service"""
    def __init__(self):
        self.callbacks = {
            'balance_updated': [],    # Dipanggil setelah balance diupdate
            'balance_checked': [],    # Dipanggil saat balance dicek
            'user_registered': [],    # Dipanggil setelah user register
            'transaction_added': [],  # Dipanggil setelah transaksi baru
            'balance_locked': [],     # Dipanggil saat balance dikunci
            'balance_unlocked': [],   # Dipanggil saat balance dibuka
            'limit_updated': [],      # Dipanggil saat limit diupdate
            'suspicious_activity': [], # Dipanggil saat ada aktivitas mencurigakan
            'error': []              # Dipanggil saat terjadi error
        }
    
    def register(self, event_type: str, callback: Callable):
        """Register callback untuk event tertentu"""
        if event_type in self.callbacks:
            self.callbacks[event_type].append(callback)
    
    async def trigger(self, event_type: str, *args: Any, **kwargs: Any):
        """Trigger semua callback untuk event tertentu"""
        if event_type in self.callbacks:
            for callback in self.callbacks[event_type]:
                try:
                    await callback(*args, **kwargs)
                except Exception as e:
                    logging.error(f"Error in {event_type} callback: {e}")

class BalanceResponse:
    """Class untuk standarisasi response dari balance service"""
    def __init__(self, success: bool, data: Any = None, message: str = "", error: str = ""):
        self.success = success
        self.data = data
        self.message = message
        self.error = error
        self.timestamp = datetime.utcnow()
    
    def to_dict(self) -> Dict:
        return {
            'success': self.success,
            'data': self.data,
            'message': self.message,
            'error': self.error,
            'timestamp': self.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        }
    
    @classmethod
    def success(cls, data: Any = None, message: str = "") -> 'BalanceResponse':
        return cls(True, data, message)
    
    @classmethod
    def error(cls, error: str, message: str = "") -> 'BalanceResponse':
        return cls(False, None, message, error)

class BalanceManagerService(BaseLockHandler):
    _instance = None
    _instance_lock = asyncio.Lock()

    def __new__(cls, bot):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.initialized = False
        return cls._instance

    def __init__(self, bot):
        if not self.initialized:
            super().__init__()
            self.bot = bot
            self.logger = logging.getLogger("BalanceManagerService")
            self.cache_manager = CacheManager()
            self.callback_manager = BalanceCallbackManager()
            self.setup_default_callbacks()
            self.initialized = True

    def setup_default_callbacks(self):
        """Setup default callbacks untuk notifikasi"""
        
        async def notify_balance_updated(growid: str, old_balance: Balance, new_balance: Balance):
            """Callback untuk notifikasi update balance"""
            channel_id = NOTIFICATION_CHANNELS.get('transactions')
            if channel := self.bot.get_channel(channel_id):
                embed = discord.Embed(
                    title="Balance Updated",
                    color=COLORS.SUCCESS
                )
                embed.add_field(name="GrowID", value=growid)
                embed.add_field(name="Old Balance", value=str(old_balance))
                embed.add_field(name="New Balance", value=str(new_balance))
                await channel.send(embed=embed)
        
        async def notify_user_registered(discord_id: str, growid: str):
            """Callback untuk notifikasi user registration"""
            channel_id = NOTIFICATION_CHANNELS.get('admin_logs')
            if channel := self.bot.get_channel(channel_id):
                embed = discord.Embed(
                    title="New User Registered",
                    color=COLORS.INFO
                )
                embed.add_field(name="Discord ID", value=discord_id)
                embed.add_field(name="GrowID", value=growid)
                await channel.send(embed=embed)

        async def notify_suspicious_activity(growid: str, activity_type: str, details: Dict):
            """Callback untuk notifikasi aktivitas mencurigakan"""
            channel_id = NOTIFICATION_CHANNELS.get('admin_logs')
            if channel := self.bot.get_channel(channel_id):
                embed = discord.Embed(
                    title="⚠️ Suspicious Activity Detected",
                    color=COLORS.WARNING
                )
                embed.add_field(name="GrowID", value=growid)
                embed.add_field(name="Type", value=activity_type)
                embed.add_field(name="Details", value=str(details))
                await channel.send(embed=embed)
        
        # Register default callbacks
        self.callback_manager.register('balance_updated', notify_balance_updated)
        self.callback_manager.register('user_registered', notify_user_registered)
        self.callback_manager.register('suspicious_activity', notify_suspicious_activity)

    async def verify_dependencies(self) -> bool:
        """Verify all required dependencies are available"""
        try:
            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()
                return True
            finally:
                if conn:
                    conn.close()
        except Exception as e:
            self.logger.error(f"Failed to verify dependencies: {e}")
            return False

    async def cleanup(self):
        """Cleanup resources before unloading"""
        try:
            patterns = [
                "growid_*",
                "discord_id_*", 
                "balance_*",
                "trx_history_*",
                "daily_limit_*",
                "lock_status_*"
            ]
            for pattern in patterns:
                await self.cache_manager.delete_pattern(pattern)
            self.logger.info("BalanceManagerService cleanup completed")
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")

    async def get_growid(self, discord_id: str) -> BalanceResponse:
        """Get GrowID for Discord user with proper locking and caching"""
        cache_key = f"growid_{discord_id}"
        cached = await self.cache_manager.get(cache_key)
        if cached:
            return BalanceResponse.success(cached)

        lock = await self.acquire_lock(cache_key)
        if not lock:
            return BalanceResponse.error(MESSAGES.ERROR['LOCK_ACQUISITION_FAILED'])

        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT growid FROM user_growid WHERE discord_id = ? COLLATE binary",
                (str(discord_id),)
            )
            result = cursor.fetchone()
            
            if result:
                growid = result['growid']
                await self.cache_manager.set(
                    cache_key, 
                    growid,
                    expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.LONG)
                )
                return BalanceResponse.success(growid)
            return BalanceResponse.error(MESSAGES.ERROR['NOT_REGISTERED'])

        except Exception as e:
            self.logger.error(f"Error getting GrowID: {e}")
            await self.callback_manager.trigger('error', 'get_growid', str(e))
            return BalanceResponse.error(MESSAGES.ERROR['DATABASE_ERROR'])
        finally:
            if conn:
                conn.close()
            self.release_lock(cache_key)

    async def register_user(self, discord_id: str, growid: str) -> BalanceResponse:
        """Register user with proper locking"""
        if not growid or len(growid) < 3:
            return BalanceResponse.error(MESSAGES.ERROR['INVALID_GROWID'])
            
        lock = await self.acquire_lock(f"register_{discord_id}")
        if not lock:
            return BalanceResponse.error(MESSAGES.ERROR['LOCK_ACQUISITION_FAILED'])

        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Check for existing GrowID - make case insensitive
            cursor.execute(
                "SELECT growid FROM users WHERE LOWER(growid) = LOWER(?) COLLATE NOCASE",
                (growid,)
            )
            existing = cursor.fetchone()
            if existing and existing['growid'] != growid:
                return BalanceResponse.error(MESSAGES.ERROR['GROWID_EXISTS'])
            
            conn.execute("BEGIN TRANSACTION")
            
            cursor.execute(
                """
                INSERT OR IGNORE INTO users (
                    growid, 
                    balance_wl, 
                    balance_dl, 
                    balance_bgl,
                    daily_limit,
                    is_locked
                ) 
                VALUES (?, 0, 0, 0, ?, FALSE)
                """,
                (growid, LIMITS.DEFAULT_DAILY_LIMIT)
            )
            
            cursor.execute(
                """
                INSERT OR REPLACE INTO user_growid (discord_id, growid) 
                VALUES (?, ?)
                """,
                (str(discord_id), growid)
            )
            
            conn.commit()
            
            # Update caches
            await self.cache_manager.set(
                f"growid_{discord_id}", 
                growid,
                expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.LONG)
            )
            await self.cache_manager.set(
                f"discord_id_{growid}", 
                discord_id,
                expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.LONG)
            )
            await self.cache_manager.delete(f"balance_{growid}")
            
            # Trigger callback
            await self.callback_manager.trigger('user_registered', discord_id, growid)
            
            return BalanceResponse.success(
                {'discord_id': discord_id, 'growid': growid},
                MESSAGES.SUCCESS['REGISTRATION'].format(growid=growid)
            )

        except Exception as e:
            self.logger.error(f"Error registering user: {e}")
            if conn:
                conn.rollback()
            await self.callback_manager.trigger('error', 'register_user', str(e))
            return BalanceResponse.error(MESSAGES.ERROR['REGISTRATION_FAILED'])
        finally:
            if conn:
                conn.close()
            self.release_lock(f"register_{discord_id}")

    async def get_balance(self, growid: str) -> BalanceResponse:
        """Get user balance with proper locking and caching"""
        # Cek status locked
        if await self.is_balance_locked(growid):
            return BalanceResponse.error(MESSAGES.ERROR['BALANCE_LOCKED'])

        cache_key = f"balance_{growid}"
        cached = await self.cache_manager.get(cache_key)
        if cached:
            if isinstance(cached, dict):
                balance = Balance(cached['wl'], cached['dl'], cached['bgl'])
            else:
                balance = cached
            return BalanceResponse.success(balance)

        lock = await self.acquire_lock(cache_key)
        if not lock:
            return BalanceResponse.error(MESSAGES.ERROR['LOCK_ACQUISITION_FAILED'])

        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                """
                SELECT balance_wl, balance_dl, balance_bgl 
                FROM users 
                WHERE growid = ? COLLATE binary
                """,
                (growid,)
            )
            result = cursor.fetchone()
            
            if result:
                balance = Balance(
                    result['balance_wl'],
                    result['balance_dl'],
                    result['balance_bgl']
                )
                await self.cache_manager.set(
                    cache_key, 
                    balance,
                    expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.SHORT)
                )
                
                # Trigger callback
                await self.callback_manager.trigger('balance_checked', growid, balance)
                
                return BalanceResponse.success(balance)
            return BalanceResponse.error(MESSAGES.ERROR['BALANCE_NOT_FOUND'])

        except Exception as e:
            self.logger.error(f"Error getting balance: {e}")
            await self.callback_manager.trigger('error', 'get_balance', str(e))
            return BalanceResponse.error(MESSAGES.ERROR['BALANCE_FAILED'])
        finally:
            if conn:
                conn.close()
            self.release_lock(cache_key)

    async def update_balance(
        self, 
        growid: str, 
        wl: int = 0, 
        dl: int = 0, 
        bgl: int = 0,
        details: str = "", 
        transaction_type: str = ""
    ) -> BalanceResponse:
        """Update balance with proper locking and validation"""
        # Cek status locked
        if await self.is_balance_locked(growid):
            return BalanceResponse.error(MESSAGES.ERROR['BALANCE_LOCKED'])

        # Cek daily limit
        if transaction_type != TransactionType.ADMIN_ADD.value:
            daily_usage = await self.get_daily_usage(growid)
            daily_limit = await self.get_daily_limit(growid)
            
            transaction_amount = abs(wl + (dl * 100) + (bgl * 10000))
            if daily_usage + transaction_amount > daily_limit:
                return BalanceResponse.error(MESSAGES.ERROR['DAILY_LIMIT_EXCEEDED'])

        lock = await self.acquire_lock(f"balance_update_{growid}")
        if not lock:
            return BalanceResponse.error(MESSAGES.ERROR['LOCK_ACQUISITION_FAILED'])

        conn = None
        try:
            # Get current balance
            balance_response = await self.get_balance(growid)
            if not balance_response.success:
                return balance_response
            
            current_balance = balance_response.data
            
            # Calculate new balance
            new_wl = max(0, current_balance.wl + wl)
            new_dl = max(0, current_balance.dl + dl)
            new_bgl = max(0, current_balance.bgl + bgl)
            
            new_balance = Balance(new_wl, new_dl, new_bgl)
            
            if not new_balance.validate():
                return BalanceResponse.error(MESSAGES.ERROR['INVALID_AMOUNT'])

            # Validate withdrawals
            if wl < 0 and abs(wl) > current_balance.wl:
                return BalanceResponse.error(MESSAGES.ERROR['INSUFFICIENT_BALANCE'])
            if dl < 0 and abs(dl) > current_balance.dl:
                return BalanceResponse.error(MESSAGES.ERROR['INSUFFICIENT_BALANCE'])
            if bgl < 0 and abs(bgl) > current_balance.bgl:
                return BalanceResponse.error(MESSAGES.ERROR['INSUFFICIENT_BALANCE'])

            # Deteksi aktivitas mencurigakan
            if await self._detect_suspicious_activity(growid, current_balance, new_balance):
                await self.callback_manager.trigger(
                    'suspicious_activity',
                    growid,
                    'large_transaction',
                    {
                        'old_balance': current_balance.format(),
                        'new_balance': new_balance.format(),
                        'change_wl': wl,
                        'change_dl': dl,
                        'change_bgl': bgl
                    }
                )

            conn = get_connection()
            cursor = conn.cursor()
            
            try:
                conn.execute("BEGIN TRANSACTION")
                
                cursor.execute(
                    """
                    UPDATE users 
                    SET balance_wl = ?, balance_dl = ?, balance_bgl = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE growid = ? COLLATE binary
                    """,
                    (new_wl, new_dl, new_bgl, growid)
                )
                
                cursor.execute(
                    """
                    INSERT INTO transactions 
                    (growid, type, details, old_balance, new_balance, amount_wl, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        growid,
                        transaction_type,
                        details,
                        current_balance.format(),
                        new_balance.format(),
                        wl + (dl * 100) + (bgl * 10000)
                    )
                )
                
                # Update daily usage jika bukan transaksi admin
                if transaction_type != TransactionType.ADMIN_ADD.value:
                    await self._update_daily_usage(
                        growid, 
                        wl + (dl * 100) + (bgl * 10000)
                    )
                
                conn.commit()
                
                # Update cache
                await self.cache_manager.set(
                    f"balance_{growid}", 
                    new_balance,
                    expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.SHORT)
                )
                
                # Invalidate transaction history cache
                await self.cache_manager.delete(f"trx_history_{growid}")
                
                # Trigger callbacks
                await self.callback_manager.trigger(
                    'balance_updated', 
                    growid, 
                    current_balance, 
                    new_balance
                )
                await self.callback_manager.trigger(
                    'transaction_added',
                    growid,
                    transaction_type,
                    details
                )
                
                return BalanceResponse.success(
                    new_balance,
                    MESSAGES.SUCCESS['BALANCE_UPDATE']
                )

            except Exception as e:
                conn.rollback()
                raise TransactionError(str(e))

        except TransactionError as e:
            return BalanceResponse.error(str(e))
        except Exception as e:
            self.logger.error(f"Error updating balance: {e}")
            await self.callback_manager.trigger('error', 'update_balance', str(e))
            return BalanceResponse.error(MESSAGES.ERROR['TRANSACTION_FAILED'])
        finally:
            if conn:
                conn.close()
            self.release_lock(f"balance_update_{growid}")

    async def transfer_balance(
        self,
        sender_growid: str,
        receiver_growid: str,
        amount_wl: int
    ) -> BalanceResponse:
        """Transfer balance antara dua user"""
        # Validasi input
        if sender_growid == receiver_growid:
            return BalanceResponse.error(MESSAGES.ERROR['INVALID_TRANSFER'])
            
        if amount_wl <= 0:
            return BalanceResponse.error(MESSAGES.ERROR['INVALID_AMOUNT'])

        # Cek status locked untuk kedua user
        if await self.is_balance_locked(sender_growid):
            return BalanceResponse.error(
                MESSAGES.ERROR['BALANCE_LOCKED'].format(growid=sender_growid)
            )
        if await self.is_balance_locked(receiver_growid):
            return BalanceResponse.error(
                MESSAGES.ERROR['BALANCE_LOCKED'].format(growid=receiver_growid)
            )

        # Lock untuk kedua user
        sender_lock = await self.acquire_lock(f"transfer_{sender_growid}")
        if not sender_lock:
            return BalanceResponse.error(MESSAGES.ERROR['LOCK_ACQUISITION_FAILED'])
            
        receiver_lock = await self.acquire_lock(f"transfer_{receiver_growid}")
        if not receiver_lock:
            self.release_lock(f"transfer_{sender_growid}")
            return BalanceResponse.error(MESSAGES.ERROR['LOCK_ACQUISITION_FAILED'])

        try:
            # Update sender balance (pengurangan)
            sender_update = await self.update_balance(
                growid=sender_growid,
                wl=-amount_wl,
                details=f"Transfer to {receiver_growid}",
                transaction_type=TransactionType.TRANSFER_OUT.value
            )
            
            if not sender_update.success:
                return sender_update

            # Update receiver balance (penambahan)
            receiver_update = await self.update_balance(
                growid=receiver_growid,
                wl=amount_wl,
                details=f"Transfer from {sender_growid}",
                transaction_type=TransactionType.TRANSFER_IN.value
            )
            
            if not receiver_update.success:
                # Rollback sender balance
                await self.update_balance(
                    growid=sender_growid,
                    wl=amount_wl,
                    details=f"Rollback transfer to {receiver_growid}",
                    transaction_type=TransactionType.TRANSFER_ROLLBACK.value
                )
                return receiver_update

            return BalanceResponse.success(
                {
                    'sender': sender_update.data,
                    'receiver': receiver_update.data,
                    'amount': amount_wl
                },
                MESSAGES.SUCCESS['TRANSFER']
            )

        finally:
            self.release_lock(f"transfer_{sender_growid}")
            self.release_lock(f"transfer_{receiver_growid}")

    async def lock_balance(self, growid: str, reason: str = "") -> BalanceResponse:
        """Kunci balance user"""
        lock = await self.acquire_lock(f"lock_{growid}")
        if not lock:
            return BalanceResponse.error(MESSAGES.ERROR['LOCK_ACQUISITION_FAILED'])

        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                """
                UPDATE users 
                SET is_locked = TRUE,
                    lock_reason = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE growid = ? COLLATE binary
                """,
                (reason, growid)
            )
            
            conn.commit()
            
            # Update cache
            await self.cache_manager.set(
                f"lock_status_{growid}",
                True,
                expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.MEDIUM)
            )
            
            # Trigger callback
            await self.callback_manager.trigger(
                'balance_locked',
                growid,
                reason
            )
            
            return BalanceResponse.success(
                None,
                MESSAGES.SUCCESS['BALANCE_LOCKED']
            )

        except Exception as e:
            self.logger.error(f"Error locking balance: {e}")
            if conn:
                conn.rollback()
            return BalanceResponse.error(str(e))
        finally:
            if conn:
                conn.close()
            self.release_lock(f"lock_{growid}")

    async def unlock_balance(self, growid: str) -> BalanceResponse:
        """Buka kunci balance user"""
        lock = await self.acquire_lock(f"lock_{growid}")
        if not lock:
            return BalanceResponse.error(MESSAGES.ERROR['LOCK_ACQUISITION_FAILED'])

        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                """
                UPDATE users 
                SET is_locked = FALSE,
                    lock_reason = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE growid = ? COLLATE binary
                """,
                (growid,)
            )
            
            conn.commit()
            
            # Update cache
            await self.cache_manager.delete(f"lock_status_{growid}")
            
            # Trigger callback
            await self.callback_manager.trigger(
                'balance_unlocked',
                growid
            )
            
            return BalanceResponse.success(
                None,
                MESSAGES.SUCCESS['BALANCE_UNLOCKED']
            )

        except Exception as e:
            self.logger.error(f"Error unlocking balance: {e}")
            if conn:
                conn.rollback()
            return BalanceResponse.error(str(e))
        finally:
            if conn:
                conn.close()
            self.release_lock(f"lock_{growid}")

    async def is_balance_locked(self, growid: str) -> bool:
        """Cek apakah balance user terkunci"""
        try:
            # Cek cache dulu
            cache_key = f"lock_status_{growid}"
            cached = await self.cache_manager.get(cache_key)
            if cached is not None:
                return cached

            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT is_locked FROM users WHERE growid = ? COLLATE binary",
                (growid,)
            )
            
            result = cursor.fetchone()
            is_locked = bool(result['is_locked']) if result else False
            
            # Update cache
            await self.cache_manager.set(
                cache_key,
                is_locked,
                expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.MEDIUM)
            )
            
            return is_locked

        except Exception as e:
            self.logger.error(f"Error checking lock status: {e}")
            return False
        finally:
            if conn:
                conn.close()

    async def set_daily_limit(self, growid: str, limit: int) -> BalanceResponse:
        """Set limit harian untuk user"""
        if limit < 0:
            return BalanceResponse.error(MESSAGES.ERROR['INVALID_LIMIT'])

        lock = await self.acquire_lock(f"limit_{growid}")
        if not lock:
            return BalanceResponse.error(MESSAGES.ERROR['LOCK_ACQUISITION_FAILED'])

        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                """
                UPDATE users 
                SET daily_limit = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE growid = ? COLLATE binary
                """,
                (limit, growid)
            )
            
            conn.commit()
            
            # Update cache
            await self.cache_manager.delete(f"daily_limit_{growid}")
            
            # Trigger callback
            await self.callback_manager.trigger(
                'limit_updated',
                growid,
                limit
            )
            
            return BalanceResponse.success(
                {'limit': limit},
                MESSAGES.SUCCESS['LIMIT_UPDATED']
            )

        except Exception as e:
            self.logger.error(f"Error setting daily limit: {e}")
            if conn:
                conn.rollback()
            return BalanceResponse.error(str(e))
        finally:
            if conn:
                conn.close()
            self.release_lock(f"limit_{growid}")

    async def get_daily_limit(self, growid: str) -> int:
        """Get limit harian user"""
        try:
            # Cek cache dulu
            cache_key = f"daily_limit_{growid}"
            cached = await self.cache_manager.get(cache_key)
            if cached is not None:
                return cached

            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT daily_limit FROM users WHERE growid = ? COLLATE binary",
                (growid,)
            )
            
            result = cursor.fetchone()
            limit = result['daily_limit'] if result else LIMITS.DEFAULT_DAILY_LIMIT
            
            # Update cache
            await self.cache_manager.set(
                cache_key,
                limit,
                expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.MEDIUM)
            )
            
            return limit

        except Exception as e:
            self.logger.error(f"Error getting daily limit: {e}")
            return LIMITS.DEFAULT_DAILY_LIMIT
        finally:
            if conn:
                conn.close()

    async def get_daily_usage(self, growid: str) -> int:
        """Get penggunaan harian user"""
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Get total amount untuk hari ini
            cursor.execute(
                """
                SELECT COALESCE(SUM(ABS(amount_wl)), 0) as total
                FROM transactions 
                WHERE growid = ? 
                AND type NOT IN (?, ?)
                AND DATE(created_at) = DATE('now')
                """,
                (
                    growid,
                    TransactionType.ADMIN_ADD.value,
                    TransactionType.TRANSFER_ROLLBACK.value
                )
            )
            
            result = cursor.fetchone()
            return result['total'] if result else 0

        except Exception as e:
            self.logger.error(f"Error getting daily usage: {e}")
            return 0
        finally:
            if conn:
                conn.close()

    async def _update_daily_usage(self, growid: str, amount: int) -> None:
        """Update penggunaan harian user (internal method)"""
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                """
                INSERT INTO daily_usage (growid, amount, date)
                VALUES (?, ?, DATE('now'))
                ON CONFLICT(growid, date) DO UPDATE
                SET amount = amount + excluded.amount
                """,
                (growid, abs(amount))
            )
            
            conn.commit()

        except Exception as e:
            self.logger.error(f"Error updating daily usage: {e}")
        finally:
            if conn:
                conn.close()

    async def _detect_suspicious_activity(
        self,
        growid: str,
        old_balance: Balance,
        new_balance: Balance
    ) -> bool:
        """Deteksi aktivitas mencurigakan"""
        try:
            # Hitung perubahan dalam WL
            old_total = old_balance.total_wl()
            new_total = new_balance.total_wl()
            change = abs(new_total - old_total)
            
            # Cek perubahan signifikan (>50% dari total balance)
            if old_total > 0 and change > (old_total * 0.5):
                return True
                
            # Cek transaksi besar (>100K WL)
            if change > 100000:
                return True
                
            # Cek transaksi cepat
            recent_transactions = await self._get_recent_transactions(growid, minutes=5)
            if len(recent_transactions) > 5:  # Lebih dari 5 transaksi dalam 5 menit
                return True
            
            return False

        except Exception as e:
            self.logger.error(f"Error detecting suspicious activity: {e}")
            return False

    async def _get_recent_transactions(
        self,
        growid: str,
        minutes: int = 5
    ) -> List[Dict]:
        """Get transaksi terbaru dalam interval waktu tertentu"""
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                """
                SELECT *
                FROM transactions 
                WHERE growid = ?
                AND created_at >= datetime('now', ?)
                ORDER BY created_at DESC
                """,
                (growid, f'-{minutes} minutes')
            )
            
            return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            self.logger.error(f"Error getting recent transactions: {e}")
            return []
        finally:
            if conn:
                conn.close()

    async def get_transaction_history(
        self,
        growid: str,
        limit: int = 10,
        offset: int = 0
    ) -> BalanceResponse:
        """Get riwayat transaksi user"""
        cache_key = f"trx_history_{growid}"
        cached = await self.cache_manager.get(cache_key)
        if cached:
            return BalanceResponse.success(cached[:limit])

        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                """
                SELECT * FROM transactions 
                WHERE growid = ? COLLATE binary
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (growid, limit, offset)
            )
            
            transactions = [dict(row) for row in cursor.fetchall()]
            
            await self.cache_manager.set(
                cache_key, 
                transactions,
                expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.SHORT)
            )
            
            if not transactions:
                return BalanceResponse.error(MESSAGES.ERROR['NO_HISTORY'])
                
            return BalanceResponse.success(transactions)

        except Exception as e:
            self.logger.error(f"Error getting transaction history: {e}")
            await self.callback_manager.trigger('error', 'get_transaction_history', str(e))
            return BalanceResponse.error(MESSAGES.ERROR['DATABASE_ERROR'])
        finally:
            if conn:
                conn.close()

class BalanceManagerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.balance_service = BalanceManagerService(bot)
        self.logger = logging.getLogger("BalanceManagerCog")

    async def cog_load(self):
        self.logger.info("BalanceManagerCog loading...")
        
    async def cog_unload(self):
        await self.balance_service.cleanup()
        self.logger.info("BalanceManagerCog unloaded")

async def setup(bot):
    if not hasattr(bot, 'balance_manager_loaded'):
        cog = BalanceManagerCog(bot)
        
        # Verify dependencies
        if not await cog.balance_service.verify_dependencies():
            raise Exception("BalanceManager dependencies verification failed")
            
        await bot.add_cog(cog)
        bot.balance_manager_loaded = True
        logging.info(
            f'BalanceManager cog loaded successfully at '
            f'{datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")} UTC'
        )