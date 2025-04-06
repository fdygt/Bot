"""
Constants for Store DC Bot
Author: fdyytu
Created at: 2025-03-07 18:04:56 UTC
Last Modified: 2025-03-10 10:09:16 UTC

Updates:
- Added VERSION tracking
- Added TIMEOUTS settings
- Added LIVE_STATUS states
- Updated MESSAGES
- Enhanced error handling
"""

import discord
from enum import Enum, auto
from typing import Dict, Union, List
from datetime import timedelta

class Permissions(Enum):
    """Permission levels for commands"""
    USER = auto()
    HELPER = auto()
    MOD = auto()
    ADMIN = auto()
    OWNER = auto()
    
# Version Tracking
class VERSION:
    """Version tracking for components"""
    LIVE_STOCK = "1.0.0"
    LIVE_BUTTONS = "1.0.0"
    PRODUCT = "1.0.0"
    BALANCE = "1.0.0"
    TRANSACTION = "1.0.0"
    ADMIN = "1.0.0"

# System Timeouts
class TIMEOUTS:
    """System timeout settings"""
    INITIALIZATION = 30  # 30 seconds
    LOCK_ACQUISITION = 3  # 3 seconds
    SERVICE_CALL = 5    # 5 seconds
    CACHE_OPERATION = 2 # 2 seconds
    SYNC_RETRY = 5     # 5 seconds retry interval
    MAX_RETRIES = 3    # Maximum number of retries

# Live System Status States
class LIVE_STATUS:
    """Live system status states"""
    INITIALIZING = "initializing"
    READY = "ready"
    MAINTENANCE = "maintenance"
    ERROR = "error"
    SYNCING = "syncing"
    RECOVERING = "recovering"
    SHUTDOWN = "shutdown"

# Cog Loading Status
COG_LOADED = {
    'PRODUCT': 'product_manager_loaded',
    'BALANCE': 'balance_manager_loaded',
    'TRANSACTION': 'transaction_manager_loaded',
    'LIVE_STOCK': 'live_stock_loaded', 
    'LIVE_BUTTONS': 'live_buttons_loaded',
    'ADMIN': 'admin_service_loaded'
}

# File Size Settings
MAX_STOCK_FILE_SIZE = 5 * 1024 * 1024  # 5MB max file size for stock files
MAX_ATTACHMENT_SIZE = 8 * 1024 * 1024  # 8MB max attachment size
MAX_EMBED_SIZE = 6000  # Discord embed character limit

# Valid Stock Formats
VALID_STOCK_FORMATS = ['txt']  # Format file yang diizinkan untuk stock

# Di constants.py, tambahkan TRANSFER_ROLLBACK ke TransactionType
class TransactionType(Enum):
    PURCHASE = "purchase"
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    DONATION = "donation"
    ADMIN_ADD = "admin_add"
    ADMIN_REMOVE = "admin_remove" 
    ADMIN_RESET = "admin_reset"
    REFUND = "refund"
    TRANSFER = "transfer"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"
    TRANSFER_ROLLBACK = "transfer_rollback" # Tambahkan ini

# Status untuk database
class Status(Enum):
    AVAILABLE = "available"  # Status di database
    SOLD = "sold"          # Status di database
    DELETED = "deleted"    # Status di database

# Balance Class yang lengkap
class Balance:
    def __init__(self, wl: int = 0, dl: int = 0, bgl: int = 0):
        self.wl = max(0, wl)
        self.dl = max(0, dl)
        self.bgl = max(0, bgl)
        self.MIN_AMOUNT = 0
        self.MAX_AMOUNT = 1000000  # 1M WLS
        self.DEFAULT_AMOUNT = 0
        self.DONATION_MIN = 10     # 10 WLS minimum donation

    def total_wl(self) -> int:
        """Convert semua balance ke WL"""
        return self.wl + (self.dl * 100) + (self.bgl * 10000)

    def format(self) -> str:
        """Format balance untuk display"""
        parts = []
        if self.bgl > 0:
            parts.append(f"{self.bgl:,} BGL")
        if self.dl > 0:
            parts.append(f"{self.dl:,} DL")
        if self.wl > 0 or not parts:
            parts.append(f"{self.wl:,} WL")
        return ", ".join(parts)

    @classmethod
    def from_wl(cls, total_wl: int) -> 'Balance':
        """Buat Balance object dari total WL"""
        bgl = total_wl // 10000
        remaining = total_wl % 10000
        dl = remaining // 100
        wl = remaining % 100
        return cls(wl, dl, bgl)

    @classmethod
    def from_string(cls, balance_str: str) -> 'Balance':
        """Create Balance object from string representation"""
        try:
            if not balance_str:
                return cls()
            parts = balance_str.split(',')
            wl = dl = bgl = 0
            for part in parts:
                part = part.strip()
                if 'WL' in part:
                    wl = int(part.replace('WL', '').strip())
                elif 'DL' in part:
                    dl = int(part.replace('DL', '').strip())
                elif 'BGL' in part:
                    bgl = int(part.replace('BGL', '').strip())
            return cls(wl, dl, bgl)
        except Exception:
            return cls()

    def __eq__(self, other):
        if not isinstance(other, Balance):
            return False
        return self.total_wl() == other.total_wl()

    def __str__(self):
        return self.format()

    def validate(self) -> bool:
        """Validasi balance"""
        total = self.total_wl()
        return self.MIN_AMOUNT <= total <= self.MAX_AMOUNT

