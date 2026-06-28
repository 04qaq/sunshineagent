"""Base classes for channel plugins.

This module defines the abstract base classes that all channel plugins must implement.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Awaitable, Optional


class MessageType(Enum):
    """Types of messages that can be sent/received."""
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    AUDIO = "audio"
    VIDEO = "video"
    LOCATION = "location"
    CARD = "card"
    SYSTEM = "system"


@dataclass
class ChannelMessage:
    """Represents a message from/to a channel."""
    
    # Message ID from the channel
    message_id: str
    
    # Channel ID (e.g., "qqbot", "wechat")
    channel_id: str
    
    # Chat ID (group ID or user ID)
    chat_id: str
    
    # Sender ID
    sender_id: str
    
    # Sender name (display name)
    sender_name: str = ""
    
    # Message type
    message_type: MessageType = MessageType.TEXT
    
    # Message content (text or base64 encoded media)
    content: str = ""
    
    # Timestamp
    timestamp: datetime = field(default_factory=datetime.now)
    
    # Is this a group message?
    is_group: bool = False
    
    # Group name (if group message)
    group_name: str = ""
    
    # Reply to message ID (if this is a reply)
    reply_to: Optional[str] = None
    
    # Media URLs (for images, files, etc.)
    media_urls: list[str] = field(default_factory=list)
    
    # Raw message data from the channel
    raw_data: dict[str, Any] = field(default_factory=dict)
    
    # Mentioned users (user IDs)
    mentions: list[str] = field(default_factory=list)
    
    # Is the bot mentioned?
    bot_mentioned: bool = False


@dataclass
class ChannelConfig:
    """Configuration for a channel plugin."""
    
    # Channel ID
    channel_id: str
    
    # Whether the channel is enabled
    enabled: bool = True
    
    # Channel-specific configuration
    config: dict[str, Any] = field(default_factory=dict)
    
    # Allowed users (empty means all users are allowed)
    allowed_users: list[str] = field(default_factory=list)
    
    # Allowed groups (empty means all groups are allowed)
    allowed_groups: list[str] = field(default_factory=list)
    
    # Whether to require mention in groups
    require_mention: bool = True
    
    # Maximum message length
    max_message_length: int = 4000
    
    # Rate limiting (messages per minute per user)
    rate_limit: int = 30


class ChannelPlugin(ABC):
    """Abstract base class for channel plugins.
    
    All channel plugins must inherit from this class and implement the abstract methods.
    """
    
    # Channel ID (must be unique)
    channel_id: str = ""
    
    # Channel name (human-readable)
    channel_name: str = ""
    
    # Channel description
    channel_description: str = ""
    
    # Plugin version
    version: str = "1.0.0"
    
    def __init__(self, config: ChannelConfig):
        """Initialize the channel plugin.
        
        Args:
            config: Channel configuration
        """
        self.config = config
        self._message_handler: Optional[Callable[[ChannelMessage], Awaitable[None]]] = None
        self._is_connected: bool = False
    
    @property
    def is_connected(self) -> bool:
        """Whether the channel is connected."""
        return self._is_connected
    
    def set_message_handler(self, handler: Callable[[ChannelMessage], Awaitable[None]]) -> None:
        """Set the message handler for incoming messages.
        
        Args:
            handler: Async function to handle incoming messages
        """
        self._message_handler = handler
    
    @abstractmethod
    async def connect(self) -> bool:
        """Connect to the channel.
        
        Returns:
            True if connection was successful, False otherwise
        """
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the channel."""
        pass
    
    @abstractmethod
    async def send_message(self, chat_id: str, message: str, 
                          message_type: MessageType = MessageType.TEXT,
                          reply_to: Optional[str] = None,
                          media_urls: Optional[list[str]] = None) -> bool:
        """Send a message to the channel.
        
        Args:
            chat_id: Target chat ID (group ID or user ID)
            message: Message content
            message_type: Type of message
            reply_to: Message ID to reply to
            media_urls: URLs of media to send
            
        Returns:
            True if message was sent successfully, False otherwise
        """
        pass
    
    @abstractmethod
    async def get_bot_info(self) -> dict[str, Any]:
        """Get information about the bot.
        
        Returns:
            Dictionary with bot information (id, name, avatar, etc.)
        """
        pass
    
    async def on_message(self, message: ChannelMessage) -> None:
        """Handle an incoming message.
        
        This method is called when a message is received from the channel.
        It should be called by the plugin implementation.
        
        Args:
            message: The incoming message
        """
        if self._message_handler:
            await self._message_handler(message)
    
    async def validate_message(self, message: ChannelMessage) -> bool:
        """Validate if a message should be processed.
        
        Args:
            message: The message to validate
            
        Returns:
            True if the message should be processed, False otherwise
        """
        # Check if channel is connected
        if not self._is_connected:
            return False
        
        # Check rate limiting (simplified - in production, use proper rate limiting)
        # TODO: Implement proper rate limiting
        
        # Check allowed users
        if self.config.allowed_users and message.sender_id not in self.config.allowed_users:
            return False
        
        # Check allowed groups
        if message.is_group and self.config.allowed_groups:
            if message.chat_id not in self.config.allowed_groups:
                return False
        
        # Check mention requirement for groups
        if message.is_group and self.config.require_mention and not message.bot_mentioned:
            return False
        
        return True
    
    def get_status(self) -> dict[str, Any]:
        """Get the current status of the channel.
        
        Returns:
            Dictionary with channel status information
        """
        return {
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "connected": self._is_connected,
            "version": self.version,
        }