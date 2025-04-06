"""
Enhanced Cache Manager with Database Integration
Author: fdyyuk
Created at: 2025-03-07 18:04:56 UTC
Last Modified: 2025-03-19 04:52:03 UTC
"""

import logging
import time
import json
from typing import Optional, Any, Dict
from datetime import datetime, timedelta
from sqlite3 import Connection, Error as SQLiteError
from database import get_connection
import asyncio
from functools import wraps

from .constants import CACHE_TIMEOUT, Balance

logger = logging.getLogger(__name__)

class CustomJSONEncoder(json.JSONEncoder):
    """Custom JSON Encoder untuk menangani object khusus"""
    def default(self, obj):
        if isinstance(obj, Balance):
            return {
                '__class__': 'Balance',
                'wl': obj.wl,
                'dl': obj.dl,
                'bgl': obj.bgl
            }
        if isinstance(obj, datetime):
            return {'__datetime__': obj.isoformat()}
        if isinstance(obj, timedelta):
            return {'__timedelta__': obj.total_seconds()}
        return super().default(obj)

class CustomJSONDecoder(json.JSONDecoder):
    """Custom JSON Decoder untuk mengembalikan object khusus"""
    def __init__(self, *args, **kwargs):
        super().__init__(object_hook=self.object_hook, *args, **kwargs)
    
    def object_hook(self, obj):
        if '__class__' in obj:
            if obj['__class__'] == 'Balance':
                return Balance(obj['wl'], obj['dl'], obj['bgl'])
        if '__datetime__' in obj:
            return datetime.fromisoformat(obj['__datetime__'])
        if '__timedelta__' in obj:
            return timedelta(seconds=obj['__timedelta__'])
        return obj

