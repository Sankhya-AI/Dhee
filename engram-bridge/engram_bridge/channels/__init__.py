from engram_bridge.channels.base import BaseChannel, IncomingMessage

# TelegramChannel and WebChannel are lazy-imported in bridge.py to avoid
# pulling in python-telegram-bot or fastapi when only one channel is used.

__all__ = ["BaseChannel", "IncomingMessage"]
