"""
Base Handler Module
Author: fdyytu2
Created at: 2025-03-07 18:04:56 UTC
Last Modified: 2025-03-19 06:18:57 UTC
"""

import asyncio
from asyncio import Lock
import logging
from typing import Optional, Dict, Tuple
from discord.ext import commands
import discord
from ext.cache_manager import CacheManager

class BaseLockHandler:
    """Handler untuk sistem locking"""
    
    def __init__(self, *args, **kwargs):
        self._locks: Dict[str, Lock] = {}
        self._response_locks: Dict[str, Lock] = {}
        self.logger = logging.getLogger(self.__class__.__name__)
        
    async def acquire_lock(self, key: str, timeout: float = 10.0) -> Optional[Lock]:
        if key not in self._locks:
            self._locks[key] = Lock()
            
        try:
            # Kurangi timeout untuk mencegah deadlock
            actual_timeout = min(timeout, 5.0)
            await asyncio.wait_for(self._locks[key].acquire(), timeout=actual_timeout)
            self.logger.debug(f"Lock acquired for {key}")
            return self._locks[key]
        except asyncio.TimeoutError:
            self.logger.warning(f"Lock acquisition timeout for {key} after {actual_timeout}s")
            return None
        except Exception as e:
            self.logger.error(f"Error acquiring lock for {key}: {e}")
            return None

    async def acquire_response_lock(self, ctx_or_interaction, timeout: float = 5.0) -> bool:
        """
        Acquire lock untuk response context/interaction
        
        Args:
            ctx_or_interaction: Context atau Interaction object
            timeout: Waktu maksimum menunggu lock dalam detik
            
        Returns:
            True jika berhasil acquire lock, False jika gagal
        """
        try:
            key = self._get_response_key(ctx_or_interaction)
                
            if key not in self._response_locks:
                self._response_locks[key] = Lock()
                
            await asyncio.wait_for(self._response_locks[key].acquire(), timeout=timeout)
            return True
        except Exception as e:
            self.logger.error(f"Error acquiring response lock: {e}")
            return False

    def release_lock(self, key: str):
        """Release lock untuk key tertentu"""
        if key in self._locks and self._locks[key].locked():
            try:
                self._locks[key].release()
            except RuntimeError:
                self.logger.warning(f"Attempted to release an unlocked lock for {key}")

    def release_response_lock(self, ctx_or_interaction):
        """Release response lock untuk context/interaction"""
        try:
            key = self._get_response_key(ctx_or_interaction)
                
            if key in self._response_locks and self._response_locks[key].locked():
                try:
                    self._response_locks[key].release()
                except RuntimeError:
                    self.logger.warning(f"Attempted to release an unlocked response lock for {key}")
        except Exception as e:
            self.logger.error(f"Error releasing response lock: {e}")

    def cleanup(self):
        """Bersihkan semua resources"""
        self._locks.clear()
        self._response_locks.clear()

    async def __aenter__(self):
        """Support untuk async context manager"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Cleanup saat exit context"""
        self.cleanup()
        
    def _get_response_key(self, ctx_or_interaction) -> str:
        """Get unique key untuk response"""
        if isinstance(ctx_or_interaction, commands.Context):
            return f"ctx_{ctx_or_interaction.message.id}"
        elif isinstance(ctx_or_interaction, discord.Interaction):
            return f"interaction_{ctx_or_interaction.id}"
        return f"other_{id(ctx_or_interaction)}"

class BaseResponseHandler:
    """Handler untuk mengirim response dengan aman"""
    
    def __init__(self):
        self.cache_manager = CacheManager()
        self.logger = logging.getLogger(self.__class__.__name__)
    
    async def send_response_once(self, ctx_or_interaction, **kwargs) -> Tuple[bool, Optional[str]]:
        """
        Kirim response sekali saja, mendukung Context dan Interaction
        
        Args:
            ctx_or_interaction: Context atau Interaction object
            **kwargs: Argument untuk send/response.send_message
            
        Returns:
            Tuple[bool, Optional[str]]: (success, error_message)
        """
        key = self._get_response_key(ctx_or_interaction)
        
        # Check if response already sent
        if await self.cache_manager.get(f"response:{key}"):
            return False, "Response already sent"
            
        try:
            if isinstance(ctx_or_interaction, discord.Interaction):
                if not ctx_or_interaction.response.is_done():
                    await ctx_or_interaction.response.send_message(**kwargs)
                else:
                    await ctx_or_interaction.followup.send(**kwargs)
            else:
                await ctx_or_interaction.send(**kwargs)
                
            # Mark response as sent
            await self.cache_manager.set(
                f"response:{key}",
                True,
                expires_in=60  # Expire after 1 minute
            )
            return True, None
                
        except discord.errors.NotFound:
            error_msg = "Message/interaction was deleted"
            self.logger.warning(error_msg)
            return False, error_msg
        except discord.errors.Forbidden:
            error_msg = "Bot doesn't have permission to send message"
            self.logger.warning(error_msg)
            return False, error_msg
        except Exception as e:
            error_msg = f"Error sending response: {str(e)}"
            self.logger.error(error_msg)
            return False, error_msg

    async def edit_response_safely(self, ctx_or_interaction, **kwargs) -> Tuple[bool, Optional[str]]:
        """
        Edit response dengan aman
        
        Args:
            ctx_or_interaction: Context atau Interaction object
            **kwargs: Argument untuk edit
            
        Returns:
            Tuple[bool, Optional[str]]: (success, error_message)
        """
        try:
            if isinstance(ctx_or_interaction, discord.Interaction):
                if ctx_or_interaction.response.is_done():
                    await ctx_or_interaction.edit_original_response(**kwargs)
                else:
                    await ctx_or_interaction.response.send_message(**kwargs)
            else:
                if hasattr(ctx_or_interaction, 'message'):
                    await ctx_or_interaction.message.edit(**kwargs)
                    
            return True, None
                    
        except discord.errors.NotFound:
            error_msg = "Message/interaction was deleted"
            self.logger.warning(error_msg)
            return False, error_msg
        except discord.errors.Forbidden:
            error_msg = "Bot doesn't have permission to edit message"
            self.logger.warning(error_msg)
            return False, error_msg
        except Exception as e:
            error_msg = f"Error editing response: {str(e)}"
            self.logger.error(error_msg)
            return False, error_msg
            
    def _get_response_key(self, ctx_or_interaction) -> str:
        """Get unique key untuk response"""
        if isinstance(ctx_or_interaction, commands.Context):
            return f"ctx_{ctx_or_interaction.message.id}"
        elif isinstance(ctx_or_interaction, discord.Interaction):
            return f"interaction_{ctx_or_interaction.id}"
        return f"other_{id(ctx_or_interaction)}"