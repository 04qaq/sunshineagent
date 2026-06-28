"""Channel manager for managing channel plugins.

This module provides the ChannelManager class for managing channel plugins.
"""

import asyncio
import logging
from typing import Any, Optional

from .base import ChannelPlugin, ChannelMessage, ChannelConfig
from .logger import get_logger

logger = logging.getLogger(__name__)


class ChannelManager:
    """Manages channel plugins and routes messages between them and the agent.
    
    This class is responsible for:
    - Loading and managing channel plugins
    - Routing incoming messages to the agent
    - Sending outgoing messages to the appropriate channel
    """
    
    def __init__(self):
        """Initialize the channel manager."""
        self._plugins: dict[str, ChannelPlugin] = {}
        self._app_context: Optional[Any] = None  # AppContext reference
        self._sessions: dict[str, str] = {}  # session_key -> session_id
        self._running: bool = False
    
    def set_app_context(self, app_context: Any) -> None:
        """Set the application context for accessing services.
        
        Args:
            app_context: The AppContext instance
        """
        self._app_context = app_context
    
    def register_plugin(self, plugin: ChannelPlugin) -> None:
        """Register a channel plugin.
        
        Args:
            plugin: The channel plugin to register
        """
        if plugin.channel_id in self._plugins:
            logger.warning(f"Plugin {plugin.channel_id} already registered, overwriting")
        
        self._plugins[plugin.channel_id] = plugin
        plugin.set_message_handler(self._handle_incoming_message)
        logger.info(f"Registered channel plugin: {plugin.channel_id}")
    
    def unregister_plugin(self, channel_id: str) -> None:
        """Unregister a channel plugin.
        
        Args:
            channel_id: The channel ID to unregister
        """
        if channel_id in self._plugins:
            plugin = self._plugins[channel_id]
            if plugin.is_connected:
                asyncio.create_task(plugin.disconnect())
            del self._plugins[channel_id]
            logger.info(f"Unregistered channel plugin: {channel_id}")
    
    def get_plugin(self, channel_id: str) -> Optional[ChannelPlugin]:
        """Get a channel plugin by ID.
        
        Args:
            channel_id: The channel ID
            
        Returns:
            The channel plugin, or None if not found
        """
        return self._plugins.get(channel_id)
    
    def get_all_plugins(self) -> dict[str, ChannelPlugin]:
        """Get all registered plugins.
        
        Returns:
            Dictionary of channel ID to plugin
        """
        return self._plugins.copy()
    
    def set_agent_handler(self, handler: Any) -> None:
        """Set the agent handler for processing messages.
        
        Args:
            handler: The agent handler (should have an `execute` method)
        """
        self._agent_handler = handler
    
    async def _handle_incoming_message(self, message: ChannelMessage) -> None:
        """Handle an incoming message from a channel.
        
        Args:
            message: The incoming message
        """
        plugin = self._plugins.get(message.channel_id)
        if not plugin:
            logger.error(f"No plugin found for channel: {message.channel_id}")
            return
        
        # Validate the message
        if not await plugin.validate_message(message):
            logger.debug(f"Message validation failed for {message.channel_id}:{message.message_id}")
            return
        
        logger.info(f"Received message from {message.channel_id}:{message.sender_id}: {message.content[:50]}...")
        
        # Process with agent
        try:
            # Log LLM request
            get_logger().log_llm_request(
                channel_id=message.channel_id,
                user_id=message.sender_id,
                prompt=message.content,
                session_id=f"{message.channel_id}:{message.chat_id}",
            )
            
            # Process the message with the agent
            response = await self._process_with_agent(message)
            
            # Send the response back to the channel
            if response:
                # Log LLM response
                get_logger().log_llm_response(
                    channel_id=message.channel_id,
                    user_id=message.sender_id,
                    response=response,
                    session_id=f"{message.channel_id}:{message.chat_id}",
                )
                
                await self.send_message(
                    channel_id=message.channel_id,
                    chat_id=message.chat_id,
                    message=response,
                    reply_to=message.message_id,
                )
        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            get_logger().log_system_event("error", f"处理消息失败: {e}", message.channel_id)
            # Send error message to user
            await self.send_message(
                channel_id=message.channel_id,
                chat_id=message.chat_id,
                message="抱歉，处理您的消息时出现了错误。",
                reply_to=message.message_id,
            )
    
    async def _process_with_agent(self, message: ChannelMessage) -> Optional[str]:
        """Process a message with the agent.
        
        Args:
            message: The message to process
            
        Returns:
            The agent's response, or None if no response
        """
        if not self._app_context:
            logger.error("AppContext not set")
            return None
        
        try:
            # Import here to avoid circular imports
            from src.agent.loop import AgentLoop, SessionContext
            from src.agent.permissions import PermissionRuleset
            
            # Get or create session for this conversation
            session_key = f"{message.channel_id}:{message.chat_id}"
            
            if not self._app_context.sessions:
                logger.error("Session service not available")
                return None
            
            # Get or create session
            session_id = self._sessions.get(session_key)
            if session_id:
                # Verify session exists
                try:
                    session = await self._app_context.sessions.get(session_id)
                    if not session:
                        session_id = None
                except Exception:
                    session_id = None
            
            if not session_id:
                # Create a new session
                session = await self._app_context.sessions.create(
                    agent="build",
                    provider_id=self._app_context.registry.default_provider if self._app_context.registry else "anthropic",
                    model_id=self._app_context.registry.default_model if self._app_context.registry else "claude-sonnet-4-6",
                )
                session_id = session.id
                self._sessions[session_key] = session_id
                logger.info(f"Created new session {session_id} for {session_key}")
            
            # Add user message to session
            await self._app_context.sessions.create_message(
                session_id, "user", 
                parts=[{"type": "text", "text": message.content}]
            )
            
            # Create session context
            c = self._app_context.config
            reg = self._app_context.registry
            
            sctx = SessionContext(
                session_id=session_id,
                agent_name="build",
                provider_id=reg.default_provider if reg else "anthropic",
                model_id=reg.default_model if reg else "claude-sonnet-4-6",
                max_steps=10,
                permission=PermissionRuleset.all(),
                workspace=c.workspace_root if c else "",
            )
            
            # Run the agent
            loop = self._app_context.make_loop()
            result_msg_id = await loop.run(sctx)
            
            if result_msg_id:
                # Get the response from the session
                messages = await self._app_context.sessions.get_messages(session_id)
                if messages:
                    # Find the last assistant message
                    for msg in reversed(messages):
                        if msg.role == "assistant":
                            import json
                            parts = json.loads(msg.parts or "[]")
                            # Extract text content
                            text_parts = [p.get("text", "") for p in parts if p.get("type") == "text"]
                            if text_parts:
                                return "\n".join(text_parts)
            
            return None
            
        except Exception as e:
            logger.error(f"Error in agent processing: {e}", exc_info=True)
            return None
    
    async def send_message(self, channel_id: str, chat_id: str, message: str,
                          message_type: str = "text", reply_to: Optional[str] = None,
                          media_urls: Optional[list[str]] = None) -> bool:
        """Send a message to a channel.
        
        Args:
            channel_id: Target channel ID
            chat_id: Target chat ID
            message: Message content
            message_type: Type of message
            reply_to: Message ID to reply to
            media_urls: URLs of media to send
            
        Returns:
            True if message was sent successfully, False otherwise
        """
        plugin = self._plugins.get(channel_id)
        if not plugin:
            logger.error(f"No plugin found for channel: {channel_id}")
            return False
        
        if not plugin.is_connected:
            logger.error(f"Channel {channel_id} is not connected")
            return False
        
        try:
            from .base import MessageType
            msg_type = MessageType(message_type)
            return await plugin.send_message(chat_id, message, msg_type, reply_to, media_urls)
        except Exception as e:
            logger.error(f"Error sending message to {channel_id}: {e}", exc_info=True)
            return False
    
    async def connect_all(self) -> dict[str, bool]:
        """Connect all enabled plugins.
        
        Returns:
            Dictionary of channel ID to connection success status
        """
        results = {}
        for channel_id, plugin in self._plugins.items():
            if plugin.config.enabled:
                try:
                    success = await plugin.connect()
                    results[channel_id] = success
                    if success:
                        logger.info(f"Connected to {channel_id}")
                    else:
                        logger.error(f"Failed to connect to {channel_id}")
                except Exception as e:
                    logger.error(f"Error connecting to {channel_id}: {e}", exc_info=True)
                    results[channel_id] = False
            else:
                logger.info(f"Plugin {channel_id} is disabled, skipping")
                results[channel_id] = False
        
        self._running = True
        return results
    
    async def disconnect_all(self) -> None:
        """Disconnect all plugins."""
        for channel_id, plugin in self._plugins.items():
            if plugin.is_connected:
                try:
                    await plugin.disconnect()
                    logger.info(f"Disconnected from {channel_id}")
                except Exception as e:
                    logger.error(f"Error disconnecting from {channel_id}: {e}", exc_info=True)
        
        self._running = False
    
    def get_status(self) -> dict[str, Any]:
        """Get the status of all plugins.
        
        Returns:
            Dictionary with status information
        """
        return {
            "running": self._running,
            "plugins": {
                channel_id: plugin.get_status()
                for channel_id, plugin in self._plugins.items()
            }
        }