class EXTENSIONS:
    # Core services harus diload pertama dan berurutan
    SERVICES: List[str] = [
        'ext.admin_service',     # Load 1st - independent
        'ext.product_manager',   # Load 2nd - independent
        'ext.balance_manager',   # Load 3rd - depends on product_manager
        'ext.trx'               # Load 4th - depends on both above
    ]
    
    # Core features yang bergantung pada services
    FEATURES: List[str] = [
        'ext.live_stock',      # Load after services
        'ext.live_buttons'     # Load last, depends on live_stock
    ]
    
    # Optional cogs - load terakhir
    COGS: List[str] = [
        'cogs.stats',
        'cogs.automod',
        'cogs.tickets',
        'cogs.welcome', 
        'cogs.leveling',
        'cogs.admin',
        'cogs.help_manager'
    ]
    
    @classmethod
    def get_all(cls) -> List[str]:
        """Get all extensions in proper loading order"""
        return cls.SERVICES + cls.FEATURES + cls.COGS
    
    @classmethod
    def verify_loaded(cls, bot) -> bool:
        """Verifikasi semua service sudah terload dengan benar"""
        required_services = [
            'ProductManagerCog',
            'BalanceManagerCog', 
            'TransactionCog'
        ]
        return all(bot.get_cog(service) for service in required_services)
        
class LIMITS:
    """Transaction and system limits"""
    DEFAULT_DAILY_LIMIT = 1_000_000  # 1M WL default daily limit
    MAX_PURCHASE_QUANTITY = 999      # Maximum items per purchase
    MAX_DAILY_TRANSACTIONS = 100     # Maximum transactions per day
    MIN_PURCHASE_AMOUNT = 1          # Minimum purchase amount
    MAX_TRANSFER_AMOUNT = 10_000_000 # Maximum transfer amount (10M WL)
    MAX_DEPOSIT_AMOUNT = 100_000_000 # Maximum deposit amount (100M WL)
    COOLDOWN_SECONDS = 3             # Cooldown between transactions
    MAX_FAILED_ATTEMPTS = 5          # Maximum failed attempts before temporary block
    BLOCK_DURATION = 30              # Minutes to block after max failed attempts
    SUSPICIOUS_THRESHOLD = 50_000    # Amount that triggers extra verification
    
    @classmethod
    def get_daily_usage_limit(cls, user_level: int) -> int:
        """Get daily usage limit based on user level"""
        limits = {
            0: cls.DEFAULT_DAILY_LIMIT,    # Default
            1: cls.DEFAULT_DAILY_LIMIT * 2, # VIP
            2: cls.DEFAULT_DAILY_LIMIT * 5, # Premium
            3: cls.DEFAULT_DAILY_LIMIT * 10 # Admin
        }
        return limits.get(user_level, cls.DEFAULT_DAILY_LIMIT)


