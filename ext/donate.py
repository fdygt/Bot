import discord
from discord.ext import commands
import logging
from datetime import datetime
import json
import re
from database import get_connection
from .constants import (
    Balance,         
    TransactionError,
    CURRENCY_RATES,  
    MESSAGES,       
    TransactionType, 
    COLORS,
    PATHS
)

# Load config sekali saat modul di-import
try:
    with open(PATHS.CONFIG, 'r') as config_file:
        config = json.load(config_file)
    DONATION_CHANNEL_ID = int(config.get('id_donation_channel'))
except Exception as e:
    logging.error(f"Failed to load donation channel ID from config: {e}")
    DONATION_CHANNEL_ID = None

class DonationManager:
    """Manager class for handling donations"""
    _instance = None

    def __new__(cls, bot):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, bot):
        if not hasattr(self, 'initialized'):
            self.bot = bot
            self.logger = logging.getLogger("DonationManager")
            self.balance_manager = None
            
            # Validasi channel ID saat inisialisasi
            if not DONATION_CHANNEL_ID:
                self.logger.error("Donation channel ID not configured in config.json")
            
            self.initialized = True

    async def validate_growid(self, growid: str) -> tuple[bool, str]:
        """Validasi GrowID menggunakan balance manager"""
        try:
            # Gunakan balance manager untuk cek GrowID
            user_data = await self.balance_manager.get_user(growid)
            
            if not user_data:
                return False, "❌ GrowID tidak terdaftar di database"
                
            # Cek apakah format GrowID sesuai dengan yang di database
            if user_data.growid != growid:
                return False, f"❌ Format GrowID salah. Gunakan: {user_data.growid}"
                
            return True, "✅ GrowID valid"
            
        except Exception as e:
            self.logger.error(f"Error validating GrowID: {e}")
            return False, "❌ Terjadi kesalahan saat validasi GrowID"

    def parse_deposit(self, deposit: str) -> tuple[int, int, int]:
        """Parse deposit string into WL, DL, BGL amounts"""
        wl = dl = bgl = 0
        
        if 'World Lock' in deposit:
            wl = int(re.search(r'(\d+) World Lock', deposit).group(1))
        if 'Diamond Lock' in deposit:
            dl = int(re.search(r'(\d+) Diamond Lock', deposit).group(1))
        if 'Blue Gem Lock' in deposit:
            bgl = int(re.search(r'(\d+) Blue Gem Lock', deposit).group(1))
                
        return wl, dl, bgl

    async def process_donation(self, growid: str, wl: int, dl: int, bgl: int, current_balance: Balance) -> Balance:
        """Process a donation"""
        try:
            # Calculate new balance
            new_balance = Balance(
                current_balance.wl + wl,
                current_balance.dl + dl,
                current_balance.bgl + bgl
            )

            # Update balance menggunakan balance manager
            await self.balance_manager.update_balance(
                growid,
                new_balance,
                TransactionType.DONATION,
                f"Donation: {wl} WL, {dl} DL, {bgl} BGL"
            )

            return new_balance

        except Exception as e:
            self.logger.error(f"Error processing donation: {e}")
            raise

    async def process_webhook_message(self, message: discord.Message) -> None:
        """Proses pesan dari webhook"""
        try:
            # Pastikan pesan dari webhook
            if not message.author.bot or not message.webhook_id:
                return

            # Parse pesan webhook
            match = re.search(r"GrowID: (\w+)\nJumlah: (.+)", message.content)
            if not match:
                await self.send_error(message.channel, "Format pesan tidak valid")
                return

            growid = match.group(1)
            deposit_str = match.group(2)

            # Validasi GrowID menggunakan balance manager
            is_valid, message_text = await self.validate_growid(growid)
            if not is_valid:
                await self.send_error(message.channel, message_text)
                return

            # Get current balance via balance manager
            user_data = await self.balance_manager.get_user(growid)
            if not user_data:
                await self.send_error(message.channel, "GrowID tidak ditemukan")
                return

            current_balance = user_data.balance

            # Parse deposit amounts
            try:
                wl, dl, bgl = self.parse_deposit(deposit_str)
                if wl == 0 and dl == 0 and bgl == 0:
                    await self.send_error(message.channel, "Jumlah donasi tidak valid")
                    return
            except Exception:
                await self.send_error(message.channel, "Format jumlah donasi tidak valid")
                return

            # Process donation
            try:
                new_balance = await self.process_donation(growid, wl, dl, bgl, current_balance)
                await self.send_success(message.channel, growid, wl, dl, bgl, new_balance)
            except Exception as e:
                await self.send_error(message.channel, f"Gagal memproses donasi: {str(e)}")

        except Exception as e:
            self.logger.error(f"Error processing webhook message: {e}")
            await self.send_error(message.channel, "Terjadi kesalahan sistem")

    async def send_error(self, channel: discord.TextChannel, message: str):
        """Kirim pesan error"""
        embed = discord.Embed(
            title="❌ Donasi Gagal",
            description=message,
            color=COLORS.ERROR,
            timestamp=datetime.utcnow()
        )
        await channel.send(embed=embed)

    async def send_success(self, channel: discord.TextChannel, growid: str, wl: int, dl: int, bgl: int, new_balance: Balance):
        """Kirim pesan sukses"""
        # Hitung total dalam WL
        total_wl = (
            wl + 
            (dl * CURRENCY_RATES.RATES['DL']) + 
            (bgl * CURRENCY_RATES.RATES['BGL'])
        )

        embed = discord.Embed(
            title="💎 Donasi Berhasil",
            color=COLORS.SUCCESS,
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(
            name="📝 Detail Donasi",
            value=(
                f"**GrowID:** {growid}\n"
                f"**Jumlah:**\n"
                f"• {wl:,} World Lock\n"
                f"• {dl:,} Diamond Lock\n"
                f"• {bgl:,} Blue Gem Lock\n"
                f"**Total:** {total_wl:,} WL"
            ),
            inline=False
        )
        
        embed.add_field(
            name="💰 Saldo Baru",
            value=(
                f"```yml\n"
                f"World Lock   : {new_balance.wl:,}\n"
                f"Diamond Lock : {new_balance.dl:,}\n"
                f"Blue Gem Lock: {new_balance.bgl:,}\n"
                f"```"
            ),
            inline=False
        )
        
        embed.set_footer(text="Terima kasih atas donasi Anda!")
        await channel.send(embed=embed)

class Donation(commands.Cog):
    """Cog for donation system"""
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger("Donation")
        self.manager = DonationManager(bot)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for webhook messages"""
        # Check channel ID from config
        if not DONATION_CHANNEL_ID:
            return
        if message.channel.id != DONATION_CHANNEL_ID:
            return

        await self.manager.process_webhook_message(message)

async def setup(bot):
    """Setup the Donation cog"""
    if not hasattr(bot, 'donation_cog_loaded'):
        # Validate config first
        if not DONATION_CHANNEL_ID:
            logging.error("Donation channel ID not found in config.json")
            return
            
        donation_cog = Donation(bot)
        
        # Get balance manager instance
        from .balance_manager import BalanceManager
        donation_cog.manager.balance_manager = BalanceManager(bot)
        
        await bot.add_cog(donation_cog)
        bot.donation_cog_loaded = True
        logging.info('Donation cog loaded successfully')