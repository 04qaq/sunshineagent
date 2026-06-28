"""QQ Bot plugin implementation.

This module implements the QQ Bot channel plugin using the official QQ Bot API.
"""

import asyncio
import json
import logging
import time
from typing import Any, Optional
from datetime import datetime

import aiohttp

from ..base import ChannelPlugin, ChannelMessage, ChannelConfig, MessageType
from ..logger import get_logger

logger = logging.getLogger(__name__)

# QQ Bot API endpoints
QQ_BOT_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
QQ_BOT_API_BASE = "https://api.sgroup.qq.com"
QQ_BOT_SANDBOX_API_BASE = "https://sandbox.api.sgroup.qq.com"


class QQBotPlugin(ChannelPlugin):
    """QQ Bot channel plugin.
    
    This plugin connects to QQ using the official QQ Bot API.
    It supports C2C (private) messages, group messages, and guild channel messages.
    """
    
    channel_id = "qqbot"
    channel_name = "QQ Bot"
    channel_description = "Connect to QQ using the official QQ Bot API"
    version = "1.0.0"
    
    def __init__(self, config: ChannelConfig):
        """Initialize the QQ Bot plugin.
        
        Args:
            config: Channel configuration with the following keys:
                - app_id: QQ Bot App ID
                - app_secret: QQ Bot App Secret
                - sandbox: Whether to use sandbox environment (default: False)
        """
        super().__init__(config)
        
        self.app_id: str = config.config.get("app_id", "")
        self.app_secret: str = config.config.get("app_secret", "")
        self.sandbox: bool = config.config.get("sandbox", False)
        
        # Set API base URL based on environment
        self.api_base: str = QQ_BOT_SANDBOX_API_BASE if self.sandbox else QQ_BOT_API_BASE
        
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws_connection: Optional[aiohttp.ClientWebSocketResponse] = None
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0
        self._bot_info: Optional[dict[str, Any]] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._sequence: int = 0
        self._session_id: Optional[str] = None
        
        # Rate limiting
        self._last_message_time: dict[str, float] = {}
        self._rate_limit_interval: float = 60.0 / config.rate_limit if config.rate_limit > 0 else 2.0
    
    async def connect(self) -> bool:
        """Connect to QQ Bot API.
        
        Returns:
            True if connection was successful, False otherwise
        """
        if not self.app_id or not self.app_secret:
            logger.error("QQ Bot App ID and App Secret are required")
            return False
        
        # Clean up any existing connection
        await self.disconnect()
        
        try:
            # Create HTTP session
            self._session = aiohttp.ClientSession()
            
            # Get access token
            if not await self._refresh_access_token():
                logger.error("Failed to get access token")
                return False
            
            # Get bot info
            self._bot_info = await self._get_bot_info()
            if not self._bot_info:
                logger.error("Failed to get bot info")
                return False
            
            logger.info(f"Connected to QQ Bot: {self._bot_info.get('username', 'Unknown')}")
            
            # Log connection success
            get_logger().log_system_event(
                "connect",
                f"QQ Bot 连接成功: {self._bot_info.get('username', 'Unknown')}",
                self.channel_id,
                {"bot_id": self._bot_info.get('id'), "bot_name": self._bot_info.get('username')}
            )
            
            # Connect to WebSocket
            if not await self._connect_websocket():
                logger.error("Failed to connect to WebSocket")
                get_logger().log_system_event("error", "WebSocket 连接失败", self.channel_id)
                return False
            
            self._is_connected = True
            
            # Start heartbeat and receive tasks
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            self._receive_task = asyncio.create_task(self._receive_loop())
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to QQ Bot: {e}", exc_info=True)
            get_logger().log_system_event("error", f"连接失败: {e}", self.channel_id)
            await self.disconnect()
            return False
    
    async def disconnect(self) -> None:
        """Disconnect from QQ Bot API."""
        if not self._is_connected and not self._session:
            return
            
        self._is_connected = False
        
        # Log disconnection
        get_logger().log_system_event("disconnect", "QQ Bot 断开连接", self.channel_id)
        
        # Cancel tasks first
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await asyncio.wait_for(self._heartbeat_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception:
                pass
            self._heartbeat_task = None
        
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await asyncio.wait_for(self._receive_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception:
                pass
            self._receive_task = None
        
        # Close WebSocket
        if self._ws_connection:
            try:
                await self._ws_connection.close()
            except Exception:
                pass
            self._ws_connection = None
        
        # Close HTTP session
        if self._session:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None
        
        # Clear state
        self._access_token = None
        self._token_expires_at = 0
        self._bot_info = None
        self._session_id = None
        self._sequence = 0
        
        logger.info("Disconnected from QQ Bot")
    
    async def send_message(self, chat_id: str, message: str,
                          message_type: MessageType = MessageType.TEXT,
                          reply_to: Optional[str] = None,
                          media_urls: Optional[list[str]] = None) -> bool:
        """Send a message to QQ.
        
        Args:
            chat_id: Target chat ID (user OpenID for C2C, group OpenID for group)
            message: Message content
            message_type: Type of message
            reply_to: Message ID to reply to
            media_urls: URLs of media to send
            
        Returns:
            True if message was sent successfully, False otherwise
        """
        if not self._is_connected or not self._access_token:
            logger.error("QQ Bot is not connected")
            return False
        
        # Rate limiting
        current_time = time.time()
        last_time = self._last_message_time.get(chat_id, 0)
        if current_time - last_time < self._rate_limit_interval:
            logger.warning(f"Rate limited for chat {chat_id}")
            return False
        
        try:
            # Determine if this is a C2C or group message
            # C2C messages have a different endpoint than group messages
            # For simplicity, we'll assume all messages are C2C for now
            # In production, you would check the chat_id format
            
            url = f"{self.api_base}/v2/users/{chat_id}/messages"
            
            # Generate a unique message ID for idempotency
            import uuid
            msg_id = str(uuid.uuid4())
            
            # Get current timestamp in seconds
            timestamp = int(time.time())
            
            payload = {
                "content": message,
                "msg_type": 0,  # Text message
                "msg_id": msg_id,
                "timestamp": timestamp,
            }
            
            if reply_to:
                payload["msg_id"] = reply_to
            
            if media_urls:
                # TODO: Handle media upload
                pass
            
            headers = {
                "Authorization": f"QQBot {self._access_token}",
                "Content-Type": "application/json",
            }
            
            async with self._session.post(url, json=payload, headers=headers) as response:
                if response.status == 200:
                    self._last_message_time[chat_id] = current_time
                    logger.info(f"Sent message to {chat_id}: {message[:50]}...")
                    
                    # Log outgoing message
                    get_logger().log_outgoing_message(
                        channel_id=self.channel_id,
                        target_id=chat_id,
                        content=message,
                        message_id=msg_id,
                        reply_to=reply_to or "",
                        success=True,
                    )
                    
                    return True
                else:
                    error_text = await response.text()
                    logger.error(f"Failed to send message: {response.status} - {error_text}")
                    
                    # Log failed message
                    get_logger().log_outgoing_message(
                        channel_id=self.channel_id,
                        target_id=chat_id,
                        content=message,
                        success=False,
                    )
                    
                    return False
                    
        except Exception as e:
            logger.error(f"Error sending message: {e}", exc_info=True)
            get_logger().log_system_event("error", f"发送消息失败: {e}", self.channel_id)
            return False
    
    async def get_bot_info(self) -> dict[str, Any]:
        """Get information about the bot.
        
        Returns:
            Dictionary with bot information
        """
        if self._bot_info:
            return self._bot_info
        return await self._get_bot_info()
    
    async def _refresh_access_token(self) -> bool:
        """Refresh the access token.
        
        Returns:
            True if token was refreshed successfully, False otherwise
        """
        if not self._session:
            return False
        
        try:
            url = QQ_BOT_TOKEN_URL
            
            payload = {
                "appId": self.app_id,
                "clientSecret": self.app_secret,
            }
            
            async with self._session.post(url, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    self._access_token = data.get("access_token")
                    expires_in = int(data.get("expires_in", 7200))
                    self._token_expires_at = time.time() + expires_in - 300  # Refresh 5 minutes before expiry
                    logger.info("Access token refreshed")
                    return True
                else:
                    error_text = await response.text()
                    logger.error(f"Failed to get access token: {response.status} - {error_text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error refreshing access token: {e}", exc_info=True)
            return False
    
    async def _get_bot_info(self) -> Optional[dict[str, Any]]:
        """Get bot information from the API.
        
        Returns:
            Dictionary with bot information, or None if failed
        """
        if not self._session or not self._access_token:
            return None
        
        try:
            url = f"{self.api_base}/users/@me"
            
            headers = {
                "Authorization": f"QQBot {self._access_token}",
            }
            
            async with self._session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                else:
                    error_text = await response.text()
                    logger.error(f"Failed to get bot info: {response.status} - {error_text}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error getting bot info: {e}", exc_info=True)
            return None
    
    async def _connect_websocket(self) -> bool:
        """Connect to QQ Bot WebSocket.
        
        Returns:
            True if connection was successful, False otherwise
        """
        if not self._session or not self._access_token:
            return False
        
        try:
            # Get WebSocket URL
            url = f"{self.api_base}/gateway"
            
            headers = {
                "Authorization": f"QQBot {self._access_token}",
            }
            
            async with self._session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    ws_url = data.get("url")
                    
                    if not ws_url:
                        logger.error("No WebSocket URL in response")
                        return False
                    
                    # Connect to WebSocket
                    self._ws_connection = await self._session.ws_connect(ws_url)
                    logger.info(f"Connected to WebSocket: {ws_url}")
                    
                    # Wait for HELLO payload
                    msg = await self._ws_connection.receive()
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        if data.get("op") == 10:  # HELLO
                            heartbeat_interval = data.get("d", {}).get("heartbeat_interval", 41250)
                            logger.info(f"Received HELLO, heartbeat interval: {heartbeat_interval}ms")
                        else:
                            logger.warning(f"Expected HELLO, got: {data}")
                    
                    # Send IDENTIFY payload
                    # Intents 是一个位掩码，用于订阅事件
                    # 1 << 0 = 1: 接收频道消息
                    # 1 << 9 = 512: 接收私聊消息
                    # 1 << 25 = 33554432: 接收C2C消息
                    # 1 << 26 = 67108864: 接收群聊消息
                    intents = 1 | 512 | 33554432 | 67108864
                    
                    identify_payload = {
                        "op": 2,  # IDENTIFY
                        "d": {
                            "token": f"QQBot {self._access_token}",
                            "intents": intents,
                            "properties": {
                                "$os": "windows",
                                "$browser": "sunshine-agent",
                                "$device": "sunshine-agent",
                            },
                        },
                    }
                    
                    await self._ws_connection.send_json(identify_payload)
                    logger.info("Sent IDENTIFY payload")
                    
                    # Wait for READY event
                    msg = await self._ws_connection.receive()
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        if data.get("op") == 0 and data.get("t") == "READY":
                            self._session_id = data.get("d", {}).get("session_id")
                            logger.info(f"Received READY, session_id: {self._session_id}")
                        else:
                            logger.warning(f"Expected READY, got: {data}")
                    
                    return True
                else:
                    error_text = await response.text()
                    logger.error(f"Failed to get WebSocket URL: {response.status} - {error_text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error connecting to WebSocket: {e}", exc_info=True)
            return False
    
    async def _heartbeat_loop(self) -> None:
        """Send heartbeat to keep the connection alive."""
        try:
            while self._is_connected and self._ws_connection and not self._heartbeat_task.cancelling():
                # Check if token needs refresh
                if time.time() > self._token_expires_at:
                    await self._refresh_access_token()
                
                # Send heartbeat
                heartbeat_payload = {
                    "op": 1,  # HEARTBEAT
                    "d": self._sequence,
                }
                
                try:
                    if self._ws_connection and not self._ws_connection.closed:
                        await self._ws_connection.send_json(heartbeat_payload)
                        logger.debug("Sent heartbeat")
                    else:
                        break
                except Exception as e:
                    logger.error(f"Failed to send heartbeat: {e}")
                    break
                
                # Wait for heartbeat interval (QQ recommends 41250ms)
                await asyncio.sleep(41.25)
                
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Heartbeat loop error: {e}", exc_info=True)
    
    async def _receive_loop(self) -> None:
        """Receive and process messages from the WebSocket."""
        try:
            while self._is_connected and self._ws_connection and not self._receive_task.cancelling():
                try:
                    msg = await asyncio.wait_for(self._ws_connection.receive(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await self._handle_payload(data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {self._ws_connection.exception()}")
                    break
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
                    logger.info("WebSocket connection closed")
                    break
                    
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Receive loop error: {e}", exc_info=True)
        
        # Try to reconnect only if still connected
        if self._is_connected:
            logger.info("Attempting to reconnect...")
            asyncio.create_task(self._reconnect())
    
    async def _reconnect(self) -> None:
        """Attempt to reconnect to WebSocket."""
        await self.disconnect()
        await asyncio.sleep(5)
        if not self._is_connected:  # Only reconnect if not manually disconnected
            await self.connect()
    
    async def _handle_payload(self, payload: dict[str, Any]) -> None:
        """Handle a payload from the WebSocket.
        
        Args:
            payload: The payload to handle
        """
        op = payload.get("op")
        
        if op == 0:  # DISPATCH
            # Update sequence
            self._sequence = payload.get("s", self._sequence)
            
            # Handle event
            event_type = payload.get("t")
            event_data = payload.get("d")
            
            if event_type and event_data:
                await self._handle_event(event_type, event_data)
                
        elif op == 1:  # HEARTBEAT
            # Respond with heartbeat
            heartbeat_payload = {
                "op": 1,
                "d": self._sequence,
            }
            await self._ws_connection.send_json(heartbeat_payload)
            
        elif op == 7:  # RECONNECT
            logger.info("Received RECONNECT request")
            await self.disconnect()
            await asyncio.sleep(5)
            await self.connect()
            
        elif op == 9:  # INVALID_SESSION
            logger.error("Invalid session, reconnecting...")
            await self.disconnect()
            await asyncio.sleep(5)
            await self.connect()
            
        elif op == 11:  # HEARTBEAT_ACK
            logger.debug("Heartbeat acknowledged")
    
    async def _handle_event(self, event_type: str, event_data: dict[str, Any]) -> None:
        """Handle a dispatch event.
        
        Args:
            event_type: The event type
            event_data: The event data
        """
        logger.debug(f"Received event: {event_type}")
        
        if event_type == "MESSAGE_CREATE":
            # C2C message
            await self._handle_c2c_message(event_data)
            
        elif event_type == "AT_MESSAGE_CREATE":
            # Group message with @mention
            await self._handle_group_message(event_data, bot_mentioned=True)
            
        elif event_type == "GROUP_AT_MESSAGE_CREATE":
            # Group message with @mention (newer API)
            await self._handle_group_message(event_data, bot_mentioned=True)
            
        elif event_type == "DIRECT_MESSAGE_CREATE":
            # Direct message (DM)
            await self._handle_direct_message(event_data)
    
    async def _handle_c2c_message(self, data: dict[str, Any]) -> None:
        """Handle a C2C (private) message.
        
        Args:
            data: The message data
        """
        try:
            author = data.get("author", {})
            user_id = author.get("user_openid", "")
            content = data.get("content", "")
            message_id = data.get("id", "")
            
            # Log incoming message
            channel_logger = get_logger()
            channel_logger.log_incoming_message(
                channel_id=self.channel_id,
                sender_id=user_id,
                sender_name=author.get("username", ""),
                content=content,
                message_id=message_id,
                chat_id=user_id,
                is_group=False,
            )
            
            # Create channel message
            message = ChannelMessage(
                message_id=message_id,
                channel_id=self.channel_id,
                chat_id=user_id,
                sender_id=user_id,
                sender_name=author.get("username", ""),
                message_type=MessageType.TEXT,
                content=content,
                timestamp=datetime.fromisoformat(data.get("timestamp", datetime.now().isoformat())),
                is_group=False,
                raw_data=data,
            )
            
            # Send to message handler
            await self.on_message(message)
            
        except Exception as e:
            logger.error(f"Error handling C2C message: {e}", exc_info=True)
            get_logger().log_system_event("error", f"处理C2C消息失败: {e}", self.channel_id)
    
    async def _handle_group_message(self, data: dict[str, Any], bot_mentioned: bool = False) -> None:
        """Handle a group message.
        
        Args:
            data: The message data
            bot_mentioned: Whether the bot was mentioned
        """
        try:
            author = data.get("author", {})
            user_id = author.get("member_openid", "")
            group_id = data.get("group_openid", "")
            content = data.get("content", "")
            message_id = data.get("id", "")
            
            # Log incoming message
            channel_logger = get_logger()
            channel_logger.log_incoming_message(
                channel_id=self.channel_id,
                sender_id=user_id,
                sender_name=author.get("username", ""),
                content=content,
                message_id=message_id,
                chat_id=group_id,
                is_group=True,
                group_name=group_id,
            )
            
            # Create channel message
            message = ChannelMessage(
                message_id=message_id,
                channel_id=self.channel_id,
                chat_id=group_id,
                sender_id=user_id,
                sender_name=author.get("username", ""),
                message_type=MessageType.TEXT,
                content=content,
                timestamp=datetime.fromisoformat(data.get("timestamp", datetime.now().isoformat())),
                is_group=True,
                group_name=group_id,  # In production, you would fetch the group name
                raw_data=data,
                bot_mentioned=bot_mentioned,
            )
            
            # Extract mentions
            mentions = data.get("mentions", [])
            for mention in mentions:
                message.mentions.append(mention.get("id", ""))
            
            # Send to message handler
            await self.on_message(message)
            
        except Exception as e:
            logger.error(f"Error handling group message: {e}", exc_info=True)
            get_logger().log_system_event("error", f"处理群消息失败: {e}", self.channel_id)
    
    async def _handle_direct_message(self, data: dict[str, Any]) -> None:
        """Handle a direct message.
        
        Args:
            data: The message data
        """
        try:
            author = data.get("author", {})
            user_id = author.get("id", "")
            guild_id = data.get("guild_id", "")
            
            # Create channel message
            message = ChannelMessage(
                message_id=data.get("id", ""),
                channel_id=self.channel_id,
                chat_id=guild_id,
                sender_id=user_id,
                sender_name=author.get("username", ""),
                message_type=MessageType.TEXT,
                content=data.get("content", ""),
                timestamp=datetime.fromisoformat(data.get("timestamp", datetime.now().isoformat())),
                is_group=False,
                raw_data=data,
            )
            
            # Send to message handler
            await self.on_message(message)
            
        except Exception as e:
            logger.error(f"Error handling direct message: {e}", exc_info=True)