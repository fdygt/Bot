import discord
from discord.ext import commands
import logging
import sys
from datetime import datetime
from .utils import Embed, event_dispatcher
from typing import Optional, Dict, Any
import traceback
from pathlib import Path
import json
import time
from colorama import Fore, Back, Style, init

# Initialize colorama for colored terminal output
init()

class EnhancedLoggingHandler(commands.Cog):
    """📝 Enhanced Logging System with Debug Features"""
    
    def __init__(self, bot):
        self.bot = bot
        self.debug_mode = False
        self.performance_metrics = {}
        self.command_history = []
        self.error_count = {}
        self.setup_logging()
        self.register_events()

    def setup_logging(self):
        """Setup enhanced logging configuration"""
        # Buat folder logs jika belum ada
        log_dir = Path('logs')
        log_dir.mkdir(exist_ok=True)
        
        # Setup formatter dengan warna untuk terminal
        terminal_formatter = logging.Formatter(
            f'{Fore.CYAN}%(asctime)s {Fore.WHITE}| '
            f'{Fore.GREEN}%(levelname)s {Fore.WHITE}| '
            f'{Fore.YELLOW}%(name)s {Fore.WHITE}| '
            f'{Fore.WHITE}%(message)s{Style.RESET_ALL}'
        )
        
        # Setup formatter untuk file
        file_formatter = logging.Formatter(
            '%(asctime)s | %(levelname)s | %(name)s | %(message)s'
        )

        # Setup logger utama
        self.logger = logging.getLogger('discord')
        self.logger.setLevel(logging.INFO)
        
        # File handler untuk log umum
        file_handler = logging.FileHandler(
            filename='logs/discord.log', 
            encoding='utf-8', 
            mode='a'
        )
        file_handler.setFormatter(file_formatter)
        
        # Terminal handler dengan warna
        terminal_handler = logging.StreamHandler(sys.stdout)
        terminal_handler.setFormatter(terminal_formatter)
        
        # Debug file handler
        debug_handler = logging.FileHandler(
            filename='logs/debug.log',
            encoding='utf-8',
            mode='a'
        )
        debug_handler.setFormatter(file_formatter)
        debug_handler.setLevel(logging.DEBUG)
        
        # Tambahkan semua handler
        self.logger.addHandler(file_handler)
        self.logger.addHandler(terminal_handler)
        self.logger.addHandler(debug_handler)
        
        # Setup activity logger
        self.activity_logger = logging.getLogger('activity')
        activity_handler = logging.FileHandler(
            filename='logs/activity.log',
            encoding='utf-8',
            mode='a'
        )
        activity_handler.setFormatter(file_formatter)
        self.activity_logger.addHandler(activity_handler)
        
        # Performance logger
        self.perf_logger = logging.getLogger('performance')
        perf_handler = logging.FileHandler(
            filename='logs/performance.log',
            encoding='utf-8',
            mode='a'
        )
        perf_handler.setFormatter(file_formatter)
        self.perf_logger.addHandler(perf_handler)
        self.perf_logger.addHandler(terminal_handler)

    def register_events(self):
        """Register event handlers"""
        event_dispatcher.register('message', self.log_message)
        event_dispatcher.register('command', self.log_command)
        event_dispatcher.register('error', self.log_error)
        event_dispatcher.register('voice', self.log_voice)

    async def log_message(self, message):
        """Log message activity with debug info"""
        if message.author.bot:
            return
            
        log_msg = (
            f"Message by {message.author} (ID: {message.author.id}) "
            f"in #{message.channel.name} ({message.guild.name})"
        )
        
        if self.debug_mode:
            log_msg += f"\nContent: {message.content}"
            self.logger.debug(f"{Fore.MAGENTA}DEBUG - {log_msg}{Style.RESET_ALL}")
        
        self.activity_logger.info(log_msg)

    async def log_command(self, ctx):
        """Log command usage with performance tracking"""
        timestamp = time.time()
        cmd_name = ctx.command.name if ctx.command else "Unknown"
        
        # Start performance tracking
        self.performance_metrics[cmd_name] = timestamp
        
        log_msg = (
            f"Command '{cmd_name}' used by {ctx.author} "
            f"(ID: {ctx.author.id}) in #{ctx.channel.name}"
        )
        
        if self.debug_mode:
            log_msg += f"\nArgs: {ctx.args[1:]}\nKwargs: {ctx.kwargs}"
            self.logger.debug(f"{Fore.CYAN}DEBUG - {log_msg}{Style.RESET_ALL}")
        
        self.logger.info(log_msg)
        
        # Track command history
        self.command_history.append({
            "timestamp": datetime.utcnow().isoformat(),
            "command": cmd_name,
            "author": str(ctx.author),
            "channel": str(ctx.channel),
            "args": str(ctx.args[1:]),
            "kwargs": str(ctx.kwargs)
        })

    async def log_error(self, ctx, error):
        """Log command errors with debug info"""
        error_type = type(error).__name__
        self.error_count[error_type] = self.error_count.get(error_type, 0) + 1
        
        log_msg = (
            f"Error in command '{ctx.command}' by {ctx.author}: {error}\n"
            f"Error type: {error_type}"
        )
        
        if self.debug_mode:
            log_msg += f"\nTraceback:\n{traceback.format_exc()}"
            self.logger.debug(f"{Fore.RED}DEBUG ERROR - {log_msg}{Style.RESET_ALL}")
        
        self.logger.error(f"{Fore.RED}{log_msg}{Style.RESET_ALL}")

    async def log_voice(self, member, before, after):
        """Log voice state changes"""
        if before.channel != after.channel:
            if after.channel:
                action = f"joined {after.channel.name}"
            else:
                action = f"left {before.channel.name}"
            
            log_msg = f"Voice: {member} (ID: {member.id}) {action}"
            
            if self.debug_mode:
                log_msg += (
                    f"\nBefore: {before.channel}"
                    f"\nAfter: {after.channel}"
                    f"\nSelf Mute: {after.self_mute}"
                    f"\nSelf Deaf: {after.self_deaf}"
                )
                self.logger.debug(f"{Fore.BLUE}DEBUG - {log_msg}{Style.RESET_ALL}")
            
            self.activity_logger.info(log_msg)

    @commands.command()
    @commands.is_owner()
    async def debug(self, ctx):
        """Toggle debug mode"""
        self.debug_mode = not self.debug_mode
        state = "enabled" if self.debug_mode else "disabled"
        
        await ctx.send(f"🔧 Debug mode {state}")
        self.logger.info(f"{Fore.YELLOW}Debug mode {state}{Style.RESET_ALL}")

    @commands.command()
    @commands.is_owner()
    async def debugstats(self, ctx):
        """Show debug statistics"""
        # Calculate command execution times
        current_time = time.time()
        for cmd, start_time in self.performance_metrics.items():
            if isinstance(start_time, float):
                duration = current_time - start_time
                self.perf_logger.info(
                    f"{Fore.CYAN}Command '{cmd}' execution time: "
                    f"{duration:.2f}s{Style.RESET_ALL}"
                )

        # Create embed with stats
        embed = discord.Embed(
            title="🔍 Debug Statistics",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )

        # Command stats
        total_commands = len(self.command_history)
        recent_commands = len([
            cmd for cmd in self.command_history 
            if (datetime.utcnow() - datetime.fromisoformat(cmd['timestamp'])).total_seconds() < 3600
        ])
        
        embed.add_field(
            name="📊 Command Stats",
            value=f"Total: {total_commands}\nLast hour: {recent_commands}",
            inline=False
        )

        # Error stats
        error_stats = "\n".join(
            f"{error}: {count}" 
            for error, count in self.error_count.items()
        ) or "No errors"
        
        embed.add_field(
            name="❌ Error Frequency",
            value=error_stats,
            inline=False
        )

        # Performance stats
        perf_stats = "\n".join(
            f"{cmd}: {time:.2f}s"
            for cmd, time in self.performance_metrics.items()
            if isinstance(time, float)
        ) or "No performance data"
        
        embed.add_field(
            name="⚡ Performance Metrics",
            value=perf_stats,
            inline=False
        )

        embed.add_field(
            name="🔧 Debug Mode",
            value="Enabled" if self.debug_mode else "Disabled",
            inline=False
        )

        await ctx.send(embed=embed)

    @commands.command()
    @commands.is_owner()
    async def clearlogs(self, ctx):
        """Clear all debug logs and reset metrics"""
        self.command_history.clear()
        self.error_count.clear()
        self.performance_metrics.clear()
        
        # Clear log files
        log_files = ['discord.log', 'activity.log', 'debug.log', 'performance.log']
        for file in log_files:
            with open(f'logs/{file}', 'w', encoding='utf-8') as f:
                f.write('')
        
        await ctx.send("🧹 All logs and metrics have been cleared!")
        self.logger.info(f"{Fore.GREEN}All logs and metrics cleared{Style.RESET_ALL}")

    @commands.Cog.listener()
    async def on_command(self, ctx):
        await event_dispatcher.dispatch('command', ctx)

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        await event_dispatcher.dispatch('error', ctx, error)

    @commands.Cog.listener()
    async def on_message(self, message):
        await event_dispatcher.dispatch('message', message)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        await event_dispatcher.dispatch('voice', member, before, after)

async def setup(bot):
    await bot.add_cog(EnhancedLoggingHandler(bot))