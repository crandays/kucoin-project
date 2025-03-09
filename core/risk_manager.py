from decimal import Decimal
import time
from utilities.numeric_utils import ensure_decimal

class RiskManager:
    def __init__(self, config, logger):
        self.max_daily_loss = Decimal(config["trading_engine"]["max_daily_loss_percentage"])
        self.max_positions = config["trading_engine"]["max_open_positions"]
        self.order_size = Decimal(config["trading_engine"]["order_size_percentage"])
        self.daily_loss = Decimal("0")
        self.open_positions = 0
        self.last_reset = time.time()
        self.logger = logger

    def check_daily_loss(self):
        """
        Check if daily loss limit hasn't been reached and max positions not exceeded
        Returns True if trading can continue
        """
        # Reset daily loss every 24 hours
        if time.time() - self.last_reset > 86400:
            self.logger.info("Resetting daily loss counter")
            self.daily_loss = Decimal("0")
            self.last_reset = time.time()
        
        can_trade = self.daily_loss < self.max_daily_loss and self.open_positions < self.max_positions
        if not can_trade:
            self.logger.warning(f"Risk limits reached: daily_loss={self.daily_loss}, positions={self.open_positions}")
        
        return can_trade

    def update_risk(self, pnl):
        """Update risk metrics after a trade"""
        if pnl < 0:
            self.daily_loss += abs(pnl)
            self.logger.info(f"Updated daily loss: {self.daily_loss}")
            
        # Update position count based on trade type
        position_change = 1 if pnl > 0 else -1
        self.open_positions = max(0, self.open_positions + position_change)
        self.logger.info(f"Updated positions count: {self.open_positions}")

    def get_position_size(self, balance):
        """Calculate position size based on account balance and risk settings"""
        return ensure_decimal(balance) * self.order_size / Decimal("100")
        
    def can_open_position(self, symbol, risk_score=None):
        """
        Check if a new position can be opened for a given symbol
        Considers:
        - Daily loss limit
        - Max position count
        - Symbol-specific risk
        """
        # Check basic risk limits
        if not self.check_daily_loss():
            return False
            
        # Can add more specific risk logic here, such as:
        # - Maximum exposure per symbol
        # - Maximum correlation between positions
        # - Volatility-based position sizing
        
        if risk_score and risk_score > 0.8:  # High risk threshold
            self.logger.warning(f"High risk score for {symbol}: {risk_score}")
            return False
            
        return True
