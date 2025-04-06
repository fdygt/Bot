import discord
from discord.ext import commands
import logging
import json
from datetime import datetime
from typing import Optional, Dict, List, Tuple, Any
from ext.cache_manager import CacheManager
from ext.base_handler import BaseResponseHandler

logger = logging.getLogger(__name__)

class CommandAnalytics:
    def __init__(self):
        self.cache_manager = CacheManager()
        self._command_registry = {}  # Track registered commands

    async def track_command(self, ctx: commands.Context, command: str) -> None:
        """Track command usage dengan sistem registry yang lebih baik"""
        cache_key = f"analytics:command:{command}"
        
        # Get atau create stats dari cache
        stats = await self.cache_manager.get(cache_key) or {
            'total_uses': 0,
            'unique_users': set(),
            'unique_channels': set(),
            'usage_history': [],
            'peak_times': [0] * 24,
            'last_used': None,
            'success_rate': {'success': 0, 'failed': 0}
        }

        # Convert sets dari cache jika perlu
        if isinstance(stats['unique_users'], list):
            stats['unique_users'] = set(stats['unique_users'])
        if isinstance(stats['unique_channels'], list):
            stats['unique_channels'] = set(stats['unique_channels'])

        # Update statistik
        now = datetime.utcnow()
        stats['total_uses'] += 1
        stats['unique_users'].add(ctx.author.id)
        stats['unique_channels'].add(ctx.channel.id)
        stats['last_used'] = now.isoformat()
        stats['peak_times'][now.hour] += 1

        # Tracking history dengan limit
        stats['usage_history'].append({
            'timestamp': now.isoformat(),
            'user_id': ctx.author.id,
            'channel_id': ctx.channel.id
        })
        
        # Keep only last 100 entries
        if len(stats['usage_history']) > 100:
            stats['usage_history'] = stats['usage_history'][-100:]

        # Prepare untuk cache (convert sets ke lists)
        cache_stats = stats.copy()
        cache_stats['unique_users'] = list(stats['unique_users'])
        cache_stats['unique_channels'] = list(stats['unique_channels'])

        # Cache dengan TTL dan backup permanen
        await self.cache_manager.set(
            cache_key,
            cache_stats,
            expires_in=3600,  # 1 hour cache
            permanent=True
        )

    async def track_error(self, command: str, error: Exception, ctx: Optional[commands.Context] = None) -> None:
        """Track error dengan context yang lebih lengkap"""
        cache_key = f"analytics:errors:{command}"
        errors = await self.cache_manager.get(cache_key) or []
        
        error_data = {
            'timestamp': datetime.utcnow().isoformat(),
            'error_type': type(error).__name__,
            'error_message': str(error),
            'traceback': getattr(error, '__traceback__', None).__str__(),
            'context': {
                'user_id': ctx.author.id if ctx else None,
                'channel_id': ctx.channel.id if ctx else None,
                'guild_id': ctx.guild.id if ctx and ctx.guild else None
            } if ctx else None
        }
        
        errors.append(error_data)
        
        # Keep only last 50 errors to prevent overflow
        if len(errors) > 50:
            errors = errors[-50:]
            
        await self.cache_manager.set(
            cache_key,
            errors,
            expires_in=86400,  # 24 hours cache
            permanent=True
        )