class CacheManager:
    """Enhanced Cache Manager dengan Database Integration"""
    _instance = None
    _lock = asyncio.Lock()
    
    MAX_MEMORY_ITEMS = 10000  # Batasan item di memory
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, 'initialized'):
            self.memory_cache: Dict[str, Dict] = {}
            self.logger = logging.getLogger('CacheManager')
            self._cleanup_task = None  # Tambahkan ini
            self._is_cleaning = False  # Tambahkan ini untuk mencegah recursive calls
            self.initialized = True
            
    async def _enforce_memory_limit(self):
        """Enforces memory cache limit by removing oldest items"""
        if len(self.memory_cache) > self.MAX_MEMORY_ITEMS:
            # Sort by last access time and remove oldest
            sorted_items = sorted(
                self.memory_cache.items(),
                key=lambda x: x[1].get('last_accessed', 0)
            )
            # Remove oldest items until we're under limit
            items_to_remove = len(sorted_items) - self.MAX_MEMORY_ITEMS
            for key, _ in sorted_items[:items_to_remove]:
                del self.memory_cache[key]

    async def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache
        Returns None if key not found or value expired
        """
        try:
            # Check memory cache first
            if key in self.memory_cache:
                cache_data = self.memory_cache[key]
                now = time.time()
                
                # Check expiry
                if 'expires_at' in cache_data and cache_data['expires_at'] <= now:
                    del self.memory_cache[key]
                    return None
                
                # Update last accessed time
                cache_data['last_accessed'] = now
                return json.loads(cache_data['value'], cls=CustomJSONDecoder)
            
            # Try database
            async with self._lock:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT value, expires_at FROM cache WHERE key = ? AND (expires_at IS NULL OR expires_at > ?)",
                    (key, time.time())
                )
                result = cursor.fetchone()
                
                if result:
                    value, expires_at = result
                    # Store in memory cache
                    self.memory_cache[key] = {
                        'value': value,
                        'expires_at': expires_at,
                        'last_accessed': time.time()
                    }
                    await self._enforce_memory_limit()
                    return json.loads(value, cls=CustomJSONDecoder)
                
            return None

        except Exception as e:
            self.logger.error(f"Error retrieving from cache: {e}")
            return None

    async def set(self, key: str, value: Any, expires_in: Optional[int] = None):
        """
        Set value in cache
        expires_in: Optional expiry time in seconds
        """
        try:
            expires_at = None
            if expires_in is not None:
                expires_at = time.time() + expires_in

            # Serialize value
            serialized_value = json.dumps(value, cls=CustomJSONEncoder)
            
            # Update memory cache
            self.memory_cache[key] = {
                'value': serialized_value,
                'expires_at': expires_at,
                'last_accessed': time.time()
            }
            
            await self._enforce_memory_limit()
            
            # Update database
            async with self._lock:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO cache (key, value, expires_at)
                    VALUES (?, ?, ?)
                    """,
                    (key, serialized_value, expires_at)
                )
                conn.commit()

        except Exception as e:
            self.logger.error(f"Error setting cache: {e}")
            raise

    async def delete(self, key: str):
        """Delete value from cache"""
        try:
            # Remove from memory cache
            if key in self.memory_cache:
                del self.memory_cache[key]
            
            # Remove from database
            async with self._lock:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute("DELETE FROM cache WHERE key = ?", (key,))
                conn.commit()

        except Exception as e:
            self.logger.error(f"Error deleting from cache: {e}")
            raise

    async def clear_all(self):
        """Clear all cache data"""
        try:
            # Clear memory cache
            self.memory_cache.clear()
            
            # Clear database cache
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM cache")
            conn.commit()
            
            self.logger.info("Cache cleared successfully")
        except Exception as e:
            self.logger.error(f"Error clearing cache: {e}")
            raise
        finally:
            if 'conn' in locals():
                conn.close()

    async def cleanup_expired(self):
        """Remove expired items from cache"""
        try:
            now = time.time()
            
            # Cleanup memory cache
            expired_keys = [
                key for key, data in self.memory_cache.items()
                if 'expires_at' in data and data['expires_at'] <= now
            ]
            for key in expired_keys:
                del self.memory_cache[key]
            
            # Cleanup database
            async with self._lock:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM cache WHERE expires_at IS NOT NULL AND expires_at <= ?",
                    (now,)
                )
                conn.commit()

        except Exception as e:
            self.logger.error(f"Error cleaning up expired cache: {e}")

    async def cleanup(self):
        """Cleanup cache resources safely"""
        if self._is_cleaning:  # Prevent recursive calls
            return
            
        try:
            self._is_cleaning = True
            
            # Cleanup expired items
            await self.cleanup_expired()
            
            # Cancel any existing cleanup task safely
            if self._cleanup_task and not self._cleanup_task.done():
                self._cleanup_task.cancel()
                try:
                    await asyncio.wait_for(self._cleanup_task, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                    
            self.logger.info("Cache cleanup completed successfully")
            
        except Exception as e:
            self.logger.error(f"Error during cache cleanup: {e}")
        finally:
            self._is_cleaning = False
            
    async def start_cleanup_task(self):
        """Start periodic cleanup task"""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
            
    async def _periodic_cleanup(self):
        """Run periodic cleanup every 5 minutes"""
        while True:
            try:
                await asyncio.sleep(300)  # 5 menit
                if not self._is_cleaning:  # Only cleanup if not already cleaning
                    await self.cleanup()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in periodic cleanup: {e}")
                await asyncio.sleep(60)  # Wait 1 minute on error

    async def delete_pattern(self, pattern: str):
        """Delete all cache keys matching the pattern"""
        try:
            # Hapus dari memory cache
            keys_to_delete = [
                key for key in self.memory_cache.keys()
                if pattern in key
            ]
            for key in keys_to_delete:
                del self.memory_cache[key]
                
            # Hapus dari database
            async with self._lock:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM cache WHERE key LIKE ?",
                    (f"%{pattern}%",)
                )
                conn.commit()
                
            self.logger.info(f"Deleted {len(keys_to_delete)} cache entries matching pattern: {pattern}")
                
        except Exception as e:
            self.logger.error(f"Error deleting cache pattern {pattern}: {e}")
            raise