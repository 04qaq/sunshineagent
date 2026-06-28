"""Channel plugins for SunshineAgent.

This module provides the plugin architecture for connecting SunshineAgent
to various messaging platforms like QQ, WeChat, etc.
"""

from .base import ChannelPlugin, ChannelMessage, ChannelConfig
from .manager import ChannelManager

__all__ = [
    "ChannelPlugin",
    "ChannelMessage", 
    "ChannelConfig",
    "ChannelManager",
]