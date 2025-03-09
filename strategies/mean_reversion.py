from decimal import Decimal
from typing import List, Dict
from .base import Strategy
from utilities.numeric_utils import ensure_decimal

class MeanReversionStrategy(Strategy):
    def __init__(self, config: dict, name: str = "mean_reversion"):
        super().__init__(config["strategies"]["mean_reversion"], name)
        self.api_client = None
        self.sma_period = self.config.get("parameters", {}).get("sma_period", 20)
        self.deviation_threshold = Decimal(self.config.get("parameters", {}).get("deviation_threshold", "2.0"))
        self.rsi_period = self.config.get("parameters", {}).get("rsi_period", 14)
        self.rsi_oversold = self.config.get("parameters", {}).get("rsi_oversold", 30)
        self.rsi_overbought = self.config.get("parameters", {}).get("rsi_overbought", 70)

    def set_api_client(self, api_client):
        """Set the API client after initialization"""
        self.api_client = api_client

    async def _generate_raw_signals(self, market_data: dict) -> List[dict]:
        """
        Mean Reversion strategy: buys when price is below average, sells when above
        
        Args:
            market_data: Dictionary containing market data
            
        Returns:
            List of trading signals
        """
        try:
            signals = []
            pairs = market_data.get("pairs", [])
            
            if not pairs and self.api_client:
                pairs = await self.api_client.get_liquid_pairs(1000)
            
            for pair in pairs[:20]:  # Limit to 20 pairs for efficiency
                sma, current_price, rsi = await self.calculate_indicators(pair)
                if sma == Decimal("0"):  # Skip if calculation failed
                    continue
                    
                deviation = ((ensure_decimal(current_price) - ensure_decimal(sma)) / ensure_decimal(sma)) * 100  # percentage deviation
                
                # Check for mean reversion opportunities
                if deviation < -self.deviation_threshold and rsi < self.rsi_oversold:
                    self.logger.info(f"Mean reversion buy signal for {pair}: price {current_price} below SMA {sma} by {abs(deviation):.2f}%, RSI: {rsi}")
                    
                    # Calculate position size
                    size = await self._calculate_position_size(pair, current_price)
                    
                    # Create buy signal with OCO order
                    signals.append({
                        "symbol": pair,
                        "action": "buy",
                        "price": str(current_price),
                        "size": str(size),
                        "order_type": "oco",  # One-Cancels-the-Other for take profit and stop loss
                        "reason": f"Mean reversion: {deviation:.2f}% below SMA, RSI: {rsi}",
                        "risk_score": 0.5
                    })
                
                elif deviation > self.deviation_threshold and rsi > self.rsi_overbought:
                    self.logger.info(f"Mean reversion sell signal for {pair}: price {current_price} above SMA {sma} by {deviation:.2f}%, RSI: {rsi}")
                    
                    # Calculate position size
                    size = await self._calculate_position_size(pair, current_price)
                    
                    # Create sell signal
                    signals.append({
                        "symbol": pair,
                        "action": "sell",
                        "price": str(current_price),
                        "size": str(size),
                        "order_type": "oco",
                        "reason": f"Mean reversion: {deviation:.2f}% above SMA, RSI: {rsi}",
                        "risk_score": 0.5
                    })
            
            return signals
            
        except Exception as e:
            self.logger.error(f"Error in Mean Reversion strategy: {e}")
            return []

    async def calculate_indicators(self, symbol):
        """Calculate SMA, current price, and RSI"""
        try:
            # Get historical candle data
            klines = await self.api_client.get_klines(symbol, "1hour", limit=max(self.sma_period, self.rsi_period) + 10)
            
            if not klines or len(klines) < max(self.sma_period, self.rsi_period):
                return Decimal("0"), Decimal("0"), 0
            
            # Extract close prices
            closes = [Decimal(kline[2]) for kline in klines]
            
            # Calculate SMA
            sma = sum(closes[:self.sma_period]) / Decimal(self.sma_period)
            
            # Get current price (most recent close)
            current_price = closes[0]
            
            # Calculate RSI
            rsi = self._calculate_rsi(closes[:self.rsi_period+1])
            
            return sma, current_price, rsi
            
        except Exception as e:
            self.logger.error(f"Error calculating indicators for {symbol}: {e}")
            return Decimal("0"), Decimal("0"), 0

    def _calculate_rsi(self, prices):
        """Calculate RSI indicator"""
        try:
            # Calculate price changes
            deltas = [prices[i-1] - prices[i] for i in range(1, len(prices))]
            
            # Separate gains and losses
            gains = [delta if delta > 0 else 0 for delta in deltas]
            losses = [abs(delta) if delta < 0 else 0 for delta in deltas]
            
            # Average gains and losses
            avg_gain = sum(gains) / len(gains)
            avg_loss = sum(losses) / len(losses) if sum(losses) > 0 else Decimal("0.001")  # Prevent division by zero
            
            # Calculate RS and RSI
            rs = float(avg_gain / avg_loss) if avg_loss > 0 else 100
            rsi = 100 - (100 / (1 + rs))
            
            return rsi
        except Exception:
            return 50  # Return neutral RSI on error
            
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