# Currency Settings
class CURRENCY_RATES:
    # Base rates (in WL)
    RATES: Dict[str, int] = {
        'WL': 1,        # 1 WL = 1 WL (base)
        'DL': 100,      # 1 DL = 100 WL
        'BGL': 10000    # 1 BGL = 10000 WL
    }
    
    # Default currency
    DEFAULT = 'WL'
    
    # Supported currencies
    SUPPORTED = ['WL', 'DL', 'BGL']
    
    # Minimum amounts for each currency
    MIN_AMOUNTS = {
        'WL': 1,
        'DL': 1,
        'BGL': 1
    }
    
    # Maximum amounts for each currency
    MAX_AMOUNTS = {
        'WL': 10000,
        'DL': 100,
        'BGL': 10
    }
    
    # Display formats
    FORMATS = {
        'WL': '{:,} WL',
        'DL': '{:,} DL',
        'BGL': '{:,} BGL'
    }
    
    @classmethod
    def to_wl(cls, amount: Union[int, float], currency: str) -> float:
        """Convert any currency to WL"""
        if currency not in cls.SUPPORTED:
            raise ValueError(f"Mata uang tidak didukung: {currency}")
        return float(amount) * cls.RATES[currency]
    
    @classmethod
    def from_wl(cls, wl_amount: Union[int, float], to_currency: str) -> float:
        """Convert WL to any currency"""
        if to_currency not in cls.SUPPORTED:
            raise ValueError(f"Mata uang tidak didukung: {to_currency}")
        return float(wl_amount) / cls.RATES[to_currency]
    
    @classmethod
    def convert(cls, amount: Union[int, float], from_currency: str, to_currency: str) -> float:
        """Convert between currencies"""
        wl_amount = cls.to_wl(amount, from_currency)
        return cls.from_wl(wl_amount, to_currency)
    
    @classmethod
    def format(cls, amount: Union[int, float], currency: str) -> str:
        """Format amount in specified currency"""
        if currency not in cls.FORMATS:
            raise ValueError(f"Mata uang tidak didukung: {currency}")
        return cls.FORMATS[currency].format(amount)

# Stock Settings
class Stock:
    MAX_ITEMS = 1000
    MIN_ITEMS = 0
    UPDATE_BATCH_SIZE = 50
    ALERT_THRESHOLD = 10
    MAX_STOCK = 999999
    MIN_STOCK = 0
    MIN_PRICE = 1
    MAX_PRICE = 999999999  # atau sesuai
    
# Discord Colors
class COLORS:
    SUCCESS = discord.Color.green()
    ERROR = discord.Color.red()
    WARNING = discord.Color.yellow()
    INFO = discord.Color.blue()
    DEFAULT = discord.Color.blurple()
    SHOP = discord.Color.purple()
    ADMIN = discord.Color.dark_grey()
    PRODUCT = discord.Color.teal()
    SYNC = discord.Color.orange()    # New
    SYSTEM = discord.Color.gold()    # New

