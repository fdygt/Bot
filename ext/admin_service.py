"""
Admin Service for Store DC Bot
Author: fdyytu1
Created at: 2025-03-09 02:20:30 UTC
Last Modified: 2025-03-19 04:57:12 UTC
"""
import logging
import asyncio
from typing import Dict, Optional
from datetime import datetime, timezone
import platform
import psutil

import discord
from discord.ext import commands

from .base_handler import BaseLockHandler, BaseResponseHandler
from .cache_manager import CacheManager
from database import get_connection

class AdminService(BaseLockHandler, BaseResponseHandler):
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
            self.logger = logging.getLogger("AdminService")
            self.cache_manager = CacheManager()
            self.maintenance_mode = False
            self.initialized = True

    def success_response(self, data: any) -> Dict:
        """Create success response"""
        return {
            'success': True,
            'data': data,
            'error': None
        }
    
    def error_response(self, error: str) -> Dict:
        """Create error response"""
        return {
            'success': False,
            'data': None,
            'error': error
        }

    async def verify_dependencies(self) -> bool:
        """Verify all required dependencies are available"""
        try:
            # Verify admin_id exists in config
            if not hasattr(self.bot, 'config'):
                raise ValueError("Bot config not found")
                
            if 'admin_id' not in self.bot.config:
                raise ValueError("Admin ID not configured")
                
            return True
        except Exception as e:
            self.logger.error(f"Failed to verify dependencies: {e}")
            return False

    async def is_maintenance_mode(self) -> bool:
        """Check if maintenance mode is active"""
        try:
            cached = await self.cache_manager.get('maintenance_mode')
            if cached is not None:
                return cached.get('enabled', False)
            return self.maintenance_mode
        except Exception as e:
            self.logger.error(f"Error checking maintenance mode: {e}")
            return False

    async def set_maintenance_mode(self, enabled: bool, reason: str = None, admin: str = None) -> Dict:
        """Set maintenance mode status"""
        try:
            self.maintenance_mode = enabled
            maintenance_data = {
                'enabled': enabled,
                'reason': reason,
                'admin': admin,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            
            await self.cache_manager.set(
                'maintenance_mode',
                maintenance_data,
                expires_in=86400  # 24 hours
            )
            
            return self.success_response(maintenance_data)
        except Exception as e:
            self.logger.error(f"Error setting maintenance mode: {e}")
            return self.error_response(str(e))

    async def check_admin_permission(self, user_id: int) -> Dict:
        """Check if user has admin permission"""
        try:
            if not hasattr(self.bot, 'config'):
                return self.error_response("Bot config not found")
                
            admin_id = self.bot.config.get('admin_id')
            if not admin_id:
                return self.error_response("Admin ID not configured")
                
            return self.success_response(str(user_id) == str(admin_id))
        except Exception as e:
            self.logger.error(f"Error checking admin permission: {e}")
            return self.error_response(str(e))

    async def get_system_stats(self) -> Dict:
        """Get system statistics"""
        try:
            # System info
            cpu_usage = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            # Bot info
            uptime = datetime.now(timezone.utc) - self.bot.start_time
            
            stats = {
                'os': f"{platform.system()} {platform.release()}",
                'cpu_usage': cpu_usage,
                'memory_used': memory.used/1024/1024/1024,
                'memory_total': memory.total/1024/1024/1024,
                'memory_percent': memory.percent,
                'disk_used': disk.used/1024/1024/1024,
                'disk_total': disk.total/1024/1024/1024,
                'disk_percent': disk.percent,
                'python_version': platform.python_version(),
                'uptime': str(uptime).split('.')[0],
                'latency': round(self.bot.latency * 1000),
                'servers': len(self.bot.guilds),
                'commands': len(self.bot.commands),
                'cache_stats': await self.cache_manager.get_stats()
            }
            
            return self.success_response(stats)
        except Exception as e:
            self.logger.error(f"Error getting system stats: {e}")
            return self.error_response(str(e))

    async def check_blacklist(self, user_id: str) -> bool:
        """Check if user is blacklisted"""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT is_blacklisted FROM users WHERE discord_id = ?",
                (user_id,)
            )
            result = cursor.fetchone()
            return result and result['is_blacklisted']
        except Exception as e:
            self.logger.error(f"Error checking blacklist: {e}")
            return False
        finally:
            if conn:
                conn.close()

    async def cleanup(self):
        """Cleanup resources"""
        try:
            await self.cache_manager.delete('maintenance_mode')
            self.logger.info("AdminService cleanup completed")
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")

async def setup(bot):
    """Setup AdminService"""
    if not hasattr(bot, 'admin_service_loaded'):
        try:
            # Initialize AdminService
            admin_service = AdminService(bot)
            if not await admin_service.verify_dependencies():
                raise Exception("AdminService dependencies verification failed")
                
            bot.admin_service = admin_service
            bot.admin_service_loaded = True
            logging.info(
                f'AdminService loaded successfully at '
                f'{datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")} UTC'
            )
        except Exception as e:
            logging.error(f"Failed to load AdminService: {e}")
            raise