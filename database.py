"""
Database Manager
Author: fdyytu
Created at: 2025-03-08 10:26:37 UTC
Updated at: 2025-03-12 17:03:45 UTC
"""

import sqlite3
import logging
import time
import os
from datetime import datetime
from typing import Optional
from pathlib import Path

# Configure logging
logger = logging.getLogger(__name__)

def get_connection(max_retries: int = 3, timeout: int = 5) -> sqlite3.Connection:
    """Get database connection dengan enhanced safety"""
    db_path = Path('shop.db')
    db_dir = db_path.parent

    # Check directory permissions
    if not os.access(db_dir, os.W_OK):
        logger.error(f"No write access to directory: {db_dir}")
        raise PermissionError(f"No write access to directory: {db_dir}")

    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect('shop.db', timeout=timeout)
            conn.row_factory = sqlite3.Row
            
            # Configure database settings for better concurrency
            cursor = conn.cursor()
            
            # Tingkatkan busy timeout untuk mencegah database locked errors
            cursor.execute("PRAGMA busy_timeout = 5000")
            
            # Gunakan WAL mode untuk concurrent access yang lebih baik
            cursor.execute("PRAGMA journal_mode = WAL")
            
            # Balance antara performance dan safety
            cursor.execute("PRAGMA synchronous = NORMAL")
            
            # Enforce foreign key constraints
            cursor.execute("PRAGMA foreign_keys = ON")
            
            return conn
            
        except sqlite3.Error as e:
            if attempt == max_retries - 1:
                logger.error(f"Failed to connect to database after {max_retries} attempts: {e}")
                raise
            logger.warning(f"Database connection attempt {attempt + 1} failed: {e}")
            time.sleep(0.1 * (attempt + 1))