# Message Templates - Updated with new messages
class MESSAGES:
    SUCCESS = {
        'PURCHASE': "✅ Pembelian berhasil!\nDetail pembelian:",
        'STOCK_UPDATE': "✅ Stock berhasil diupdate!",
        'DONATION': "✅ Donasi berhasil diterima!",
        'BALANCE_UPDATE': "✅ Balance berhasil diupdate!",
        'REGISTRATION': "✅ Registrasi berhasil! GrowID: {growid}",
        'WORLD_UPDATE': "✅ World info berhasil diupdate!",
        'PRODUCT_CREATED': "✅ Product created successfully.",
        'PRODUCT_UPDATED': "✅ Product updated successfully.",
        'PRODUCT_DELETED': "✅ Product deleted successfully.",
        'STOCK_SOLD': "✅ Stock sold successfully.",
        'CACHE_CLEARED': "✅ Cache cleared successfully.",
        'COG_LOADED': "✅ {} loaded successfully.",
        'SYNC_SUCCESS': "✅ Components synchronized successfully.",  # New
        'REGISTRATION': "✅ Registrasi berhasil! GrowID: {growid}",
        'RECOVERY_SUCCESS': "✅ System recovered successfully."      # New
    }
    
    ERROR = {
        'INSUFFICIENT_BALANCE': "❌ Balance tidak cukup!",
        'OUT_OF_STOCK': "❌ Stock habis!",
        'INVALID_AMOUNT': "❌ Jumlah tidak valid!",
        'PERMISSION_DENIED': "❌ Anda tidak memiliki izin!",
        'INVALID_INPUT': "❌ Input tidak valid!",
        'TRANSACTION_FAILED': "❌ Transaksi gagal!",
        'REGISTRATION_FAILED': "❌ Registrasi gagal! Silakan coba lagi.",
        'NOT_REGISTERED': "❌ Anda belum terdaftar! Gunakan tombol Register.",
        'BALANCE_NOT_FOUND': "❌ Balance tidak ditemukan!",
        'BALANCE_FAILED': "❌ Gagal mengambil informasi balance!",
        'WORLD_INFO_FAILED': "❌ Gagal mengambil informasi world!",
        'NO_HISTORY': "❌ Tidak ada riwayat transaksi!",
        'INVALID_GROWID': "❌ GrowID tidak valid!",
        'PRODUCT_NOT_FOUND': "❌ Produk tidak ditemukan!",
        'INSUFFICIENT_STOCK': "❌ Stock tidak mencukupi!",
        'INVALID_PRODUCT_CODE': "❌ Invalid product code format.",
        'PRODUCT_EXISTS': "❌ Product with this code already exists.",
        'CACHE_ERROR': "❌ Error accessing cache. Please try again.",
        'DATABASE_ERROR': "❌ Database error occurred. Please try again.",
        'LOCK_ACQUISITION_FAILED': "❌ Could not acquire lock. Please try again.",
        'DISPLAY_ERROR': "❌ Terjadi kesalahan pada display sistem",  # New
        'SYNC_ERROR': "❌ Gagal mensinkronkan komponen sistem",       # New
        'INITIALIZATION_ERROR': "❌ Gagal menginisialisasi sistem",
        'INVALID_GROWID_FORMAT': "❌ Format GrowID tidak valid. Harus diawali huruf dan tidak boleh mengandung karakter khusus",
        'GROWID_EXISTS': "❌ GrowID ini sudah terdaftar dengan akun lain",
        'RATE_LIMIT': "❌ Mohon tunggu 5 menit sebelum mencoba mendaftar lagi",
        'USER_BLACKLISTED': "❌ Akun Anda tidak diizinkan untuk melakukan pendaftaran"
    }
    
    INFO = {
        'PROCESSING': "⏳ Sedang memproses...",
        'MAINTENANCE': "🛠️ Sistem dalam maintenance",
        'COOLDOWN': "⏳ Mohon tunggu {time} detik",
        'INITIALIZING': "⚙️ Sistem sedang dipersiapkan...",          # New
        'SYNCING': "🔄 Sedang mensinkronkan komponen...",            # New
        'RECOVERING': "🔧 Sistem sedang dalam proses pemulihan..."    # New
    }

    WARNING = {
        'MESSAGE_NOT_FOUND': "⚠️ Pesan tidak ditemukan",
        'LOW_STOCK': "⚠️ Stok menipis",
        'SYNC_WARNING': "⚠️ Sinkronisasi tidak sempurna",
        'VERSION_MISMATCH': "⚠️ Versi komponen tidak sesuai"
    }

class PRODUCT_CONSTANTS:
    """Konstanta khusus untuk product manager"""
    MAX_NAME_LENGTH = 50
    MAX_CODE_LENGTH = 20
    MAX_DESCRIPTION_LENGTH = 200
    MIN_PRICE = 1
    CACHE_PREFIX = "product_"
    DEFAULT_SORT = "code"
    VALID_SORT_FIELDS = ["code", "name", "price"]

# Event Types for Callback System (New)
class EVENTS:
    """Event types untuk callback system"""
    PRODUCT = {
        'CREATED': 'product_created',
        'UPDATED': 'product_updated',
        'DELETED': 'product_deleted',
        'VIEWED': 'product_viewed'
    }
    
    STOCK = {
        'ADDED': 'stock_added',
        'UPDATED': 'stock_updated',
        'SOLD': 'stock_sold',
        'LOW': 'stock_low'
    }
    
    WORLD = {
        'UPDATED': 'world_updated',
        'ACCESSED': 'world_accessed'
    }
    
    SYSTEM = {
        'ERROR': 'error',
        'WARNING': 'warning',
        'INFO': 'info',
        'DEBUG': 'debug'
    }

# Permission Levels (New)
class PERMISSIONS:
    """Permission levels untuk product manager"""
    VIEW = 0      # Can view products and stock
    PURCHASE = 1  # Can purchase products
    STOCK = 2     # Can manage stock
    ADMIN = 3     # Can manage products and settings
    OWNER = 4     # Full access

