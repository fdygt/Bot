"""
Transaction Manager Service
Version: 2.0.0
Author: fdyytu2
Created at: 2025-03-07 18:04:56 UTC
Last Modified: 2025-03-16 05:07:32 UTC

Dependencies:
- database.py: For database connections
- base_handler.py: For lock management
- cache_manager.py: For caching functionality
- product_manager.py: For product operations
- balance_manager.py: For balance operations

Core Features:
1. Transaction Processing
   - Purchase processing
   - Deposit processing
   - Batch transaction handling
   - Transaction queue management
   
2. Security & Validation
   - Input validation
   - Balance verification
   - Stock verification
   - Transaction limits
   - Lock management
   
3. Integration
   - Balance Manager integration
   - Product Manager integration
   - Event/Callback system
   
4. Monitoring & Recovery
   - Performance monitoring
   - Transaction recovery
   - Error handling
   - Logging system
   
5. Utilities
   - Transaction history
   - Analytics
   - Notifications
   - Cache management
"""

import logging
import asyncio
from typing import Optional, Dict, List, Union, Callable, Any
from datetime import datetime, timedelta
import discord
from discord.ext import commands

from .constants import (
    Status,
    TransactionType,
    Balance,
    TransactionError,
    MESSAGES,
    CACHE_TIMEOUT,
    COLORS,
    EVENTS,
    NOTIFICATION_CHANNELS,
    CURRENCY_RATES
)
from database import get_connection
from .base_handler import BaseLockHandler
from .cache_manager import CacheManager
from .product_manager import ProductManagerService
from .balance_manager import BalanceManagerService

# Custom Exceptions
class ValidationError(TransactionError):
    """Raised when validation fails"""
    pass

class LockError(TransactionError):
    """Raised when lock acquisition fails"""
    pass

class ProcessingError(TransactionError):
    """Raised when transaction processing fails"""
    pass

class InsufficientBalanceError(TransactionError):
    """Raised when balance is insufficient"""
    pass

class StockError(TransactionError):
    """Raised when stock-related issues occur"""
    pass

class TransactionMonitor:
    """Monitor transaction performance and status"""
    def __init__(self):
        self.start_time = None
        self.steps = []
        
    def start(self):
        """Start monitoring transaction"""
        self.start_time = datetime.utcnow()
        
    def add_step(self, step_name: str):
        """Add processing step with timestamp"""
        if self.start_time:
            elapsed = (datetime.utcnow() - self.start_time).total_seconds()
            self.steps.append({
                'step': step_name,
                'elapsed': elapsed,
                'timestamp': datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            })
            
    def get_report(self) -> Dict:
        """Get monitoring report"""
        if not self.start_time:
            return {}
            
        total_time = (datetime.utcnow() - self.start_time).total_seconds()
        return {
            'total_time': total_time,
            'steps': self.steps,
            'start_time': self.start_time.strftime("%Y-%m-%d %H:%M:%S"),
            'end_time': datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        }

class TransactionQueue:
    """Queue sistem untuk mengelola transaksi"""
    def __init__(self):
        self.queue = asyncio.Queue()
        self._processing = False
        self.logger = logging.getLogger("TransactionQueue")
        self.monitor = TransactionMonitor()

    async def add_transaction(self, transaction: Dict):
        """Add transaction to queue"""
        await self.queue.put(transaction)
        if not self._processing:
            await self.process_queue()

    async def process_queue(self):
        """Process transactions in queue"""
        self._processing = True
        self.monitor.start()
        
        while not self.queue.empty():
            trx = await self.queue.get()
            try:
                self.monitor.add_step(f"processing_transaction_{trx.get('type', 'unknown')}")
                await self.process_transaction(trx)
            except Exception as e:
                self.logger.error(f"Error processing transaction: {e}")
            finally:
                self.queue.task_done()
                
        self._processing = False
        self.logger.info(
            "Queue processing completed",
            extra={'performance': self.monitor.get_report()}
        )

