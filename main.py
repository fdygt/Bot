#!/usr/bin/env python3
"""
Discord Bot for Store DC
Author: fdyyuk
Created at: 2025-03-07 18:30:16 UTC
Last Modified: 2025-03-12 16:43:04 UTC
"""

import sys
import os
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent
sys.path.append(str(project_root))

# Core imports
import discord
from discord.ext import commands
import json
import logging
import asyncio
import aiohttp
import sqlite3
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

# Import constants first
from ext.constants import (
    COLORS,
    MESSAGES,
    BUTTON_IDS,
    CACHE_TIMEOUT,
    Stock,
    Balance,
    TransactionType,
    Status,
    CURRENCY_RATES,
    UPDATE_INTERVAL,
    EXTENSIONS,
    LOGGING,
    PATHS,
    Database,
    CommandCooldown
)

# Import database
from database import setup_database, get_connection

# Import handlers and managers
from ext.cache_manager import CacheManager
from ext.base_handler import BaseLockHandler, BaseResponseHandler
from utils.command_handler import AdvancedCommandHandler

def setup_logging():
    """Setup logging configuration dengan proper handling"""
    try:
        # Buat folder logs jika belum ada
        log_dir = Path(PATHS.LOGS)
        log_dir.mkdir(exist_ok=True)
        
        # Reset handler yang ada untuk mencegah duplikasi
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
            
        # Setup format yang konsisten
        formatter = logging.Formatter(LOGGING.FORMAT)
        
        # File handler dengan rotasi
        file_handler = RotatingFileHandler(
            log_dir / 'bot.log',
            maxBytes=LOGGING.MAX_BYTES,
            backupCount=LOGGING.BACKUP_COUNT,
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        
        # Setup root logger
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)
        
        return True
    except Exception as e:
        print(f"Failed to setup logging: {e}")
        return False
    
def setup_project_structure():
    """Create necessary directories and files"""
    dirs = ['logs', 'ext', 'utils', 'cogs', 'data', 'temp', 'backups']
    for directory in dirs:
        Path(directory).mkdir(exist_ok=True)
        init_file = Path(directory) / '__init__.py'
        init_file.touch(exist_ok=True)

def check_dependencies():
    """Check if all required dependencies are installed"""
    required = {
        'discord.py': 'discord',
        'aiohttp': 'aiohttp',
        'sqlite3': 'sqlite3',
        'asyncio': 'asyncio',
        'PyNaCl': 'nacl'  # Optional for voice support
    }

    missing = []
    for package, import_name in required.items():
        try:
            __import__(import_name)
        except ImportError:
            if package != 'PyNaCl':  # Skip PyNaCl as it's optional
                missing.append(package)

    if missing:
        print(f"Missing required packages: {', '.join(missing)}")
        print("Please install required packages using:")
        print(f"pip install {' '.join(missing)}")
        sys.exit(1)

# Check dependencies and setup structure first
check_dependencies()
setup_project_structure()

# Setup logging
if not setup_logging():
    print("Failed to initialize logging system")
    sys.exit(1)

logger = logging.getLogger(__name__)

def load_config():
    """Load and validate configuration"""
    required_keys = [
        'token', 
        'guild_id', 
        'admin_id', 
        'id_live_stock',
        'id_log_purch',
        'id_donation_log', 
        'id_history_buy'
    ]

    try:
        with open(PATHS.CONFIG, 'r') as f:
            config = json.load(f)

        # Validate required keys
        missing_keys = [key for key in required_keys if key not in config]
        if missing_keys:
            raise KeyError(f"Missing required config keys: {', '.join(missing_keys)}")

        # Validate value types
        int_keys = ['guild_id', 'admin_id', 'id_live_stock', 'id_log_purch', 
                   'id_donation_log', 'id_history_buy']

        for key in int_keys:
            try:
                config[key] = int(config[key])
            except (ValueError, TypeError):
                raise ValueError(f"Invalid value for {key}. Expected integer.")

        # Set default values if not present
        defaults = {
            'cooldown_time': CommandCooldown.DEFAULT,
            'max_items': Stock.MAX_ITEMS,
            'cache_timeout': CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.SHORT)
        }

        for key, value in defaults.items():
            if key not in config:
                config[key] = value

        return config
    except FileNotFoundError:
        logger.critical(f"Config file not found: {PATHS.CONFIG}")
        logger.info("Please create a config.json file with required settings")
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.critical(f"Invalid JSON in config file: {e}")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"Error loading config: {e}")
        sys.exit(1)

class StoreBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(
            command_prefix='!',
            intents=intents,
            help_command=None
        )

        self.config = load_config()
        self.cache_manager = CacheManager()
        self.start_time = datetime.now(timezone.utc)
        self.maintenance_mode = False
        self._ready = asyncio.Event()
        self._setup_done = False

    async def setup_hook(self):
        """Initial setup when bot connects"""
        try:
            logger.info("Bot is connecting...")
        except Exception as e:
            logger.error(f"Error in setup_hook: {e}")
            await self.close()

    async def load_extensions(self):
        """Load all extensions after bot is ready"""
        try:
            # Setup database first
            logger.info("Setting up database...")
            setup_database()

            # Load core services first and verify
            logger.info("Loading core services...")
            for ext in EXTENSIONS.SERVICES:
                try:
                    logger.info(f"Loading service: {ext}")
                    await self.load_extension(ext)
                    logger.info(f"Successfully loaded service: {ext}")
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.critical(f"Failed to load critical service {ext}: {e}")
                    await self.close()
                    return

            # Verify core services
            logger.info("Verifying core services...")
            if not EXTENSIONS.verify_loaded(self):
                logger.critical("Critical services failed to load properly")
                await self.close()
                return

            # Load core features
            logger.info("Loading core features...")
            for ext in EXTENSIONS.FEATURES:
                try:
                    logger.info(f"Loading feature: {ext}")
                    await self.load_extension(ext)
                    logger.info(f"Successfully loaded feature: {ext}")
                    await asyncio.sleep(2)
                except Exception as e:
                    logger.error(f"Failed to load feature {ext}: {e}")

            # Load optional cogs
            logger.info("Loading optional cogs...")
            for ext in EXTENSIONS.COGS:
                try:
                    logger.info(f"Loading cog: {ext}")
                    await self.load_extension(ext)
                    logger.info(f"Successfully loaded cog: {ext}")
                except Exception as e:
                    logger.warning(f"Failed to load optional cog {ext}: {e}")

            self._setup_done = True
            logger.info("All extensions loaded successfully")

        except Exception as e:
            logger.critical(f"Failed to load extensions: {e}")
            await self.close()

    async def on_ready(self):
        """Called when bot is ready"""
        try:
            if not self._setup_done:
                logger.info(f"Logged in as {self.user.name} ({self.user.id})")
                logger.info(f"Discord.py Version: {discord.__version__}")
                
                # Load extensions after bot is ready
                await self.load_extensions()

                # Validate channels
                logger.info("Validating channels...")
                await asyncio.sleep(2)

                required_channels = [
                    ('id_live_stock', 'Live Stock Channel'),
                    ('id_log_purch', 'Purchase Log Channel'),
                    ('id_donation_log', 'Donation Log Channel'),
                    ('id_history_buy', 'Purchase History Channel')
                ]

                for channel_id, channel_name in required_channels:
                    channel = self.get_channel(self.config[channel_id])
                    if not channel:
                        logger.error(f"{channel_name} dengan ID {self.config[channel_id]} tidak ditemukan")
                        await self.close()
                        return
                    logger.info(f"Found {channel_name}: {channel.name}")

                # Set bot status
                activity = discord.Activity(
                    type=discord.ActivityType.watching,
                    name="Growtopia Shop 🏪"
                )
                await self.change_presence(activity=activity)

                # Clear expired cache
                await self.cache_manager.cleanup_expired()

                # Set ready event
                self._ready.set()
                logger.info("Bot is fully ready!")

        except Exception as e:
            logger.critical(f"Error in on_ready: {e}", exc_info=True)
            await self.close()

    async def on_error(self, event_method: str, *args, **kwargs):
        """Global error handler"""
        exc_type, exc_value, exc_traceback = sys.exc_info()
        logger.error(f"Error in {event_method}: {exc_type.__name__}: {exc_value}")
        logger.error("Full traceback:", exc_info=True)

    async def close(self):
        """Cleanup before closing"""
        try:
            # Cleanup tasks
            if hasattr(self, 'cache_manager'):
                await self.cache_manager.clear_all()

            # Cancel all tasks
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            [task.cancel() for task in tasks]

            await asyncio.gather(*tasks, return_exceptions=True)
            await super().close()

        except Exception as e:
            logger.error(f"Error during shutdown: {e}", exc_info=True)
        finally:
            logger.info("Bot shutdown complete")

async def run_bot():
    """Run the bot"""
    bot = StoreBot()

    try:
        async with bot:
            await bot.start(bot.config['token'])

            # Add timeout for wait_until_ready
            try:
                await asyncio.wait_for(bot._ready.wait(), timeout=60)
            except asyncio.TimeoutError:
                logger.critical("Bot failed to become ready within 60 seconds")
                await bot.close()
                return

    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except discord.LoginFailure:
        logger.critical("Invalid bot token")
    except Exception as e:
        logger.critical(f"Bot crashed: {e}", exc_info=True)
    finally:
        if not bot.is_closed():
            await bot.close()

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)