# Button IDs (Existing)
class BUTTON_IDS:
    # Basic Buttons
    CONFIRM = "confirm_{}"
    CANCEL = "cancel_{}"
    BUY = "buy"
    DONATE = "donate"
    REFRESH = "refresh"
    
    # Shop Buttons
    REGISTER = "register"
    BALANCE = "balance"
    WORLD_INFO = "world_info"
    CONFIRM_PURCHASE = "confirm_purchase"
    CANCEL_PURCHASE = "cancel_purchase"
    HISTORY = "history"
    CHECK_GROWID = "check_growid"  
    # Ticket Buttons
    TICKET_CREATE = "create_ticket"       # Tambahkan ini
    TICKET_CLOSE = "close_ticket"         # Tambahkan ini
    TICKET_REOPEN = "reopen_ticket"       # Tambahkan ini
    TICKET_CLAIM = "claim_ticket"         # Tambahkan ini
    @classmethod
    def get_purchase_confirmation_id(cls, product_code: str) -> str:
        """Generate ID untuk konfirmasi pembelian"""
        return f"{cls.CONFIRM_PURCHASE}_{product_code}"
        
    @classmethod
    def get_confirm_id(cls, action_id: str) -> str:
        """Generate ID untuk konfirmasi umum"""
        return cls.CONFIRM.format(action_id)
        
    @classmethod
    def get_cancel_id(cls, action_id: str) -> str:
        """Generate ID untuk pembatalan umum"""
        return cls.CANCEL.format(action_id)

# Update Intervals (Existing)
class UPDATE_INTERVAL:
    LIVE_BUTTONS = 120  # Update buttons setiap 120 detik
    CACHE_CLEANUP = 300
    LIVE_STOCK = 55.0    # Update live stock every 55 seconds
    BUTTONS = 30.0       # Update buttons every 30 seconds
    CACHE = 300.0        # Cache timeout 5 minutes
    STATUS = 15.0        # Status update every 15 seconds

# Cache Settings (Existing)
class CACHE_TIMEOUT:
    SHORT = timedelta(minutes=5)      # 5 menit
    MEDIUM = timedelta(hours=1)       # 1 jam
    LONG = timedelta(days=1)          # 24 jam
    PERMANENT = timedelta(days=3650)  # 10 tahun (effectively permanent)

    @classmethod
    def get_seconds(cls, timeout: timedelta) -> int:
        """Convert timedelta ke detik"""
        return int(timeout.total_seconds())

# Command Cooldowns (Existing)
class CommandCooldown:
    DEFAULT = 3
    PURCHASE = 5
    ADMIN = 2
    DONATE = 10

# Database Settings (Existing)
class Database:
    TIMEOUT = 5
    MAX_CONNECTIONS = 5
    RETRY_ATTEMPTS = 3
    RETRY_DELAY = 1
    BACKUP_INTERVAL = 86400  # 24 hours

# Paths Configuration (Existing)
class PATHS:
    CONFIG = "config.json"
    LOGS = "logs/"
    DATABASE = "database.db"
    BACKUP = "backups/"
    TEMP = "temp/"

# Logging Configuration (Existing)
class LOGGING:
    FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
    MAX_BYTES = 5 * 1024 * 1024  # 5MB
    BACKUP_COUNT = 5

# Custom Exceptions
class TransactionError(Exception):
    """Base exception for transaction related errors"""
    pass

class InsufficientBalanceError(TransactionError):
    """Raised when user has insufficient balance"""
    pass

class OutOfStockError(TransactionError):
    """Raised when item is out of stock"""
    pass

# Product Manager Exceptions (New)
class ProductError(Exception):
    """Base exception for product related errors"""
    pass

class ProductNotFoundError(ProductError):
    """Raised when product is not found"""
    pass

class InvalidProductCodeError(ProductError):
    """Raised when product code is invalid"""
    pass

class StockLimitError(ProductError):
    """Raised when stock limit is reached"""
    pass

class LockError(Exception):
    """Raised when lock acquisition fails"""
    pass

# Notification Channel IDs for Product Manager
class NOTIFICATION_CHANNELS:
    """Channel IDs untuk notifikasi sistem"""
    TRANSACTIONS = 1348580531519881246
    PRODUCT_LOGS = 1348580616647610399
    STOCK_LOGS = 1348580676202528839
    ADMIN_LOGS = 1348580745433710625
    ERROR_LOGS = 1348581120723128390
    SHOP = 1319281983796547595

    @classmethod
    def get(cls, channel_type: str, default=None):
        """Get channel ID by type"""
        try:
            return getattr(cls, channel_type.upper(), default)
        except AttributeError:
            return default