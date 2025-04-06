import discord
from discord.ext import commands
from datetime import datetime
from typing import Optional, Union, Dict, Any, Callable, List
import logging
import sys
from pathlib import Path

# Add parent directory to path to import database
sys.path.append(str(Path(__file__).parent.parent))
from database import get_connection

# Configure logger
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

class EventDispatcher:
    """Central event dispatcher"""
    
    def __init__(self):
        self.handlers: Dict[str, List[tuple[int, Callable]]] = {}
        self.logger = logging.getLogger('EventDispatcher')

    def register(self, event: str, handler: Callable, priority: int = 0):
        """Register an event handler"""
        if event not in self.handlers:
            self.handlers[event] = []
        self.handlers[event].append((priority, handler))
        self.handlers[event].sort(key=lambda x: x[0], reverse=True)

    async def dispatch(self, event: str, *args, **kwargs):
        """Dispatch an event to all registered handlers"""
        if event not in self.handlers:
            return

        for priority, handler in self.handlers[event]:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(*args, **kwargs)
                else:
                    handler(*args, **kwargs)
            except Exception as e:
                self.logger.error(f"Error in {event} handler: {e}")

class Permissions:
    """Permission management utility"""
    
    def __init__(self):
        self.permissions = {
            # Default permissions
            "admin": ["*"],  # Admin has all permissions
            "moderator": [
                "manage_messages",
                "kick_members",
                "ban_members",
                "manage_roles",
            ],
            "helper": [
                "manage_messages",
                "manage_channels",
            ],
        }

    def add_role_permission(self, role: str, permission: str):
        """Add permission to role"""
        if role not in self.permissions:
            self.permissions[role] = []
        if permission not in self.permissions[role]:
            self.permissions[role].append(permission)

    def remove_role_permission(self, role: str, permission: str):
        """Remove permission from role"""
        if role in self.permissions and permission in self.permissions[role]:
            self.permissions[role].remove(permission)

    def has_permission(self, member: discord.Member, permission: str) -> bool:
        """Check if member has permission"""
        # Admin bypass
        if any(role.name.lower() == "admin" for role in member.roles):
            return True

        # Check member roles
        for role in member.roles:
            role_name = role.name.lower()
            if role_name in self.permissions:
                # Check for wildcard
                if "*" in self.permissions[role_name]:
                    return True
                # Check specific permission
                if permission in self.permissions[role_name]:
                    return True
        return False

class Embed:
    """Centralized embed creation"""
    
    @staticmethod
    def create(
        title: str, 
        description: Optional[str] = None, 
        color: discord.Color = discord.Color.blue(),
        **kwargs
    ) -> discord.Embed:
        """Create a standardized embed"""
        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=datetime.utcnow()
        )
        
        for key, value in kwargs.items():
            if key.startswith("field_"):
                field_name = key.replace("field_", "")
                if isinstance(value, dict):
                    embed.add_field(
                        name=field_name,
                        value=value["value"],
                        inline=value.get("inline", True)
                    )
                else:
                    embed.add_field(name=field_name, value=value)
                    
        return embed

def execute_query(query: str, params: tuple = (), fetch: bool = False):
    """Execute a database query with proper connection management"""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(query, params)
        
        if fetch:
            result = cursor.fetchall()
        else:
            conn.commit()
            result = None
            
        return result
    except Exception as e:
        logger.error(f"Database error: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

def transaction(func):
    """Decorator for handling database transactions"""
    def wrapper(*args, **kwargs):
        conn = None
        try:
            conn = get_connection()
            result = func(conn, *args, **kwargs)
            conn.commit()
            return result
        except Exception as e:
            logger.error(f"Transaction error: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()
    return wrapper

# Initialize global instances
event_dispatcher = EventDispatcher()
permissions = Permissions()

# Helper functions for common database operations
@transaction
def get_user(conn, user_id: int):
    """Get user data from database"""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (str(user_id),))
    return cursor.fetchone()

@transaction
def update_user(conn, user_id: int, **kwargs):
    """Update user data in database"""
    cursor = conn.cursor()
    set_values = ", ".join([f"{k} = ?" for k in kwargs.keys()])
    query = f"UPDATE users SET {set_values} WHERE user_id = ?"
    params = tuple(kwargs.values()) + (str(user_id),)
    cursor.execute(query, params)

@transaction
def log_activity(conn, guild_id: int, user_id: int, activity_type: str, details: str = None):
    """Log activity to database"""
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO activity_logs (guild_id, user_id, activity_type, details)
        VALUES (?, ?, ?, ?)
    """, (str(guild_id), str(user_id), activity_type, details))

# Export commonly used functions and classes
__all__ = [
    'Embed',
    'EventDispatcher',
    'Permissions',
    'get_connection',
    'execute_query',
    'transaction',
    'get_user',
    'update_user',
    'log_activity',
    'logger',
    'event_dispatcher',
    'permissions'
]