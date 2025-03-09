from decimal import Decimal
from typing import List, Dict, Tuple
from .base import Strategy
from utilities.numeric_utils import ensure_decimal

class BreakoutTradingStrategy(Strategy):
    def __init__(self, config: dict, name: str = "breakout_trading"):
        super().__init__(config["strategies"]["breakout_trading"], name)
        self.api_client = None
        self.resistance_period = self.config.get("parameters", {}).get("resistance_period", 20)
        self.confirmation_candles = self.config.get("parameters", {}).get("confirmation_candles", 3)
        self.min_volume_increase = Decimal(self.config.get("parameters", {}).get("min_volume_increase", "2.0"))

    def set_api_client(self, api_client):
        """Set the API client after initialization"""
        self.api_client = api_client

    async def _generate_raw_signals(self, market_data: dict) -> List[dict]:
        """
        Breakout Trading strategy: Buy when resistance level is broken
        
        Args:
            market_data: Dictionary containing market data
            
        Returns:
            List of trading signals if breakouts detected
        """
        try:
            signals = []
            pairs = market_data.get("pairs", [])
            
            if not pairs and self.api_client:
                pairs = await self.api_client.get_liquid_pairs(1000)
            
            for pair in pairs[:20]:  # Limit to 20 pairs for efficiency
                resistance, support, current_price, volume_confirmed = await self.analyze_levels(pair)
                
                # Check for breakout with volume confirmation
                if current_price > resistance and volume_confirmed:
                    self.logger.info(f"Bullish breakout detected for {pair}. Current: {current_price}, Resistance: {resistance}")
                    
                    # Calculate size based on risk parameters
                    size = await self._calculate_position_size(pair, current_price)
                    
                    # Create buy signal
                    signals.append({
                        "symbol": pair,
                        "action": "buy",
                        "price": str(current_price),
                        "size": str(size),
                        "order_type": "trailing_stop",  # Use trailing stop for breakout trades
                        "reason": f"Breakout: {current_price} > {resistance}",
                        "risk_score": 0.6  # Breakout has moderate risk
                    })
                
                # Check for breakdown with volume confirmation
                elif current_price < support and volume_confirmed:
                    self.logger.info(f"Bearish breakdown detected for {pair}. Current: {current_price}, Support: {support}")
                    
                    # Calculate size based on risk parameters
                    size = await self._calculate_position_size(pair, current_price)
                    
                    # Create sell signal
                    signals.append({
                        "symbol": pair,
                        "action": "sell",
                        "price": str(current_price),
                        "size": str(size),
                        "order_type": "trailing_stop",
                        "reason": f"Breakdown: {current_price} < {support}",
                        "risk_score": 0.6
                    })
            
            return signals
            
        except Exception as e:
            self.logger.error(f"Error in Breakout Trading strategy: {e}")
            return []

    async def analyze_levels(self, symbol) -> Tuple[Decimal, Decimal, Decimal, bool]:
        """
        Calculate resistance/support levels and check volume confirmation
        
        Args:
            symbol: Trading pair symbol
            
        Returns:
            Tuple of (resistance, support, current_price, volume_confirmed)
        """
        try:
            # Get historical candle data
            if not self.api_client:
                self.logger.error(f"API client not available for {symbol}")
                return Decimal("0"), Decimal("0"), Decimal("0"), False
                
            klines = await self.api_client.get_klines(symbol, "1hour", limit=self.resistance_period + 10)
            
            if not klines or len(klines) < self.resistance_period:
                return Decimal("0"), Decimal("0"), Decimal("0"), False
            
            # Extract high, low, close prices and volumes
            highs = [Decimal(kline[3]) for kline in klines]
            lows = [Decimal(kline[4]) for kline in klines]
            closes = [Decimal(kline[2]) for kline in klines]
            volumes = [Decimal(kline[5]) for kline in klines]
            
            # Calculate resistance and support
            resistance = max(ensure_decimal(h) for h in highs[1:self.resistance_period+1])  # Skip the most recent candle
            support = min(ensure_decimal(l) for l in lows[1:self.resistance_period+1])
            current_price = closes[0]  # Most recent close
            
            # Check if volume is increasing (volume confirmation)
            avg_volume = sum(volumes[self.confirmation_candles+1:]) / (len(volumes) - self.confirmation_candles - 1)
            recent_volume = sum(volumes[:self.confirmation_candles]) / self.confirmation_candles
            volume_confirmed = recent_volume > (avg_volume * self.min_volume_increase)
            
            return resistance, support, current_price, volume_confirmed
        except Exception as e:
            self.logger.error(f"Error analyzing levels for {symbol}: {e}")
            return Decimal("0"), Decimal("0"), Decimal("0"), False
        
    async def _calculate_position_size(self, symbol, price):
        """Calculate appropriate position size based on risk parameters"""
        try:
            from utilities.trade_sizing import calculate_trade_size
            
            # Convert price to Decimal if it's not already
            if not isinstance(price, Decimal):
                price = Decimal(str(price))
            
            # Use the utility function to calculate the size
            if self.api_client:
                size = await calculate_trade_size(
                    self.api_client,
                    symbol,
                    price,
                    self.config
                )
            else:
                size = Decimal("0.01")  # Default minimal size
                self.logger.warning(f"API client not available, using default position size for {symbol}")
            
            return size
        except Exception as e:
            self.logger.error(f"Error calculating position size: {e}")
            return Decimal("0.01")  # Default minimal size on error


    async def analyze_levels(self, symbol):
        """
        Calculate resistance/support levels and check volume confirmation
        Returns: resistance, support, current_price, volume_confirmed
        """
        # Get historical candle data
        klines = await self.api_client.get_klines(symbol, "1hour", limit=self.resistance_period + 10)
        
        if not klines or len(klines) < self.resistance_period:
            return Decimal("0"), Decimal("0"), Decimal("0"), False
        
        # Extract high, low, close prices and volumes
        highs = [Decimal(kline[3]) for kline in klines]
        lows = [Decimal(kline[4]) for kline in klines]
        closes = [Decimal(kline[2]) for kline in klines]
        volumes = [Decimal(kline[5]) for kline in klines]
        
        # Calculate resistance and support
        resistance = max(highs[1:self.resistance_period+1])  # Skip the most recent candle
        support = min(lows[1:self.resistance_period+1])
        current_price = closes[0]  # Most recent close
        
        # Check if volume is increasing (volume confirmation)
        avg_volume = sum(volumes[self.confirmation_candles+1:]) / (len(volumes) - self.confirmation_candles - 1)
        recent_volume = sum(volumes[:self.confirmation_candles]) / self.confirmation_candles
        volume_confirmed = recent_volume > (avg_volume * self.min_volume_increase)
        
        return resistance, support, current_price, volume_confirmed
        
    async def _calculate_position_size(self, symbol, price):
        """Calculate appropriate position size based on risk parameters"""
        from utilities.trade_sizing import calculate_trade_size
        
        # Convert price to Decimal if it's not already
        if not isinstance(price, Decimal):
            price = Decimal(str(price))
        
        # Use the utility function to calculate the size
        size = await calculate_trade_size(
            self.api_client,
            symbol,
            price,
            self.config
        )
        
        return str(size)
