"""Channel configuration management.

This module provides configuration management for channel plugins.
"""

import json
import os
from pathlib import Path
from typing import Any, Optional

from .base import ChannelConfig


class ChannelConfigManager:
    """Manages channel configurations.
    
    This class handles loading, saving, and managing channel configurations.
    """
    
    def __init__(self, config_dir: str):
        """Initialize the configuration manager.
        
        Args:
            config_dir: Directory to store configuration files
        """
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._configs: dict[str, ChannelConfig] = {}
    
    def load_config(self, channel_id: str) -> Optional[ChannelConfig]:
        """Load configuration for a channel.
        
        Args:
            channel_id: The channel ID
            
        Returns:
            The channel configuration, or None if not found
        """
        config_file = self.config_dir / f"{channel_id}.json"
        
        if not config_file.exists():
            return None
        
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            config = ChannelConfig(
                channel_id=channel_id,
                enabled=data.get("enabled", True),
                config=data.get("config", {}),
                allowed_users=data.get("allowed_users", []),
                allowed_groups=data.get("allowed_groups", []),
                require_mention=data.get("require_mention", True),
                max_message_length=data.get("max_message_length", 4000),
                rate_limit=data.get("rate_limit", 30),
            )
            
            self._configs[channel_id] = config
            return config
            
        except Exception as e:
            print(f"Error loading config for {channel_id}: {e}")
            return None
    
    def save_config(self, config: ChannelConfig) -> bool:
        """Save configuration for a channel.
        
        Args:
            config: The channel configuration to save
            
        Returns:
            True if saved successfully, False otherwise
        """
        config_file = self.config_dir / f"{config.channel_id}.json"
        
        try:
            data = {
                "enabled": config.enabled,
                "config": config.config,
                "allowed_users": config.allowed_users,
                "allowed_groups": config.allowed_groups,
                "require_mention": config.require_mention,
                "max_message_length": config.max_message_length,
                "rate_limit": config.rate_limit,
            }
            
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            self._configs[config.channel_id] = config
            return True
            
        except Exception as e:
            print(f"Error saving config for {config.channel_id}: {e}")
            return False
    
    def get_config(self, channel_id: str) -> Optional[ChannelConfig]:
        """Get configuration for a channel.
        
        Args:
            channel_id: The channel ID
            
        Returns:
            The channel configuration, or None if not found
        """
        return self._configs.get(channel_id)
    
    def get_all_configs(self) -> dict[str, ChannelConfig]:
        """Get all configurations.
        
        Returns:
            Dictionary of channel ID to configuration
        """
        return self._configs.copy()
    
    def delete_config(self, channel_id: str) -> bool:
        """Delete configuration for a channel.
        
        Args:
            channel_id: The channel ID
            
        Returns:
            True if deleted successfully, False otherwise
        """
        config_file = self.config_dir / f"{channel_id}.json"
        
        try:
            if config_file.exists():
                config_file.unlink()
            
            if channel_id in self._configs:
                del self._configs[channel_id]
            
            return True
            
        except Exception as e:
            print(f"Error deleting config for {channel_id}: {e}")
            return False
    
    def update_config(self, channel_id: str, updates: dict[str, Any]) -> Optional[ChannelConfig]:
        """Update configuration for a channel.
        
        Args:
            channel_id: The channel ID
            updates: Dictionary of updates to apply
            
        Returns:
            The updated configuration, or None if not found
        """
        config = self._configs.get(channel_id)
        if not config:
            return None
        
        # Apply updates
        for key, value in updates.items():
            if hasattr(config, key):
                setattr(config, key, value)
        
        # Save the updated configuration
        self.save_config(config)
        
        return config
    
    def load_all_configs(self) -> dict[str, ChannelConfig]:
        """Load all configurations from the config directory.
        
        Returns:
            Dictionary of channel ID to configuration
        """
        configs = {}
        
        for config_file in self.config_dir.glob("*.json"):
            channel_id = config_file.stem
            config = self.load_config(channel_id)
            if config:
                configs[channel_id] = config
        
        return configs


def create_default_config(channel_id: str, **kwargs) -> ChannelConfig:
    """Create a default configuration for a channel.
    
    Args:
        channel_id: The channel ID
        **kwargs: Additional configuration values
        
    Returns:
        The default configuration
    """
    return ChannelConfig(
        channel_id=channel_id,
        enabled=kwargs.get("enabled", True),
        config=kwargs.get("config", {}),
        allowed_users=kwargs.get("allowed_users", []),
        allowed_groups=kwargs.get("allowed_groups", []),
        require_mention=kwargs.get("require_mention", True),
        max_message_length=kwargs.get("max_message_length", 4000),
        rate_limit=kwargs.get("rate_limit", 30),
    )