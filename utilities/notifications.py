import requests
import time

class Notifications:
    def __init__(self, config, logger):
        self.config = config.get("notifications", {})
        self.telegram_token = self.config.get("telegram_token", "")
        self.telegram_chat_id = self.config.get("telegram_chat_id", "")
        self.enabled = self.config.get("enabled", False)
        self.last_notification = {}
        self.notification_cooldown = self.config.get("cooldown_seconds", 300)  # 5 minutes default
        self.logger = logger
        
    async def send_notification(self, message):
        """Send a standard notification"""
        return await self.send_telegram_message(message)
        
    async def send_error_notification(self, message):
        """Send an error notification"""
        error_prefix = "❗ ERROR: "
        return await self.send_telegram_message(f"{error_prefix}{message}")

    async def send_telegram_message(self, message):
        """Send message via Telegram bot"""
        if not self.enabled or not self.telegram_token or not self.telegram_chat_id:
            self.logger.debug(f"Notifications disabled or not configured, message: {message}")
            return False

        # Check for rate limiting
        message_hash = hash(message)
        current_time = time.time()
        
        if message_hash in self.last_notification:
            elapsed = current_time - self.last_notification[message_hash]
            if elapsed < self.notification_cooldown:
                self.logger.debug(f"Skipping notification (cooldown): {message}")
                return False
        
        # Update last notification time for this message
        self.last_notification[message_hash] = current_time
        
        # Send message
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            data = {
                "chat_id": self.telegram_chat_id,
                "text": message,
                "parse_mode": "Markdown"
            }
            response = requests.post(url, data=data, timeout=10)
            
            if response.status_code == 200:
                self.logger.debug(f"Telegram notification sent: {message}")
                return True
            else:
                self.logger.error(f"Failed to send Telegram notification: {response.text}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error sending Telegram notification: {e}")
            return False