class AdvancedCommandHandler(BaseResponseHandler):
    def __init__(self, bot: commands.Bot):
        super().__init__()  # Initialize BaseResponseHandler
        self.bot = bot
        self.analytics = CommandAnalytics()
        
        # Load config dengan error handling yang lebih baik
        try:
            with open('config.json', 'r') as f:
                self.config = json.load(f)
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            self.config = self._get_default_config()
            
        # Setup sistem rate limiting dan cooldown
        self.rate_limits = self._setup_rate_limits()
        self.cooldowns = self._setup_cooldowns()
        self.permissions = self._setup_permissions()
        
        # Setup channel untuk logging
        self.log_channel_id = int(self.config.get('channels', {}).get('logs', 0))

    def _get_default_config(self) -> Dict:
        """Default configuration jika config.json bermasalah"""
        return {
            'cooldowns': {'default': 3, 'admin': 1},
            'permissions': {},
            'rate_limits': {
                'global': [5, 5],
                'user': [3, 5],
                'channel': [10, 5]
            },
            'channels': {'logs': 0}
        }

    def _setup_rate_limits(self) -> Dict:
        """Setup rate limits dengan validation"""
        default_limits = {
            'global': [5, 5],
            'user': [3, 5],
            'channel': [10, 5]
        }
        
        rate_limits = self.config.get('rate_limits', {})
        
        # Validate dan set defaults jika invalid
        for key, default in default_limits.items():
            if key not in rate_limits or not isinstance(rate_limits[key], list) or len(rate_limits[key]) != 2:
                rate_limits[key] = default
                
        return rate_limits

    def _setup_cooldowns(self) -> Dict:
        """Setup cooldowns dengan validation"""
        return self.config.get('cooldowns', {'default': 3, 'admin': 1})

    def _setup_permissions(self) -> Dict:
        """Setup permissions dengan validation"""
        return self.config.get('permissions', {})

    async def check_rate_limit(self, ctx: commands.Context) -> bool:
        """Rate limit check dengan better caching"""
        now = datetime.utcnow()
        
        # Admin bypass
        if str(ctx.author.id) == str(self.config.get('admin_id')):
            return True

        # Multi-level rate limiting
        cache_keys = {
            'user': f"rate_limit:user:{ctx.author.id}",
            'channel': f"rate_limit:channel:{ctx.channel.id}",
            'global': "rate_limit:global"
        }
        
        for limit_type, cache_key in cache_keys.items():
            rate_data = await self.cache_manager.get(cache_key) or {
                'commands': [],
                'last_reset': now.timestamp()
            }
            
            # Cleanup old commands
            window = self.rate_limits[limit_type][1]
            rate_data['commands'] = [
                cmd_time for cmd_time in rate_data['commands']
                if (now - datetime.fromtimestamp(cmd_time)).total_seconds() <= window
            ]
            
            # Check limit
            if len(rate_data['commands']) >= self.rate_limits[limit_type][0]:
                return False
                
            # Update rate limit data
            rate_data['commands'].append(now.timestamp())
            await self.cache_manager.set(
                cache_key,
                rate_data,
                expires_in=window
            )
            
        return True

    async def check_cooldown(self, user_id: int, command: str) -> Tuple[bool, float]:
        """Cooldown check dengan better caching"""
        # Admin bypass
        if str(user_id) == str(self.config.get('admin_id')):
            return True, 0

        cache_key = f"cooldown:{user_id}:{command}"
        last_used = await self.cache_manager.get(cache_key)
        
        if last_used:
            cooldown_time = self.cooldowns.get(command, self.cooldowns.get('default', 3))
            elapsed = (datetime.utcnow() - datetime.fromtimestamp(last_used)).total_seconds()
            
            if elapsed < cooldown_time:
                return False, cooldown_time - elapsed

        # Set new cooldown
        await self.cache_manager.set(
            cache_key,
            datetime.utcnow().timestamp(),
            expires_in=self.cooldowns.get(command, 3)
        )
        return True, 0

    async def check_permissions(self, ctx: commands.Context, command: str) -> bool:
        """Permission check dengan better caching"""
        # Admin bypass
        if str(ctx.author.id) == str(self.config.get('admin_id')):
            return True
            
        cache_key = f"perms:{ctx.author.id}:{command}"
        cached_perm = await self.cache_manager.get(cache_key)
        
        if cached_perm is not None:
            return cached_perm

        # Check user roles
        user_roles = [str(role.id) for role in ctx.author.roles]
        has_permission = False
        
        for role_id in user_roles:
            if role_id in self.permissions:
                perms = self.permissions[role_id]
                if 'all' in perms or command in perms:
                    has_permission = True
                    break

        # Cache result
        await self.cache_manager.set(
            cache_key,
            has_permission,
            expires_in=300  # 5 minutes cache
        )
        
        return has_permission

    async def handle_command(self, ctx: commands.Context, command_name: str) -> None:
        """Handle command dengan sistem anti-duplikasi response dan error handling"""
        # Check if command exists
        command = self.bot.get_command(command_name)
        if not command:
            logger.error(f"Command not found: {command_name}")
            return

        try:
            # Rate Limit Check
            if not await self.check_rate_limit(ctx):
                success, error = await self.send_response_once(
                    ctx,
                    content="🚫 You're sending commands too fast! Please slow down.",
                    delete_after=5
                )
                if not success:
                    logger.warning(f"Failed to send rate limit message: {error}")
                return
                
            # Permission Check
            if not await self.check_permissions(ctx, command_name):
                success, error = await self.send_response_once(
                    ctx,
                    content="❌ You don't have permission to use this command!",
                    delete_after=5
                )
                if not success:
                    logger.warning(f"Failed to send permission message: {error}")
                return
                
            # Cooldown Check
            can_run, remaining = await self.check_cooldown(ctx.author.id, command_name)
            if not can_run:
                success, error = await self.send_response_once(
                    ctx,
                    content=f"⏰ Please wait {remaining:.1f}s before using this command again!",
                    delete_after=5
                )
                if not success:
                    logger.warning(f"Failed to send cooldown message: {error}")
                return

            # Track command usage
            await self.analytics.track_command(ctx, command_name)
            
            # Log successful execution
            await self.log_command(ctx, command_name, True)

        except Exception as e:
            # Error tracking dengan context
            await self.analytics.track_error(command_name, e, ctx)
            await self.log_command(ctx, command_name, False, e)
            
            # Get custom error message
            error_message = self._get_error_message(e)
            
            # Send error message dengan anti-duplikasi
            success, error = await self.send_response_once(
                ctx,
                content=error_message,
                delete_after=5
            )
            if not success:
                logger.error(f"Failed to send error message: {error}")
                
            logger.error(f"Error in command {command_name}: {e}")

    async def log_command(self, ctx: commands.Context, command: str, success: bool, error: Optional[Exception] = None) -> None:
        """Log command dengan better formatting dan error handling"""
        if not self.log_channel_id:
            return
            
        channel = self.bot.get_channel(self.log_channel_id)
        if not channel:
            return

        # Create detailed embed
        embed = discord.Embed(
            title="Command Log",
            timestamp=datetime.utcnow(),
            color=discord.Color.green() if success else discord.Color.red()
        )
        
        # Basic info
        embed.add_field(name="Command", value=f"`{command}`", inline=True)
        embed.add_field(name="User", value=f"{ctx.author} (`{ctx.author.id}`)", inline=True)
        embed.add_field(name="Channel", value=f"{ctx.channel} (`{ctx.channel.id}`)", inline=True)
        
        # Additional info
        if hasattr(ctx, 'guild') and ctx.guild:
            embed.add_field(name="Guild", value=f"{ctx.guild.name} (`{ctx.guild.id}`)", inline=True)
            
        if error:
            embed.add_field(
                name="Error",
                value=f"```py\n{type(error).__name__}: {str(error)}```",
                inline=False
            )
            
        # Add command arguments if any
        if ctx.args and len(ctx.args) > 2:  # First 2 args are bot and ctx
            args = ctx.args[2:]  # Skip bot and ctx
            embed.add_field(
                name="Arguments",
                value=f"```py\n{', '.join(map(str, args))}```",
                inline=False
            )

        try:
            await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Failed to send command log: {e}")

        # Cache log entry
        cache_key = f"cmdlog:{ctx.message.id}"
        log_entry = {
            'command': command,
            'user_id': ctx.author.id,
            'channel_id': ctx.channel.id,
            'guild_id': ctx.guild.id if ctx.guild else None,
            'success': success,
            'error': str(error) if error else None,
            'args': ctx.args[2:] if ctx.args and len(ctx.args) > 2 else [],
            'timestamp': datetime.utcnow().isoformat()
        }
        
        await self.cache_manager.set(
            cache_key,
            log_entry,
            expires_in=86400,  # 24 hours
            permanent=True
        )

    def _get_error_message(self, error: Exception) -> str:
        """Get custom error message based on exception type"""
        if isinstance(error, commands.MissingPermissions):
            return "❌ You don't have the required permissions!"
        elif isinstance(error, commands.CommandOnCooldown):
            return f"⏰ Please wait {error.retry_after:.1f}s before using this command again!"
        elif isinstance(error, commands.MissingRequiredArgument):
            return f"❌ Missing required argument: {error.param.name}"
        elif isinstance(error, commands.BadArgument):
            return "❌ Invalid argument provided!"
        return "❌ An error occurred while executing the command!"