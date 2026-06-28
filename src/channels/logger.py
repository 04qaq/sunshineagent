"""Channel message logger.

This module provides logging functionality for channel messages,
including incoming messages and outgoing responses.
"""

import logging
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.table import Table


# Rich console for pretty printing
console = Console()


class ChannelLogger:
    """Logger for channel messages.
    
    This class provides formatted logging for:
    - Incoming messages from users
    - Outgoing responses from the bot
    - System events (connection, errors, etc.)
    """
    
    def __init__(self, log_dir: Optional[str] = None, log_to_file: bool = True, log_to_console: bool = True):
        """Initialize the channel logger.
        
        Args:
            log_dir: Directory to store log files (None for no file logging)
            log_to_file: Whether to log to file
            log_to_console: Whether to log to console
        """
        self.log_to_file = log_to_file
        self.log_to_console = log_to_console
        
        # Setup file logger
        self._file_logger: Optional[logging.Logger] = None
        if log_dir and log_to_file:
            self._setup_file_logger(log_dir)
    
    def _setup_file_logger(self, log_dir: str) -> None:
        """Setup file logger."""
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        
        # Create logger
        self._file_logger = logging.getLogger("channel_messages")
        self._file_logger.setLevel(logging.DEBUG)
        
        # Create file handler
        log_file = log_path / f"messages_{datetime.now().strftime('%Y%m%d')}.log"
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        
        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        
        # Add handler to logger
        self._file_logger.addHandler(file_handler)
    
    def log_incoming_message(self, channel_id: str, sender_id: str, sender_name: str, 
                           content: str, message_id: str = "", chat_id: str = "",
                           is_group: bool = False, group_name: str = "") -> None:
        """Log an incoming message from a user.
        
        Args:
            channel_id: Channel ID (e.g., 'qqbot', 'wechat')
            sender_id: Sender's user ID
            sender_name: Sender's display name
            content: Message content
            message_id: Message ID
            chat_id: Chat ID (user ID or group ID)
            is_group: Whether this is a group message
            group_name: Group name (if group message)
        """
        # Format message for display
        if is_group:
            source = f"[群聊:{group_name}]"
        else:
            source = "[私聊]"
        
        # Console output
        if self.log_to_console:
            console.print()
            console.print(Panel(
                Text(content, style="white"),
                title=f"📨 收到消息 {source}",
                subtitle=f"来自: {sender_name} ({sender_id}) | 频道: {channel_id}",
                border_style="blue",
                expand=False,
            ))
        
        # File output
        if self._file_logger:
            log_entry = {
                "type": "incoming",
                "channel": channel_id,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "chat_id": chat_id,
                "message_id": message_id,
                "content": content,
                "is_group": is_group,
                "group_name": group_name,
                "timestamp": datetime.now().isoformat(),
            }
            self._file_logger.info(f"INCOMING: {json.dumps(log_entry, ensure_ascii=False)}")
    
    def log_outgoing_message(self, channel_id: str, target_id: str, content: str,
                           message_id: str = "", reply_to: str = "",
                           success: bool = True) -> None:
        """Log an outgoing message from the bot.
        
        Args:
            channel_id: Channel ID
            target_id: Target user/group ID
            content: Message content
            message_id: Sent message ID
            reply_to: Message ID being replied to
            success: Whether the message was sent successfully
        """
        status = "✅" if success else "❌"
        style = "green" if success else "red"
        
        # Console output
        if self.log_to_console:
            console.print()
            console.print(Panel(
                Text(content, style="white"),
                title=f"{status} 发送消息",
                subtitle=f"目标: {target_id} | 频道: {channel_id}",
                border_style=style,
                expand=False,
            ))
        
        # File output
        if self._file_logger:
            log_entry = {
                "type": "outgoing",
                "channel": channel_id,
                "target_id": target_id,
                "message_id": message_id,
                "reply_to": reply_to,
                "content": content,
                "success": success,
                "timestamp": datetime.now().isoformat(),
            }
            self._file_logger.info(f"OUTGOING: {json.dumps(log_entry, ensure_ascii=False)}")
    
    def log_llm_request(self, channel_id: str, user_id: str, prompt: str, 
                       session_id: str = "") -> None:
        """Log an LLM request.
        
        Args:
            channel_id: Channel ID
            user_id: User ID
            prompt: Prompt sent to LLM
            session_id: Session ID
        """
        # Console output
        if self.log_to_console:
            console.print()
            console.print(Panel(
                Text(prompt[:200] + "..." if len(prompt) > 200 else prompt, style="cyan"),
                title="🤖 LLM 请求",
                subtitle=f"用户: {user_id} | 会话: {session_id}",
                border_style="cyan",
                expand=False,
            ))
        
        # File output
        if self._file_logger:
            log_entry = {
                "type": "llm_request",
                "channel": channel_id,
                "user_id": user_id,
                "session_id": session_id,
                "prompt": prompt,
                "timestamp": datetime.now().isoformat(),
            }
            self._file_logger.info(f"LLM_REQUEST: {json.dumps(log_entry, ensure_ascii=False)}")
    
    def log_llm_response(self, channel_id: str, user_id: str, response: str,
                        session_id: str = "", tokens_used: int = 0) -> None:
        """Log an LLM response.
        
        Args:
            channel_id: Channel ID
            user_id: User ID
            response: Response from LLM
            session_id: Session ID
            tokens_used: Number of tokens used
        """
        # Console output
        if self.log_to_console:
            console.print()
            console.print(Panel(
                Text(response[:200] + "..." if len(response) > 200 else response, style="green"),
                title="🧠 LLM 响应",
                subtitle=f"用户: {user_id} | tokens: {tokens_used}",
                border_style="green",
                expand=False,
            ))
        
        # File output
        if self._file_logger:
            log_entry = {
                "type": "llm_response",
                "channel": channel_id,
                "user_id": user_id,
                "session_id": session_id,
                "response": response,
                "tokens_used": tokens_used,
                "timestamp": datetime.now().isoformat(),
            }
            self._file_logger.info(f"LLM_RESPONSE: {json.dumps(log_entry, ensure_ascii=False)}")
    
    def log_system_event(self, event_type: str, message: str, 
                        channel_id: str = "", details: dict[str, Any] = None) -> None:
        """Log a system event.
        
        Args:
            event_type: Event type (e.g., 'connect', 'disconnect', 'error')
            message: Event message
            channel_id: Channel ID
            details: Additional details
        """
        # Determine style based on event type
        if event_type == "connect":
            style = "green"
            icon = "🔗"
        elif event_type == "disconnect":
            style = "yellow"
            icon = "🔌"
        elif event_type == "error":
            style = "red"
            icon = "❌"
        else:
            style = "blue"
            icon = "ℹ️"
        
        # Console output
        if self.log_to_console:
            console.print()
            console.print(Panel(
                Text(message, style=style),
                title=f"{icon} {event_type.upper()}",
                subtitle=f"频道: {channel_id}" if channel_id else None,
                border_style=style,
                expand=False,
            ))
        
        # File output
        if self._file_logger:
            log_entry = {
                "type": "system",
                "event_type": event_type,
                "channel": channel_id,
                "message": message,
                "details": details or {},
                "timestamp": datetime.now().isoformat(),
            }
            self._file_logger.info(f"SYSTEM: {json.dumps(log_entry, ensure_ascii=False)}")


# Global logger instance
_logger: Optional[ChannelLogger] = None


def get_logger() -> ChannelLogger:
    """Get the global channel logger instance."""
    global _logger
    if _logger is None:
        _logger = ChannelLogger()
    return _logger


def init_logger(log_dir: Optional[str] = None, log_to_file: bool = True, 
               log_to_console: bool = True) -> ChannelLogger:
    """Initialize the global channel logger.
    
    Args:
        log_dir: Directory to store log files
        log_to_file: Whether to log to file
        log_to_console: Whether to log to console
        
    Returns:
        The initialized logger instance
    """
    global _logger
    _logger = ChannelLogger(log_dir, log_to_file, log_to_console)
    return _logger