from decimal import Decimal
from typing import List, Dict
from .base import Strategy
from utilities.numeric_utils import ensure_decimal

class TrendFollowingStrategy(Strategy):
    def __init__(self, config: dict, name: str = "trend_following"):
        super().__init__(config["strategies"]["trend_following"], name)
        self.api_client = None  # Will be set after initialization
        self.sma_short_period = self.config.get("parameters", {}).get("sma_short_period", 9)
        self.sma_long_period = self.config.get("parameters", {}).get("sma_long_period", 21)
        self.adx_period = self.config.get("parameters", {}).get("adx_period", 14)
        self.adx_threshold = self.config.get("parameters", {}).get("adx_threshold", 25)

    def set_api_client(self, api_client):
        """Set the API client after initialization"""
        self.api_client = api_client

    async def _generate_raw_signals(self, market_data: dict) -> List[dict]:
        """
        Generate trading signals based on trend following strategy
        
        Args:
            market_data: Dictionary containing market data including pairs
            
        Returns:
            List of signal dictionaries
        """
        try:
            signals = []
            pairs = market_data.get("pairs", [])
            
            if not pairs and self.api_client:
                # Fall back to fetching pairs from API if not provided in market_data
                pairs = await self.api_client.get_liquid_pairs(1000)
            
            for pair in pairs[:20]:  # Limit to 20 pairs for efficiency
                sma_short, sma_long, adx, current_price = await self.calculate_indicators(pair)
                
                if sma_short == Decimal("0"):  # Skip if calculation failed
                    continue
                    
                # Check for strong trend with ADX
                if adx > self.adx_threshold:
                    # Bullish trend: short SMA above long SMA
                    if sma_short > sma_long:
                        trend_strength = ((ensure_decimal(sma_short) / ensure_decimal(sma_long)) - 1) * 100  # percentage
                        
                        self.logger.info(f"Bullish trend detected for {pair}: short {sma_short} > long {sma_long}, ADX: {adx}")
                        
                        # Calculate position size
                        size = await self._calculate_position_size(pair, current_price)
                        
                        # Create buy signal
                        signals.append({
                            "symbol": pair,
                            "action": "buy",
                            "price": str(current_price),
                            "size": str(size),
                            "order_type": "trailing_stop",  # Use trailing stop for trend following
                            "reason": f"Bullish trend: {trend_strength:.2f}% strength, ADX: {adx}",
                            "risk_score": 0.4
                        })
                    
                    # Bearish trend: short SMA below long SMA
                    elif sma_short < sma_long:
                        trend_strength = ((ensure_decimal(sma_long) / ensure_decimal(sma_short)) - 1) * 100  # percentage
                        
                        self.logger.info(f"Bearish trend detected for {pair}: short {sma_short} < long {sma_long}, ADX: {adx}")
                        
                        # Calculate position size
                        size = await self._calculate_position_size(pair, current_price)
                        
                        # Create sell signal
                        signals.append({
                            "symbol": pair,
                            "action": "sell",
                            "price": str(current_price),
                            "size": str(size),
                            "order_type": "trailing_stop",
                            "reason": f"Bearish trend: {trend_strength:.2f}% strength, ADX: {adx}",
                            "risk_score": 0.4
                        })
            
            return signals
            
        except Exception as e:
            self.logger.error(f"Error in Trend Following strategy: {e}")
            return []

    async def calculate_indicators(self, symbol):
        """Calculate short SMA, long SMA, ADX and current price"""
        try:
            # Get historical candle data
            period = max(self.sma_short_period, self.sma_long_period, self.adx_period) + 10
            
            if self.api_client:
                klines = await self.api_client.get_klines(symbol, "1hour", limit=period)
            else:
                # Use base class methods if api_client not available
                # This would need implementation based on your API structure
                self.logger.error("API client not available for fetching klines")
                return Decimal("0"), Decimal("0"), 0, Decimal("0")
            
            if not klines or len(klines) < period:
                return Decimal("0"), Decimal("0"), 0, Decimal("0")
            
            # Extract close prices, high, low for calculations
            closes = [Decimal(kline[2]) for kline in klines]
            highs = [Decimal(kline[3]) for kline in klines]
            lows = [Decimal(kline[4]) for kline in klines]
            
            # Calculate SMAs
            sma_short = sum(ensure_decimal(c) for c in closes[:self.sma_short_period]) / Decimal(self.sma_short_period)
            sma_long = sum(ensure_decimal(c) for c in closes[:self.sma_long_period]) / Decimal(self.sma_long_period)
            
            # Get current price (most recent close)
            current_price = closes[0]
            
            # Calculate ADX
            adx = self._calculate_adx(highs[:self.adx_period+1], lows[:self.adx_period+1], closes[:self.adx_period+1])
            
            return sma_short, sma_long, adx, current_price
            
        except Exception as e:
            self.logger.error(f"Error calculating indicators for {symbol}: {e}")
            return Decimal("0"), Decimal("0"), 0, Decimal("0")

    def _calculate_adx(self, highs, lows, closes):
        """Calculate ADX (Average Directional Index) indicator"""
        try:
            # Calculate True Range
            tr = []
            for i in range(1, len(highs)):
                hl = ensure_decimal(highs[i-1]) - ensure_decimal(lows[i-1])
                hc = abs(ensure_decimal(highs[i-1]) - ensure_decimal(closes[i]))
                lc = abs(ensure_decimal(lows[i-1]) - ensure_decimal(closes[i]))
                tr.append(max(hl, hc, lc))
            
            # Calculate Directional Movement
            plus_dm = []
            minus_dm = []
            for i in range(1, len(highs)):
                up_move = ensure_decimal(highs[i-1]) - ensure_decimal(highs[i])
                down_move = ensure_decimal(lows[i]) - ensure_decimal(lows[i-1])
                
                if up_move > down_move and up_move > 0:
                    plus_dm.append(up_move)
                else:
                    plus_dm.append(0)
                    
                if down_move > up_move and down_move > 0:
                    minus_dm.append(down_move)
                else:
                    minus_dm.append(0)
            
            # Calculate smoothed averages
            tr_sum = sum(tr)
            plus_dm_sum = sum(plus_dm)
            minus_dm_sum = sum(minus_dm)
            
            # Calculate DI+ and DI-
            plus_di = 100 * ensure_decimal(plus_dm_sum) / ensure_decimal(tr_sum) if tr_sum > 0 else 0
            minus_di = 100 * ensure_decimal(minus_dm_sum) / ensure_decimal(tr_sum) if tr_sum > 0 else 0
            
            # Calculate ADX
            dx = 100 * abs(ensure_decimal(plus_di) - ensure_decimal(minus_di)) / (ensure_decimal(plus_di) + ensure_decimal(minus_di)) if (plus_di + minus_di) > 0 else 0
            adx = dx  # Normally we'd smooth this over the ADX period, but keeping it simple
            
            return adx
            
        except Exception:
            return 0  # Return 0 ADX on error
            
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