class TransactionValidator:
    """Validator untuk transaksi"""
    @staticmethod
    async def validate_purchase(
        user_id: str,
        product_code: str, 
        quantity: int
    ) -> None:
        """Validate purchase transaction inputs"""
        if not user_id or not str(user_id).isdigit():
            raise ValidationError("Invalid user ID")
            
        if not product_code or len(product_code) < 3:
            raise ValidationError("Invalid product code")
            
        if quantity <= 0 or quantity > 999:
            raise ValidationError("Invalid quantity (must be between 1-999)")

    @staticmethod
    async def validate_deposit(
        user_id: str, 
        amount: Dict[str, int]
    ) -> None:
        """Validate deposit transaction inputs"""
        if not user_id or not str(user_id).isdigit():
            raise ValidationError("Invalid user ID")
            
        if not amount or sum(amount.values()) <= 0:
            raise ValidationError("Invalid deposit amount")
            
        for currency, value in amount.items():
            if currency not in CURRENCY_RATES.SUPPORTED:
                raise ValidationError(f"Unsupported currency: {currency}")
            if value < 0:
                raise ValidationError("Negative amounts not allowed")

class TransactionResponse:
    """Response handler for transactions"""
    def __init__(
        self,
        success: bool,
        transaction_type: str = "",
        data: Any = None,
        message: str = "",
        error: str = "",
        product_response: Any = None,
        balance_response: Any = None
    ):
        self.success = success
        self.transaction_type = transaction_type
        self.data = data
        self.message = message
        self.error = error
        self.product_data = product_response
        self.balance_data = balance_response
        self.timestamp = datetime.utcnow()
        self.performance = None

    @classmethod
    def success(
        cls,
        transaction_type: str,
        data: Any = None,
        message: str = "",
        product_response: Any = None,
        balance_response: Any = None
    ) -> 'TransactionResponse':
        return cls(
            True,
            transaction_type,
            data,
            message,
            product_response=product_response,
            balance_response=balance_response
        )

    @classmethod
    def error(
        cls,
        error: str,
        message: str = ""
    ) -> 'TransactionResponse':
        return cls(False, "", None, message, error)

    def add_performance_data(self, monitor: TransactionMonitor):
        """Add performance monitoring data"""
        self.performance = monitor.get_report()
        return self

    def to_dict(self) -> Dict:
        """Convert response to dictionary"""
        result = {
            'success': self.success,
            'transaction_type': self.transaction_type,
            'data': self.data,
            'message': self.message,
            'error': self.error,
            'timestamp': self.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        if self.performance:
            result['performance'] = self.performance
            
        if self.product_data:
            result['product_data'] = self.product_data
            
        if self.balance_data:
            result['balance_data'] = self.balance_data
            
        return result

class TransactionCallbackManager:
    """Manager for transaction callbacks"""
    def __init__(self):
        self.callbacks = {
            'transaction_started': [],
            'transaction_completed': [],
            'transaction_failed': [],
            'purchase_completed': [],
            'deposit_completed': [],
            'withdrawal_completed': [],
            'batch_completed': [],
            'recovery_attempted': [],
            'error': []
        }
    
    def register(self, event_type: str, callback: Callable):
        """Register callback for event"""
        if event_type in self.callbacks:
            self.callbacks[event_type].append(callback)
    
    async def trigger(self, event_type: str, *args: Any, **kwargs: Any):
        """Trigger callbacks for event"""
        if event_type in self.callbacks:
            for callback in self.callbacks[event_type]:
                try:
                    await callback(*args, **kwargs)
                except Exception as e:
                    logging.error(f"Error in {event_type} callback: {e}")
class TransactionManager(BaseLockHandler):
    """Core transaction manager service"""
    def __init__(self, bot):
        if not hasattr(self, 'initialized'):
            super().__init__()
            self.bot = bot
            self.logger = logging.getLogger("TransactionManager")
            self.cache_manager = CacheManager()
            self.product_manager = ProductManagerService(bot)
            self.balance_manager = BalanceManagerService(bot)
            self.callback_manager = TransactionCallbackManager()
            self.transaction_queue = TransactionQueue()
            self.validator = TransactionValidator()
            self.setup_default_callbacks()
            self.initialized = True
            self.logger.info("TransactionManager initialized")

    def setup_default_callbacks(self):
        """Setup default notification callbacks"""
        
        async def notify_transaction_completed(**data):
            """Notify when transaction completes"""
            channel_id = NOTIFICATION_CHANNELS.get('transactions')
            if channel := self.bot.get_channel(channel_id):
                embed = self._create_transaction_embed(data)
                await channel.send(embed=embed)
        
        async def notify_large_transaction(**data):
            """Notify for large transactions"""
            if 'total_wl' in data and data['total_wl'] > 100000:
                channel_id = NOTIFICATION_CHANNELS.get('admin_logs')
                if channel := self.bot.get_channel(channel_id):
                    embed = discord.Embed(
                        title="⚠️ Large Transaction Alert",
                        description="Transaction above 100K WL detected",
                        color=COLORS.WARNING
                    )
                    for key, value in data.items():
                        embed.add_field(
                            name=key.replace('_', ' ').title(),
                            value=str(value)
                        )
                    await channel.send(embed=embed)
        
        # Register default callbacks
        self.callback_manager.register(
            'transaction_completed',
            notify_transaction_completed
        )
        self.callback_manager.register(
            'transaction_completed',
            notify_large_transaction
        )

    async def process_purchase(
        self,
        user_id: str,
        product_code: str, 
        quantity: int = 1
    ) -> TransactionResponse:
        """Process purchase transaction"""
        monitor = TransactionMonitor()
        monitor.start()
        
        try:
            # Validate input
            monitor.add_step("validation_start")
            await self.validator.validate_purchase(
                user_id,
                product_code,
                quantity
            )
            monitor.add_step("validation_complete")

            # Lock acquisition
            monitor.add_step("lock_acquisition_start")
            lock = await self.acquire_lock(f"purchase_{user_id}_{product_code}")
            if not lock:
                raise LockError(MESSAGES.ERROR['LOCK_ACQUISITION_FAILED'])
            monitor.add_step("lock_acquisition_complete")

            try:
                # Get GrowID and validate
                monitor.add_step("growid_validation_start")
                growid_response = await self.balance_manager.get_growid(user_id)
                if not growid_response.success:
                    raise ValidationError(growid_response.error)
                growid = growid_response.data
                monitor.add_step("growid_validation_complete")

                # Get product and validate
                monitor.add_step("product_validation_start")
                product_response = await self.product_manager.get_product(product_code)
                if not product_response.success:
                    raise ValidationError(product_response.error)
                product = product_response.data
                monitor.add_step("product_validation_complete")

                # Get stock and validate
                monitor.add_step("stock_validation_start")
                stock_response = await self.product_manager.get_available_stock(
                    product_code,
                    quantity
                )
                if not stock_response.success:
                    raise ValidationError(stock_response.error)
                available_stock = stock_response.data
                monitor.add_step("stock_validation_complete")

                # Calculate total price
                total_price = product['price'] * quantity

                # Verify balance and limits
                monitor.add_step("balance_validation_start")
                balance_response = await self.balance_manager.get_balance(growid)
                if not balance_response.success:
                    raise ValidationError(balance_response.error)
                current_balance = balance_response.data

                if total_price > current_balance.total_wl():
                    raise InsufficientBalanceError(MESSAGES.ERROR['INSUFFICIENT_BALANCE'])

                # Check daily limit
                daily_limit_response = await self.balance_manager.check_daily_limit(
                    growid,
                    total_price
                )
                if not daily_limit_response.success:
                    raise ValidationError(daily_limit_response.error)
                monitor.add_step("balance_validation_complete")

                # Process transaction
                monitor.add_step("transaction_processing_start")
                
                # Update stock
                stock_update = await self.product_manager.update_stock_status(
                    product_code,
                    [item['id'] for item in available_stock[:quantity]],
                    Status.SOLD.value,
                    user_id
                )
                if not stock_update.success:
                    raise ProcessingError(stock_update.error)

                # Update balance
                balance_update = await self.balance_manager.update_balance(
                    growid=growid,
                    wl=-total_price,
                    details=f"Purchase {quantity}x {product['name']}",
                    transaction_type=TransactionType.PURCHASE.value
                )
                if not balance_update.success:
                    # Rollback stock if balance update fails
                    await self.product_manager.update_stock_status(
                        product_code,
                        [item['id'] for item in available_stock[:quantity]],
                        Status.AVAILABLE.value,
                        None
                    )
                    raise ProcessingError(balance_update.error)
                
                monitor.add_step("transaction_processing_complete")

                # Send notifications
                monitor.add_step("notification_start")
                await self._send_transaction_notification(
                    user_id,
                    TransactionType.PURCHASE.value,
                    {
                        'product': product['name'],
                        'quantity': quantity,
                        'total_price': total_price,
                        'new_balance': balance_update.data,
                        'performance': monitor.get_report()
                    }
                )
                monitor.add_step("notification_complete")

                # Create success response
                response = TransactionResponse.success(
                    transaction_type=TransactionType.PURCHASE.value,
                    data={
                        'product': product,
                        'quantity': quantity,
                        'total_price': total_price,
                        'content': [item['content'] for item in available_stock[:quantity]],
                        'performance': monitor.get_report()
                    },
                    message=f"Successfully purchased {quantity}x {product['name']}",
                    product_response=product_response,
                    balance_response=balance_update
                )

                # Trigger completion callback
                await self.callback_manager.trigger(
                    'transaction_completed',
                    transaction_type=TransactionType.PURCHASE.value,
                    user_id=user_id,
                    product_code=product_code,
                    quantity=quantity,
                    total_price=total_price,
                    performance=monitor.get_report()
                )

                return response

            except Exception as e:
                self.logger.error(f"Error in purchase transaction: {e}")
                await self.callback_manager.trigger(
                    'transaction_failed',
                    error=str(e),
                    user_id=user_id
                )
                raise
            finally:
                self.release_lock(f"purchase_{user_id}_{product_code}")

        except (ValidationError, LockError, ProcessingError, InsufficientBalanceError) as e:
            return TransactionResponse.error(str(e))
        except Exception as e:
            self.logger.error(f"Unexpected error in purchase: {e}")
            return TransactionResponse.error(MESSAGES.ERROR['TRANSACTION_FAILED'])

    async def process_deposit(
        self,
        user_id: str,
        wl: int = 0,
        dl: int = 0,
        bgl: int = 0
    ) -> TransactionResponse:
        """Process deposit transaction"""
        monitor = TransactionMonitor()
        monitor.start()
        
        try:
            # Validation
            monitor.add_step("validation_start")
            await self.validator.validate_deposit(
                user_id,
                {'wl': wl, 'dl': dl, 'bgl': bgl}
            )
            monitor.add_step("validation_complete")

            # Lock acquisition
            lock = await self.acquire_lock(f"deposit_{user_id}")
            if not lock:
                raise LockError(MESSAGES.ERROR['LOCK_ACQUISITION_FAILED'])

            try:
                # Get GrowID
                monitor.add_step("growid_validation_start")
                growid_response = await self.balance_manager.get_growid(user_id)
                if not growid_response.success:
                    raise ValidationError(growid_response.error)
                growid = growid_response.data
                monitor.add_step("growid_validation_complete")

                # Calculate total
                total_wl = wl + (dl * CURRENCY_RATES.RATES['DL']) + (bgl * CURRENCY_RATES.RATES['BGL'])

                # Process deposit
                monitor.add_step("deposit_processing_start")
                balance_response = await self.balance_manager.update_balance(
                    growid=growid,
                    wl=wl,
                    dl=dl,
                    bgl=bgl,
                    details=f"Deposit: {wl}WL, {dl}DL, {bgl}BGL",
                    transaction_type=TransactionType.DEPOSIT.value
                )

                if not balance_response.success:
                    raise ProcessingError(balance_response.error)
                monitor.add_step("deposit_processing_complete")

                # Send notification
                monitor.add_step("notification_start")
                await self._send_transaction_notification(
                    user_id,
                    TransactionType.DEPOSIT.value,
                    {
                        'amount': f"{wl}WL, {dl}DL, {bgl}BGL",
                        'total_wl': total_wl,
                        'new_balance': balance_response.data,
                        'performance': monitor.get_report()
                    }
                )
                monitor.add_step("notification_complete")

                # Create success response
                response = TransactionResponse.success(
                    transaction_type=TransactionType.DEPOSIT.value,
                    data={'total_deposited': total_wl},
                    message=f"Successfully deposited {total_wl:,} WL",
                    balance_response=balance_response
                ).add_performance_data(monitor)

                # Trigger completion callback
                await self.callback_manager.trigger(
                    'transaction_completed',
                    transaction_type=TransactionType.DEPOSIT.value,
                    user_id=user_id,
                    amount={'wl': wl, 'dl': dl, 'bgl': bgl},
                    total_wl=total_wl,
                    performance=monitor.get_report()
                )

                return response

            finally:
                self.release_lock(f"deposit_{user_id}")

        except (ValidationError, LockError, ProcessingError) as e:
            return TransactionResponse.error(str(e))
        except Exception as e:
            self.logger.error(f"Error processing deposit: {e}")
            return TransactionResponse.error(MESSAGES.ERROR['TRANSACTION_FAILED'])

    async def process_batch_transaction(
        self,
        transactions: List[Dict[str, Any]]
    ) -> List[TransactionResponse]:
        """Process multiple transactions in batch"""
        monitor = TransactionMonitor()
        monitor.start()
        
        results = []
        for trx in transactions:
            try:
                monitor.add_step(f"processing_{trx['type']}")
                if trx['type'] == TransactionType.PURCHASE.value:
                    result = await self.process_purchase(
                        trx['user_id'],
                        trx['product_code'],
                        trx['quantity']
                    )
                elif trx['type'] == TransactionType.DEPOSIT.value:
                    result = await self.process_deposit(
                        trx['user_id'],
                        **trx['amount']
                    )
                else:
                    result = TransactionResponse.error(
                        f"Unsupported transaction type: {trx['type']}"
                    )
                results.append(result)
            except Exception as e:
                self.logger.error(f"Error in batch transaction: {e}")
                results.append(TransactionResponse.error(str(e)))

        # Trigger batch completion callback
        await self.callback_manager.trigger(
            'batch_completed',
            results=results,
            performance=monitor.get_report()
        )

        return results

    async def get_transaction_history(
        self,
        user_id: str,
        limit: int = 10,
        offset: int = 0,
        transaction_type: str = None,
        start_date: datetime = None,
        end_date: datetime = None
    ) -> TransactionResponse:
        """Get transaction history"""
        try:
            # Get GrowID
            growid_response = await self.balance_manager.get_growid(user_id)
            if not growid_response.success:
                return TransactionResponse.error(growid_response.error)
            growid = growid_response.data

            # Get history from balance manager
            history_response = await self.balance_manager.get_transaction_history(
                growid,
                limit=limit,
                offset=offset,
                transaction_type=transaction_type,
                start_date=start_date,
                end_date=end_date
            )

            if not history_response.success:
                return TransactionResponse.error(history_response.error)

            transactions = history_response.data
            if not transactions:
                return TransactionResponse.error(MESSAGES.ERROR['NO_HISTORY'])

            # Format transactions
            formatted_transactions = []
            for trx in transactions:
                formatted_trx = self._format_transaction(trx)
                if formatted_trx:
                    formatted_transactions.append(formatted_trx)

            return TransactionResponse.success(
                transaction_type='history',
                data=formatted_transactions,
                message=f"Found {len(formatted_transactions)} transactions"
            )

        except Exception as e:
            self.logger.error(f"Error getting transaction history: {e}")
            return TransactionResponse.error(MESSAGES.ERROR['HISTORY_FAILED'])

    async def _send_transaction_notification(
        self,
        user_id: str,
        transaction_type: str,
        details: Dict
    ) -> None:
        """Send transaction notification"""
        try:
            user = await self.bot.fetch_user(int(user_id))
            if not user:
                return

            embed = self._create_transaction_embed(
                {
                    'type': transaction_type,
                    'user_id': user_id,
                    **details
                }
            )
            
            await user.send(embed=embed)
        except Exception as e:
            self.logger.error(f"Error sending notification: {e}")

    def _create_transaction_embed(self, data: Dict) -> discord.Embed:
        """Create transaction embed"""
        transaction_type = data.get('type', 'Unknown')
        embed = discord.Embed(
            title=f"{'✅' if data.get('success', True) else '❌'} {transaction_type.title()} Transaction",
            color=COLORS.SUCCESS if data.get('success', True) else COLORS.ERROR,
            timestamp=datetime.utcnow()
        )

        # Add main transaction info
        for key, value in data.items():
            if key not in ['type', 'success', 'performance']:
                embed.add_field(
                    name=key.replace('_', ' ').title(),
                    value=str(value),
                    inline=False
                )

        # Add performance data if available
        if 'performance' in data:
            perf_data = data['performance']
            if perf_data:
                perf_text = f"Total Time: {perf_data['total_time']:.2f}s\n"
                for step in perf_data['steps']:
                    perf_text += f"• {step['step']}: {step['elapsed']:.2f}s\n"
                embed.add_field(
                    name="Performance",
                    value=f"```\n{perf_text}```",
                    inline=False
                )

        return embed

    def _format_transaction(self, transaction: Dict) -> Optional[Dict]:
        """Format transaction data"""
        try:
            # Format timestamp
            created_at = datetime.fromisoformat(transaction['created_at'].replace('Z', '+00:00'))
            formatted_date = created_at.strftime('%Y-%m-%d %H:%M:%S UTC')

            # Calculate balance change
            old_balance = Balance.from_string(transaction['old_balance'])
            new_balance = Balance.from_string(transaction['new_balance'])
            balance_change = new_balance.total_wl() - old_balance.total_wl()

            # Format response
            return {
                'id': transaction['id'],
                'type': transaction['type'],
                'date': formatted_date,
                'amount': self._format_amount(abs(balance_change)),
                'change': f"{'+' if balance_change >= 0 else '-'}{self._format_amount(abs(balance_change))}",
                'details': transaction['details'],
                'status': transaction['status'],
                'old_balance': old_balance.format(),
                'new_balance': new_balance.format()
            }
        except Exception as e:
            self.logger.error(f"Error formatting transaction: {e}")
            return None

    def _format_amount(self, amount: int) -> str:
        """Format amount with currency rates"""
        try:
            if amount >= CURRENCY_RATES.RATES['BGL']:
                return f"{amount/CURRENCY_RATES.RATES['BGL']:.1f} BGL"
            elif amount >= CURRENCY_RATES.RATES['DL']:
                return f"{amount/CURRENCY_RATES.RATES['DL']:.0f} DL"
            return f"{amount:,} WL"
        except Exception as e:
            self.logger.error(f"Error formatting amount: {e}")
            return f"{amount:,} WL"

    async def monitor_pending_transactions(self):
        """Monitor and recover pending transactions"""
        while True:
            try:
                # Get pending transactions
                conn = get_connection()
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT * FROM transactions 
                    WHERE status = 'pending' 
                    AND created_at <= datetime('now', '-5 minutes')
                """)
                
                pending = cursor.fetchall()
                
                for trx in pending:
                    try:
                        await self.recover_failed_transaction(trx['id'])
                    except Exception as e:
                        self.logger.error(f"Error recovering transaction {trx['id']}: {e}")
                
                await asyncio.sleep(300)  # Check every 5 minutes
                
            except Exception as e:
                self.logger.error(f"Error monitoring transactions: {e}")
                await asyncio.sleep(300)
            finally:
                if conn:
                    conn.close()

    async def recover_failed_transaction(self, transaction_id: str) -> TransactionResponse:
        """
        Attempt to recover failed transaction
        
        Args:
            transaction_id: ID of failed transaction
            
        Returns:
            TransactionResponse: Recovery attempt result
        """
        try:
            # Get transaction details
            transaction = await self._get_transaction(transaction_id)
            if not transaction:
                return TransactionResponse.error("Transaction not found")

            if transaction['status'] != 'failed':
                return TransactionResponse.error("Transaction is not in failed state")

            # Attempt recovery based on transaction type
            if transaction['type'] == TransactionType.PURCHASE.value:
                return await self.recover_purchase(transaction)
            elif transaction['type'] == TransactionType.DEPOSIT.value:
                return await self.recover_deposit(transaction)
            else:
                return TransactionResponse.error(f"Recovery not supported for type: {transaction['type']}")

        except Exception as e:
            self.logger.error(f"Error recovering transaction: {e}")
            return TransactionResponse.error(str(e))

class TransactionCog(commands.Cog):
    """Discord cog for transaction management"""
    def __init__(self, bot):
        self.bot = bot
        self.trx_manager = TransactionManager(bot)
        self.logger = logging.getLogger("TransactionCog")

    async def cog_load(self):
        """Setup when cog is loaded"""
        self.logger.info("TransactionCog loading...")
        
        # Start monitoring task
        self.bot.loop.create_task(self.trx_manager.monitor_pending_transactions())

    async def cog_unload(self):
        """Cleanup when cog is unloaded"""
        self.logger.info("TransactionCog unloaded")

async def setup(bot):
    """Setup cog with proper error handling"""
    try:
        if not hasattr(bot, 'transaction_manager_loaded'):
            # Verify dependencies
            product_manager = ProductManagerService(bot)
            balance_manager = BalanceManagerService(bot)
            
            if not hasattr(bot, 'product_manager_loaded'):
                raise Exception("ProductManager must be loaded before TransactionManager")
            if not hasattr(bot, 'balance_manager_loaded'):
                raise Exception("BalanceManager must be loaded before TransactionManager")
                
            # Add cog
            cog = TransactionCog(bot)
            await bot.add_cog(cog)
            bot.transaction_manager_loaded = True
            
            logging.info(
                f'Transaction Manager loaded successfully at '
                f'{datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")} UTC'
            )
    except Exception as e:
        logging.error(f"Failed to load TransactionManager: {e}")
        if hasattr(bot, 'transaction_manager_loaded'):
            delattr(bot, 'transaction_manager_loaded')
        raise

async def teardown(bot):
    """Cleanup when extension is unloaded"""
    try:
        if hasattr(bot, 'transaction_manager_loaded'):
            cog = bot.get_cog('TransactionCog')
            if cog:
                await bot.remove_cog('TransactionCog')
            delattr(bot, 'transaction_manager_loaded')
            logging.info("Transaction Manager unloaded successfully")
    except Exception as e:
        logging.error(f"Error unloading TransactionManager: {e}")