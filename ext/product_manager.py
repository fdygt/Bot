"""
Product Manager Service
Version: 2.0.0
Author: fdyytu2
Created at: 2025-03-07 18:04:56 UTC
Last Modified: 2025-03-19 17:21:19 UTC

Dependencies:
- database.py: For database connections
- base_handler.py: For lock management
- cache_manager.py: For caching functionality

Core Features:
1. Product Management
   - Product CRUD operations
   - Stock management
   - Category management
   - Price management
   
2. Stock Control
   - Stock addition
   - Stock reduction
   - Stock validation
   - Stock history
   
3. Security
   - Lock management
   - Input validation
   - Access control
   
4. Integration
   - Balance Manager integration 
   - Transaction Manager integration
   - Notification system
   
5. Monitoring
   - Stock level monitoring
   - Performance monitoring
   - Error tracking
   - Activity logging
"""

import logging
import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime
import hashlib
import io

import discord
from discord.ext import commands

from .constants import (
    Status,
    TransactionError,
    CACHE_TIMEOUT,
    MESSAGES,
    Stock,
    COLORS,
    NOTIFICATION_CHANNELS
)
from database import get_connection
from .base_handler import BaseLockHandler
from .cache_manager import CacheManager

class ProductManagerResponse:
    """Response handler untuk semua operasi product manager"""
    def __init__(self, success: bool, data: Any = None, message: str = "", error: str = ""):
        self.success = success
        self.data = data 
        self.message = message
        self.error = error
        self.timestamp = datetime.utcnow()
        
        # Fields untuk product
        self.product_code = None
        self.product_name = None
        self.product_price = None
        self.description = None
        
        # Fields untuk stock
        self.stock_count = None
        self.stock_items = None
        self.stock_status = None
        
        # Fields untuk transaksi
        self.buyer_id = None
        self.quantity = None
        self.total_price = None
        self.transaction_type = None
        
        # Fields untuk world info
        self.world = None
        self.owner = None
        self.bot_name = None
        self.world_status = None

    @classmethod
    def success(cls, data: Any = None, message: str = "") -> 'ProductManagerResponse':
        return cls(True, data, message)

    @classmethod
    def error(cls, error: str, message: str = "") -> 'ProductManagerResponse':
        return cls(False, None, message, error)

    def set_product_info(self, code: str, name: str, price: int, description: str = None):
        """Set informasi produk"""
        self.product_code = code
        self.product_name = name
        self.product_price = price
        self.description = description
        return self

    def set_stock_info(self, count: int, items: List = None, status: str = None):
        """Set informasi stok"""
        self.stock_count = count
        self.stock_items = items
        self.stock_status = status
        return self

    def set_transaction_info(self, buyer_id: str, quantity: int, total_price: int, type: str):
        """Set informasi transaksi"""
        self.buyer_id = buyer_id
        self.quantity = quantity
        self.total_price = total_price
        self.transaction_type = type
        return self

    def set_world_info(self, world: str, owner: str, bot_name: str, status: str):
        """Set informasi world"""
        self.world = world
        self.owner = owner
        self.bot_name = bot_name
        self.world_status = status
        return self

    def to_dict(self) -> Dict:
        """Convert response ke dictionary"""
        result = {
            'success': self.success,
            'data': self.data,
            'message': self.message,
            'error': self.error,
            'timestamp': self.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        # Add product info if exists
        if self.product_code:
            result.update({
                'product': {
                    'code': self.product_code,
                    'name': self.product_name,
                    'price': self.product_price,
                    'description': self.description
                }
            })
            
        # Add stock info if exists
        if self.stock_count is not None:
            result.update({
                'stock': {
                    'count': self.stock_count,
                    'items': self.stock_items,
                    'status': self.stock_status
                }
            })
            
        # Add transaction info if exists
        if self.buyer_id:
            result.update({
                'transaction': {
                    'buyer_id': self.buyer_id,
                    'quantity': self.quantity,
                    'total_price': self.total_price,
                    'type': self.transaction_type
                }
            })
            
        # Add world info if exists
        if self.world:
            result.update({
                'world': {
                    'name': self.world,
                    'owner': self.owner,
                    'bot': self.bot_name,
                    'status': self.world_status
                }
            })
            
        return result

class ProductCallbackManager:
    """Manager untuk mengelola callbacks product service"""
    def __init__(self):
        self.callbacks = {
            'product_created': [],
            'product_updated': [],
            'product_deleted': [],
            'stock_added': [],
            'stock_updated': [],
            'stock_sold': [],
            'stock_reduced': [],
            'world_updated': [],
            'error': []
        }
    
    def register(self, event_type: str, callback: Any):
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

class ProductManagerService(BaseLockHandler):
    """Core product manager service"""
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
            self.logger = logging.getLogger("ProductManagerService")
            self.cache_manager = CacheManager()
            self.callback_manager = ProductCallbackManager()
            self.setup_default_callbacks()
            self.initialized = True

    async def verify_dependencies(self) -> bool:
        """Verify all required dependencies are available"""
        try:
            # Verify database connection
            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                
                # Test products table
                cursor.execute("""
                    SELECT 1 FROM products 
                    LIMIT 1
                """)
                
                # Test stock table
                cursor.execute("""
                    SELECT 1 FROM stock
                    LIMIT 1
                """)
                
                return True
                
            except Exception as e:
                self.logger.error(f"Database verification failed: {e}")
                return False
            finally:
                if conn:
                    conn.close()
                    
        except Exception as e:
            self.logger.error(f"Failed to verify dependencies: {e}")
            return False

    def setup_default_callbacks(self):
        """Setup default callbacks untuk notifikasi"""
        
        async def notify_product_created(product: Dict):
            """Callback untuk notifikasi produk baru"""
            channel_id = NOTIFICATION_CHANNELS.get('product_logs')
            if channel := self.bot.get_channel(channel_id):
                embed = discord.Embed(
                    title="New Product Created",
                    description=f"Product: {product['name']} ({product['code']})",
                    color=COLORS.SUCCESS
                )
                embed.add_field(name="Price", value=f"{product['price']:,} WL")
                if product['description']:
                    embed.add_field(name="Description", value=product['description'])
                await channel.send(embed=embed)
        
        async def notify_stock_added(product_code: str, quantity: int, added_by: str):
            """Callback untuk notifikasi penambahan stock"""
            channel_id = NOTIFICATION_CHANNELS.get('stock_logs')
            if channel := self.bot.get_channel(channel_id):
                embed = discord.Embed(
                    title="Stock Added",
                    description=f"Product: {product_code}",
                    color=COLORS.INFO
                )
                embed.add_field(name="Quantity", value=str(quantity))
                embed.add_field(name="Added By", value=added_by)
                await channel.send(embed=embed)
        
        async def notify_stock_sold(product: Dict, buyer: str, quantity: int):
            """Callback untuk notifikasi penjualan"""
            channel_id = NOTIFICATION_CHANNELS.get('transactions')
            if channel := self.bot.get_channel(channel_id):
                embed = discord.Embed(
                    title="Product Sold",
                    description=f"Product: {product['name']} ({product['code']})",
                    color=COLORS.SUCCESS
                )
                embed.add_field(name="Buyer", value=buyer)
                embed.add_field(name="Quantity", value=str(quantity))
                embed.add_field(name="Total Price", value=f"{product['price'] * quantity:,} WL")
                await channel.send(embed=embed)

        async def notify_product_deleted(product: Dict, reason: str):
            """Callback untuk notifikasi product deletion"""
            channel_id = NOTIFICATION_CHANNELS.get('admin_logs')
            if channel := self.bot.get_channel(channel_id):
                embed = discord.Embed(
                    title="Product Deleted",
                    description=f"Product: {product['name']} ({product['code']})",
                    color=COLORS.WARNING
                )
                if reason:
                    embed.add_field(name="Reason", value=reason)
                await channel.send(embed=embed)
        
        async def notify_stock_reduced(product_code: str, quantity: int, reason: str, reduced_stocks: List[str]):
            """Callback untuk notifikasi pengurangan stock"""
            channel_id = NOTIFICATION_CHANNELS.get('admin_logs')
            if channel := self.bot.get_channel(channel_id):
                embed = discord.Embed(
                    title="Stock Reduced",
                    description=f"Product: {product_code}",
                    color=COLORS.WARNING
                )
                embed.add_field(name="Quantity", value=str(quantity))
                if reason:
                    embed.add_field(name="Reason", value=reason)
                await channel.send(embed=embed)
        
        # Register default callbacks
        self.callback_manager.register('product_created', notify_product_created)
        self.callback_manager.register('stock_added', notify_stock_added)
        self.callback_manager.register('stock_sold', notify_stock_sold)
        self.callback_manager.register('product_deleted', notify_product_deleted)
        self.callback_manager.register('stock_reduced', notify_stock_reduced)

    async def get_product(self, product_code: str) -> ProductManagerResponse:
        """Get product by code"""
        try:
            cache_key = f"product_{product_code}"
            cached = await self.cache_manager.get(cache_key)
            if cached:
                return ProductManagerResponse.success(cached)

            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                """
                SELECT * FROM products 
                WHERE code = ? COLLATE NOCASE 
                AND status != ?
                """,
                (product_code, Status.DELETED.value)
            )
            
            result = cursor.fetchone()
            if not result:
                return ProductManagerResponse.error("Product not found")
                
            product = dict(result)
            await self.cache_manager.set(
                cache_key,
                product,
                expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.MEDIUM)
            )
            
            return ProductManagerResponse.success(product)

        except Exception as e:
            self.logger.error(f"Error getting product: {e}")
            return ProductManagerResponse.error(str(e))
        finally:
            if conn:
                conn.close()

    async def get_all_products(self) -> ProductManagerResponse:
        """Get all available products"""
        try:
            cache_key = "all_products"
            cached = await self.cache_manager.get(cache_key)
            if cached:
                return ProductManagerResponse.success(cached)

            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                """
                SELECT * FROM products 
                WHERE status != ?
                ORDER BY category, priority, price DESC
                """,
                (Status.DELETED.value,)
            )
            
            products = [dict(row) for row in cursor.fetchall()]
            await self.cache_manager.set(
                cache_key,
                products,
                expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.SHORT)
            )
            
            return ProductManagerResponse.success(products)

        except Exception as e:
            self.logger.error(f"Error getting all products: {e}")
            return ProductManagerResponse.error(str(e))
        finally:
            if conn:
                conn.close()

    async def get_stock_count(self, product_code: str) -> ProductManagerResponse:
        """Get available stock count for product"""
        try:
            cache_key = f"stock_count_{product_code}"
            cached = await self.cache_manager.get(cache_key)
            if cached is not None:
                return ProductManagerResponse.success(cached)

            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                """
                SELECT COUNT(*) as count
                FROM stock
                WHERE product_code = ?
                AND status = ?
                """,
                (product_code, Status.AVAILABLE.value)
            )
            
            result = cursor.fetchone()
            count = result['count'] if result else 0
            
            await self.cache_manager.set(
                cache_key,
                count,
                expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.SHORT)
            )
            
            return ProductManagerResponse.success(count)

        except Exception as e:
            self.logger.error(f"Error getting stock count: {e}")
            return ProductManagerResponse.error(str(e))
        finally:
            if conn:
                conn.close()

    async def get_available_stock(
        self,
        product_code: str,
        quantity: int = 1
    ) -> ProductManagerResponse:
        """Get available stock items for product"""
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                """
                SELECT * FROM stock
                WHERE product_code = ?
                AND status = ?
                LIMIT ?
                """,
                (product_code, Status.AVAILABLE.value, quantity)
            )
            
            stocks = [dict(row) for row in cursor.fetchall()]
            if not stocks:
                return ProductManagerResponse.error("No stock available")
                
            return ProductManagerResponse.success(stocks)

        except Exception as e:
            self.logger.error(f"Error getting available stock: {e}")
            return ProductManagerResponse.error(str(e))
        finally:
            if conn:
                conn.close()

    async def update_stock_status(
        self,
        product_code: str,
        stock_ids: List[str],
        new_status: str,
        buyer_id: Optional[str] = None
    ) -> ProductManagerResponse:
        """Update stock status"""
        if not stock_ids:
            return ProductManagerResponse.error("No stock IDs provided")
            
        lock = await self.acquire_lock(f"stock_update_{product_code}")
        if not lock:
            return ProductManagerResponse.error(MESSAGES.ERROR['LOCK_ACQUISITION_FAILED'])

        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            placeholders = ','.join('?' * len(stock_ids))
            cursor.execute(
                f"""
                UPDATE stock
                SET status = ?,
                    buyer_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})
                AND product_code = ?
                AND status = ?
                """,
                (
                    new_status,
                    buyer_id,
                    *stock_ids,
                    product_code,
                    Status.AVAILABLE.value
                )
            )
            
            if cursor.rowcount != len(stock_ids):
                conn.rollback()
                return ProductManagerResponse.error("Failed to update all stock items")
                
            conn.commit()
            
            # Invalidate cache
            await self.cache_manager.delete(f"stock_count_{product_code}")
            for i in range(1, Stock.MAX_ITEMS + 1):
                await self.cache_manager.delete(f"stock_{product_code}_q{i}")
            
            return ProductManagerResponse.success(
                {'updated_count': cursor.rowcount}
            )

        except Exception as e:
            self.logger.error(f"Error updating stock status: {e}")
            if conn:
                conn.rollback()
            return ProductManagerResponse.error(str(e))
        finally:
            if conn:
                conn.close()
            self.release_lock(f"stock_update_{product_code}")

    async def delete_product(self, product_code: str, reason: str = "") -> ProductManagerResponse:
        """Delete product dengan proper locking dan cleanup"""
        lock = await self.acquire_lock(f"product_delete_{product_code}")
        if not lock:
            return ProductManagerResponse.error(MESSAGES.ERROR['LOCK_ACQUISITION_FAILED'])

        try:
            # Validasi product existence
            product_response = await self.get_product(product_code)
            if not product_response.success:
                return product_response

            conn = get_connection()
            cursor = conn.cursor()
            
            try:
                conn.execute("BEGIN TRANSACTION")
                
                # Soft delete product
                cursor.execute(
                    """
                    UPDATE products 
                    SET status = ?, 
                        deleted_at = CURRENT_TIMESTAMP,
                        delete_reason = ?
                    WHERE code = ? COLLATE NOCASE
                    """,
                    (Status.DELETED.value, reason, product_code)
                )
                
                # Mark all available stock as deleted
                cursor.execute(
                    """
                    UPDATE stock
                    SET status = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE product_code = ? 
                    AND status = ?
                    """,
                    (Status.DELETED.value, product_code, Status.AVAILABLE.value)
                )
                
                conn.commit()
                
                # Invalidate caches
                await self.cache_manager.delete(f"product_{product_code}")
                await self.cache_manager.delete("all_products")
                for i in range(1, Stock.MAX_ITEMS + 1):
                    await self.cache_manager.delete(f"stock_{product_code}_q{i}")
                
                # Trigger callback
                await self.callback_manager.trigger(
                    'product_deleted',
                    product_response.data,
                    reason
                )
                
                return ProductManagerResponse.success(
                    None,
                    f"Product {product_code} has been deleted successfully"
                )

            except Exception as e:
                if conn:
                    conn.rollback()
                raise e
                
        except Exception as e:
            self.logger.error(f"Error deleting product: {e}")
            return ProductManagerResponse.error(str(e))
        finally:
            if conn:
                conn.close()
            self.release_lock(f"product_delete_{product_code}")

    async def get_world_info(self) -> ProductManagerResponse:
        """Get world info from database"""
        try:
            cache_key = "world_info"
            cached = await self.cache_manager.get(cache_key)
            if cached:
                return ProductManagerResponse.success(cached)
                
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM world_info WHERE id = 1")
            result = cursor.fetchone()
            
            if result:
                world_info = {
                    'world': result['world_name'],
                    'owner': result['owner_id'],
                    'bot': result['bot_name'],
                    'status': result['status'],
                    'updated_at': result['updated_at']
                }
                
                await self.cache_manager.set(
                    cache_key,
                    world_info,
                    expires_in=300  # 5 menit cache
                )
                
                return ProductManagerResponse.success(world_info)
            
            return ProductManagerResponse.error("World info not found")
            
        except Exception as e:
            self.logger.error(f"Error getting world info: {e}")
            return ProductManagerResponse.error(str(e))
        finally:
            if conn:
                conn.close()

class ProductManagerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.product_service = ProductManagerService(bot)
        self.logger = logging.getLogger("ProductManagerCog")

    async def cog_load(self):
        self.logger.info("ProductManagerCog loading...")
        
    async def cog_unload(self):
        await self.product_service.cleanup()
        self.logger.info("ProductManagerCog unloaded")

async def setup(bot):
    if not hasattr(bot, 'product_manager_loaded'):
        cog = ProductManagerCog(bot)
        
        # Verify dependencies
        if not await cog.product_service.verify_dependencies():
            raise Exception("ProductManager dependencies verification failed")
            
        await bot.add_cog(cog)
        bot.product_manager_loaded = True
        logging.info(
            f'ProductManager cog loaded successfully at '
            f'{datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")} UTC'
        )

async def teardown(bot):
    """Cleanup when extension is unloaded"""
    try:
        if hasattr(bot, 'product_manager_loaded'):
            cog = bot.get_cog('ProductManagerCog')
            if cog:
                await bot.remove_cog('ProductManagerCog')
                await cog.product_service.cleanup()
            delattr(bot, 'product_manager_loaded')
            logging.info("ProductManager extension unloaded successfully")
    except Exception as e:
        logging.error(f"Error unloading ProductManager extension: {e}")