def setup_database():
    """Initialize and setup all database tables"""
    conn = None
    try:
        # Create database directory if needed
        db_path = Path('shop.db')
        db_dir = db_path.parent
        db_dir.mkdir(parents=True, exist_ok=True)

        # If database exists, verify it first
        if db_path.exists():
            if verify_database():
                logger.info("Database already exists and verified")
                return True
            else:
                logger.warning("Database verification failed, continuing with setup...")

        conn = get_connection()
        cursor = conn.cursor()

        # Begin transaction
        cursor.execute("BEGIN TRANSACTION")

        try:
            # 1. Admin System Tables
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    growid TEXT PRIMARY KEY,
                    balance_wl INTEGER DEFAULT 0,
                    balance_dl INTEGER DEFAULT 0,
                    balance_bgl INTEGER DEFAULT 0,
                    daily_limit INTEGER DEFAULT 1000000,
                    is_locked BOOLEAN DEFAULT FALSE,
                    lock_reason TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_growid (
                    discord_id TEXT PRIMARY KEY,
                    growid TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (growid) REFERENCES users(growid) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    code TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    description TEXT,
                    category TEXT DEFAULT 'Other',
                    priority INTEGER DEFAULT 999,
                    discount INTEGER DEFAULT 0,
                    limited BOOLEAN DEFAULT 0,
                    bundle TEXT,
                    bonus TEXT, 
                    show_code BOOLEAN DEFAULT 1,
                    status TEXT DEFAULT 'available' CHECK (status IN ('available', 'hidden', 'deleted')),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    deleted_at TIMESTAMP,
                    delete_reason TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stock (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_code TEXT NOT NULL,
                    content TEXT NOT NULL UNIQUE,
                    status TEXT DEFAULT 'available' CHECK (status IN ('available', 'sold', 'deleted')),
                    added_by TEXT NOT NULL,
                    buyer_id TEXT,
                    seller_id TEXT,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (product_code) REFERENCES products(code) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    growid TEXT NOT NULL,
                    type TEXT NOT NULL,
                    details TEXT NOT NULL,
                    old_balance TEXT,
                    new_balance TEXT,
                    items_count INTEGER DEFAULT 0,
                    total_price INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (growid) REFERENCES users(growid) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS world_info (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    world TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    bot TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS blacklist (
                    growid TEXT PRIMARY KEY,
                    added_by TEXT NOT NULL,
                    reason TEXT,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (growid) REFERENCES users(growid) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS admin_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT,
                    details TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS role_permissions (
                    role_id TEXT PRIMARY KEY,
                    permissions TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    discord_id TEXT NOT NULL,
                    activity_type TEXT NOT NULL,
                    details TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (discord_id) REFERENCES user_growid(discord_id)
                )
            """)

            # 2. Statistics System Tables
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS activity_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    activity_type TEXT NOT NULL,
                    details TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS member_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    member_count INTEGER NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 3. Cache System Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    expires_at REAL,
                    created_at REAL DEFAULT (strftime('%s', 'now')),
                    updated_at REAL DEFAULT (strftime('%s', 'now'))
                )
            """)

            # 4. Poll System Tables
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS polls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    author_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    options TEXT NOT NULL,
                    end_time DATETIME,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS poll_votes (
                    poll_id INTEGER,
                    user_id TEXT NOT NULL,
                    option_index INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (poll_id) REFERENCES polls (id) ON DELETE CASCADE,
                    UNIQUE (poll_id, user_id)
                )
            """)

            # 5. Level System Tables
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS levels (
                    user_id TEXT NOT NULL,
                    guild_id TEXT NOT NULL,
                    xp INTEGER DEFAULT 0,
                    level INTEGER DEFAULT 0,
                    messages INTEGER DEFAULT 0,
                    last_message_time TIMESTAMP,
                    PRIMARY KEY (user_id, guild_id)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS level_rewards (
                    guild_id TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    role_id TEXT NOT NULL,
                    PRIMARY KEY (guild_id, level)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS level_settings (
                    guild_id TEXT PRIMARY KEY,
                    min_xp INTEGER DEFAULT 15,
                    max_xp INTEGER DEFAULT 25,
                    cooldown INTEGER DEFAULT 60,
                    announcement_channel TEXT,
                    level_up_message TEXT DEFAULT 'Congratulations {user}! You reached level {level}!',
                    stack_roles BOOLEAN DEFAULT FALSE,
                    ignore_bots BOOLEAN DEFAULT TRUE
                )
            """)

            # 6. Music System Tables
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS music_settings (
                    guild_id TEXT PRIMARY KEY,
                    default_volume INTEGER DEFAULT 100,
                    vote_skip_ratio FLOAT DEFAULT 0.5,
                    max_queue_size INTEGER DEFAULT 500,
                    max_song_duration INTEGER DEFAULT 7200,
                    dj_role TEXT,
                    music_channel TEXT,
                    announce_songs BOOLEAN DEFAULT TRUE,
                    auto_play BOOLEAN DEFAULT FALSE
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS playlists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT,
                    name TEXT,
                    owner_id TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(guild_id, name)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS playlist_songs (
                    playlist_id INTEGER,
                    track_url TEXT,
                    track_title TEXT,
                    added_by TEXT,
                    added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (playlist_id) REFERENCES playlists (id) ON DELETE CASCADE
                )
            """)

            # 7. Reminder System Tables
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reminder_settings (
                    guild_id TEXT PRIMARY KEY,
                    max_reminders INTEGER DEFAULT 25,
                    max_duration INTEGER DEFAULT 31536000,
                    reminder_channel TEXT,
                    timezone TEXT DEFAULT 'UTC',
                    mention_roles BOOLEAN DEFAULT FALSE,
                    allow_everyone BOOLEAN DEFAULT FALSE
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    trigger_time DATETIME NOT NULL,
                    repeat_interval TEXT,
                    last_triggered DATETIME,
                    mentions TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reminder_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    message TEXT NOT NULL,
                    duration TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(guild_id, name)
                )
            """)

            # 8. Welcome System Tables
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS welcome_settings (
                    guild_id TEXT PRIMARY KEY,
                    channel_id TEXT,
                    message TEXT,
                    embed_color INTEGER DEFAULT 3447003,
                    auto_role_id TEXT,
                    verification_required BOOLEAN DEFAULT FALSE,
                    custom_background TEXT,
                    custom_font TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS welcome_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    action_type TEXT NOT NULL
                )
            """)

            # 9. AutoMod System Tables
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS automod_settings (
                    guild_id TEXT PRIMARY KEY,
                    enabled BOOLEAN DEFAULT TRUE,
                    spam_threshold INTEGER DEFAULT 5,
                    spam_timeframe INTEGER DEFAULT 5,
                    caps_threshold FLOAT DEFAULT 0.7,
                    caps_min_length INTEGER DEFAULT 10,
                    banned_words TEXT,
                    banned_wildcards TEXT,
                    warn_threshold INTEGER DEFAULT 3,
                    mute_duration INTEGER DEFAULT 10,
                    dj_role TEXT,
                    disabled_channels TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS warnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    guild_id TEXT NOT NULL,
                    warning_type TEXT NOT NULL,
                    reason TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 10. Giveaway System Tables
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS giveaways (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    host_id TEXT NOT NULL,
                    prize TEXT NOT NULL,
                    winners INTEGER DEFAULT 1,
                    entries INTEGER DEFAULT 0,
                    requirements TEXT,
                    end_time DATETIME NOT NULL,
                    ended BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS giveaway_entries (
                    giveaway_id INTEGER,
                    user_id TEXT NOT NULL,
                    entries INTEGER DEFAULT 1,
                    entered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (giveaway_id, user_id),
                    FOREIGN KEY (giveaway_id) REFERENCES giveaways (id) ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS giveaway_settings (
                    guild_id TEXT PRIMARY KEY,
                    manager_role TEXT,
                    default_duration INTEGER DEFAULT 86400,
                    maximum_duration INTEGER DEFAULT 2592000,
                    maximum_winners INTEGER DEFAULT 20,
                    bypass_roles TEXT,
                    required_roles TEXT,
                    blacklisted_roles TEXT
                )
            """)

            # Create indexes for optimization
            indexes = [
                # Cache System Indexes
                ("idx_cache_expiry", "cache(expires_at)"),
                
                # Admin System Indexes
                ("idx_user_growid_discord", "user_growid(discord_id)"),
                ("idx_user_growid_growid", "user_growid(growid)"),
                # Admin System Indexes
                ("idx_products_code", "products(code)"),
                ("idx_products_status", "products(status)"),  # Tambahkan ini
                ("idx_products_category", "products(category)"),  # Tambahkan ini juga
                ("idx_stock_status", "stock(status)"),
                ("idx_stock_content", "stock(content)"),
                ("idx_transactions_growid", "transactions(growid)"),
                ("idx_transactions_created", "transactions(created_at)"),
                ("idx_blacklist_growid", "blacklist(growid)"),
                ("idx_admin_logs_admin", "admin_logs(admin_id)"),
                ("idx_admin_logs_created", "admin_logs(created_at)"),
                ("idx_user_activity_discord", "user_activity(discord_id)"),
                ("idx_user_activity_type", "user_activity(activity_type)"),
                ("idx_role_permissions_role", "role_permissions(role_id)"),


                # Stats System Indexes
                ("idx_activity_logs_guild", "activity_logs(guild_id)"),
                ("idx_activity_logs_user", "activity_logs(user_id)"),
                ("idx_activity_logs_type", "activity_logs(activity_type)"),
                ("idx_activity_logs_timestamp", "activity_logs(timestamp)"),
                ("idx_member_history_guild", "member_history(guild_id)"),
                ("idx_member_history_timestamp", "member_history(timestamp)"),

                # Poll System Indexes
                ("idx_polls_guild", "polls(guild_id)"),
                ("idx_polls_channel", "polls(channel_id)"),
                ("idx_polls_message", "polls(message_id)"),
                ("idx_polls_author", "polls(author_id)"),
                ("idx_polls_active", "polls(is_active)"),
                ("idx_poll_votes_poll", "poll_votes(poll_id)"),
                ("idx_poll_votes_user", "poll_votes(user_id)"),

                # Level System Indexes
                ("idx_levels_user", "levels(user_id)"),
                ("idx_levels_guild", "levels(guild_id)"),
                ("idx_level_settings_guild", "level_settings(guild_id)"),

                # Music System Indexes
                ("idx_music_settings_guild", "music_settings(guild_id)"),
                ("idx_playlists_guild", "playlists(guild_id)"),
                ("idx_playlists_owner", "playlists(owner_id)"),
                ("idx_playlist_songs_playlist", "playlist_songs(playlist_id)"),

                # Reminder System Indexes
                ("idx_reminders_guild", "reminders(guild_id)"),
                ("idx_reminders_user", "reminders(user_id)"),
                ("idx_reminders_trigger_time", "reminders(trigger_time)"),
                ("idx_reminder_templates_guild", "reminder_templates(guild_id)"),
                ("idx_reminder_settings_guild", "reminder_settings(guild_id)"),

                # Welcome System Indexes
                ("idx_welcome_settings_guild", "welcome_settings(guild_id)"),
                ("idx_welcome_logs_guild", "welcome_logs(guild_id)"),
                ("idx_welcome_logs_user", "welcome_logs(user_id)"),

                # AutoMod System Indexes
                ("idx_warnings_user", "warnings(user_id)"),
                ("idx_warnings_guild", "warnings(guild_id)"),
                ("idx_automod_settings_guild", "automod_settings(guild_id)"),

                # Giveaway System Indexes
                ("idx_giveaways_guild", "giveaways(guild_id)"),
                ("idx_giveaways_channel", "giveaways(channel_id)"),
                ("idx_giveaways_message", "giveaways(message_id)"),
                ("idx_giveaways_host", "giveaways(host_id)"),
                ("idx_giveaways_end_time", "giveaways(end_time)"),
                ("idx_giveaway_entries_giveaway", "giveaway_entries(giveaway_id)"),
                ("idx_giveaway_entries_user", "giveaway_entries(user_id)"),
                ("idx_giveaway_settings_guild", "giveaway_settings(guild_id)")
            ]

            # Create indexes with error handling
            for idx_name, idx_cols in indexes:
                try:
                    cursor.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {idx_cols}")
                except sqlite3.Error as e:
                    logger.warning(f"Failed to create index {idx_name}: {e}")

            # Insert default data
            cursor.execute("""
                INSERT OR IGNORE INTO world_info (id, world, owner, bot)
                VALUES (1, 'YOURWORLD', 'OWNER', 'BOT')
            """)

            cursor.execute("""
                INSERT OR IGNORE INTO role_permissions (role_id, permissions)
                VALUES ('admin', 'all')
            """)

            # Commit all changes
            conn.commit()
            logger.info(f"Database setup completed successfully at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")

            # Set proper file permissions
            if os.name != 'nt':  # Not Windows
                try:
                    os.chmod('shop.db', 0o660)
                except Exception as e:
                    logger.warning(f"Failed to set database file permissions: {e}")
                    
            return True

        except sqlite3.Error as e:
            logger.error(f"Database setup error: {e}")
            conn.rollback()
            return False
            
    except Exception as e:
        logger.error(f"Unexpected error during database setup: {e}")
        if conn and conn.in_transaction:
            conn.rollback()
        return False
    finally:
        if conn:
            try:
                conn.close()
            except Exception as e:
                logger.error(f"Error closing database connection: {e}")

def verify_database():
    """Verify database integrity and tables existence"""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # Check all tables exist
        tables = [
            # Cache System Tables
            'cache',
            
            # Admin System Tables
            'users', 'user_growid', 'products', 'stock', 'transactions',
            'world_info', 'bot_settings', 'blacklist', 'admin_logs',
            'role_permissions', 'user_activity',
            
            # Stats System Tables
            'activity_logs', 'member_history',
            
            # Poll System Tables
            'polls', 'poll_votes',
            
            # Level System Tables
            'levels', 'level_rewards', 'level_settings',
            
            # Music System Tables
            'music_settings', 'playlists', 'playlist_songs',
            
            # Welcome System Tables
            'welcome_settings', 'welcome_logs',
            
            # AutoMod System Tables
            'automod_settings', 'warnings',
            
            # Reminder System Tables
            'reminder_settings', 'reminders', 'reminder_templates',
            
            # Giveaway System Tables
            'giveaways', 'giveaway_entries', 'giveaway_settings'
        ]

        missing_tables = []
        for table in tables:
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
            if not cursor.fetchone():
                missing_tables.append(table)

        if missing_tables:
            logger.error(f"Missing tables: {', '.join(missing_tables)}")
            raise sqlite3.Error(f"Database verification failed: missing tables")

        #Clean expired cache entries
        cursor.execute("DELETE FROM cache WHERE expires_at < strftime('%s', 'now')")
        
        #Commit transaksi sebelum menjalankan VACUUM
        conn.commit()
        
        #Vacuum database to optimize storage
        cursor.execute("VACUUM")
        
        #Mulai transaksi baru
        cursor.execute("BEGIN TRANSACTION")
        
        conn.commit()
        logger.info(f"Database verification completed successfully at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
        return True

    except sqlite3.Error as e:
        logger.error(f"Database verification error: {e}")
        return False
    finally:
        if conn:
            try:
                conn.close()
            except Exception as e:
                logger.error(f"Error closing connection during verification: {e}")

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('database.log')
        ]
    )
    
    try:
        # Cek apakah database sudah ada dan valid
        if Path('shop.db').exists():
            if verify_database():
                logger.info("Database already exists and verified")
            else:
                # Jika database ada tapi rusak, hapus dan buat ulang
                logger.error("Database verification failed. Recreating database...")
                Path('shop.db').unlink()
                if setup_database():
                    logger.info("Database successfully recreated")
                else:
                    logger.error("Failed to recreate database")
        else:
            # Jika database belum ada, buat baru
            if setup_database():
                logger.info("Database successfully created")
            else:
                logger.error("Failed to create database")
                
        # Final verification
        if verify_database():
            logger.info("Database initialization complete")
        else:
            logger.error("Database verification failed")
            
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}", exc_info=True)