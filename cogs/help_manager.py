"""
Help Command Manager
Author: fdyytu
Created at: 2025-03-12 14:24:08 UTC
"""

import discord
from discord.ext import commands
from typing import Dict, List
from datetime import datetime

from ext.constants import COLORS, Permissions
from ext.admin_service import AdminService

class HelpManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.PREFIX = "!"
        self.admin_service = AdminService(bot)
        
        # Command Categories
        self.command_categories = {
            "Product Management": [
                (f"{self.PREFIX}addproduct <code> <name> <price> [description]", "Add new product"),
                (f"{self.PREFIX}editproduct <code> <field> <value>", "Edit product details"),
                (f"{self.PREFIX}deleteproduct <code>", "Delete product"),
                (f"{self.PREFIX}addstock <code>", "Add stock with file attachment"),
                (f"{self.PREFIX}addworld <name> [description]", "Add world information")
            ],
            "Balance Management": [
                (f"{self.PREFIX}addbal <growid> <amount> <WL/DL/BGL>", "Add balance"),
                (f"{self.PREFIX}removebal <growid> <amount> <WL/DL/BGL>", "Remove balance"),
                (f"{self.PREFIX}checkbal <growid>", "Check balance"),
                (f"{self.PREFIX}resetuser <growid>", "Reset balance")
            ],
            "Transaction Management": [
                (f"{self.PREFIX}trxhistory <growid> [limit]", "View transactions"),
                (f"{self.PREFIX}stockhistory <code> [limit]", "View stock history")
            ],
            "System Management": [
                (f"{self.PREFIX}systeminfo", "Show bot system information"),
                (f"{self.PREFIX}maintenance <on/off>", "Toggle maintenance mode"),
                (f"{self.PREFIX}blacklist <add/remove> <growid>", "Manage blacklisted users")
            ],
            "User Commands": [
                (f"{self.PREFIX}balance", "Check your balance"),
                (f"{self.PREFIX}deposit", "Deposit World Locks"),
                (f"{self.PREFIX}withdraw", "Withdraw World Locks"),
                (f"{self.PREFIX}shop", "View available products"),
                (f"{self.PREFIX}buy", "Purchase products")
            ],
            "Ticket System": [
                (f"{self.PREFIX}ticket", "Create a new ticket"),
                (f"{self.PREFIX}close", "Close current ticket"),
                (f"{self.PREFIX}add <user>", "Add user to ticket"),
                (f"{self.PREFIX}remove <user>", "Remove user from ticket")
            ],
            "Leveling System": [
                (f"{self.PREFIX}rank", "Check your current rank"),
                (f"{self.PREFIX}leaderboard", "View server leaderboard"),
                (f"{self.PREFIX}rewards", "View level rewards"),
                (f"{self.PREFIX}givexp <user> <amount>", "Give XP to user")
            ],
            "Server Management": [
                (f"{self.PREFIX}config", "Configure server settings"),
                (f"{self.PREFIX}autorole", "Setup automatic roles"),
                (f"{self.PREFIX}welcome", "Configure welcome messages"),
                (f"{self.PREFIX}logs", "Setup logging channels")
            ],
            "Auto Moderation": [
                (f"{self.PREFIX}automod", "Configure auto moderation"),
                (f"{self.PREFIX}warn <user> <reason>", "Warn a user"),
                (f"{self.PREFIX}mute <user> <duration>", "Mute a user"),
                (f"{self.PREFIX}unmute <user>", "Unmute a user")
            ],
            "Reputation System": [
                (f"{self.PREFIX}rep <user>", "Give reputation to user"),
                (f"{self.PREFIX}repstats", "View reputation statistics"),
                (f"{self.PREFIX}toprep", "View top reputation users"),
                (f"{self.PREFIX}reprewards", "View reputation rewards")
            ],
            "Statistics": [
                (f"{self.PREFIX}serverstats", "View server statistics"),
                (f"{self.PREFIX}rolestat", "View role statistics"),
                (f"{self.PREFIX}activitystats", "View activity statistics"),
                (f"{self.PREFIX}botinfo", "View bot information")
            ],
            "Help Commands": [
                (f"{self.PREFIX}help", "Show this help message"),
                (f"{self.PREFIX}adminhelp", "Show admin commands (Admin only)"),
                (f"{self.PREFIX}help_category <category>", "Show detailed category help"),
                (f"{self.PREFIX}commandinfo <command>", "Show detailed command info")
            ]
        }

    @commands.command(name="help")
    async def help_command(self, ctx):
        """Show help menu based on user permissions"""
        is_admin = await self.admin_service.check_admin_permission(ctx.author.id)
        
        embed = discord.Embed(
            title="🔰 Command Help",
            description=(
                f"Bot Commands - Prefix: `{self.PREFIX}`\n"
                f"Last Updated: 2025-03-12 14:24:08 UTC\n"
                f"Maintained by: fdyytu"
            ),
            color=COLORS.DEFAULT,
            timestamp=datetime.utcnow()
        )

        # Show relevant categories based on permissions
        categories_to_show = [
            "User Commands",
            "Ticket System",
            "Leveling System",
            "Reputation System",
            "Statistics",
            "Help Commands"
        ]
        
        if is_admin:
            categories_to_show.extend([
                "Product Management",
                "Balance Management", 
                "Transaction Management",
                "System Management",
                "Server Management",
                "Auto Moderation"
            ])

        for category in categories_to_show:
            commands_list = self.command_categories.get(category, [])
            if commands_list:
                commands_text = "\n".join([
                    f"`{cmd}` - {desc}" 
                    for cmd, desc in commands_list
                ])
                embed.add_field(
                    name=f"📋 {category}",
                    value=commands_text,
                    inline=False
                )

        # Add footer with additional info
        footer_text = (
            "Type !help <command> for more details about a specific command\n"
            f"{'Admin access granted | ' if is_admin else ''}"
            f"Requested by {ctx.author}"
        )
        embed.set_footer(text=footer_text)

        await ctx.send(embed=embed)

    @commands.command(name="adminhelp")
    async def admin_help(self, ctx):
        """Show admin commands"""
        if not await self.admin_service.check_admin_permission(ctx.author.id):
            embed = discord.Embed(
                title="❌ Access Denied",
                description="You don't have permission to use admin commands.",
                color=COLORS.ERROR
            )
            return await ctx.send(embed=embed)

        embed = discord.Embed(
            title="🛠️ Admin Commands",
            description=(
                f"Administrative Commands - Prefix: `{self.PREFIX}`\n"
                f"Last Updated: 2025-03-12 14:24:08 UTC\n"
                f"Maintained by: fdyytu"
            ),
            color=COLORS.DEFAULT,
            timestamp=datetime.utcnow()
        )

        admin_categories = [
            "Product Management",
            "Balance Management",
            "Transaction Management",
            "System Management",
            "Server Management",
            "Auto Moderation"
        ]

        for category in admin_categories:
            commands_list = self.command_categories.get(category, [])
            if commands_list:
                commands_text = "\n".join([
                    f"`{cmd}` - {desc}" 
                    for cmd, desc in commands_list
                ])
                embed.add_field(
                    name=f"📋 {category}",
                    value=commands_text,
                    inline=False
                )

        # Add tips field
        embed.add_field(
            name="💡 Tips",
            value=(
                "• Always use confirmation when prompted\n"
                "• Check logs with !systeminfo\n"
                "• Use !maintenance for system updates\n"
                "• Backup data regularly"
            ),
            inline=False
        )

        embed.set_footer(text=f"Admin System v2.0 | Requested by {ctx.author}")
        await ctx.send(embed=embed)

    @commands.command(name="help_category")
    async def category_help(self, ctx, category: str):
        """Show detailed help for a specific category"""
        if not category in self.command_categories:
            return await ctx.send(f"Category '{category}' not found. Use !help to see available categories.")

        is_admin = await self.admin_service.check_admin_permission(ctx.author.id)
        if category in ["Product Management", "Balance Management", "Transaction Management", "System Management"] and not is_admin:
            return await ctx.send("You don't have permission to view this category.")

        embed = discord.Embed(
            title=f"📚 {category} Help",
            description=f"Detailed commands for {category}",
            color=COLORS.INFO,
            timestamp=datetime.utcnow()
        )

        for cmd, desc in self.command_categories[category]:
            embed.add_field(
                name=cmd,
                value=desc,
                inline=False
            )

        embed.set_footer(text=f"Type {self.PREFIX}help <command> for more details")
        await ctx.send(embed=embed)

async def setup(bot):
    """Setup the Help Manager cog"""
    await bot.add_cog(HelpManager(bot))