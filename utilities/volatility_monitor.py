from decimal import Decimal
import numpy as np
from utilities.numeric_utils import ensure_decimal

class VolatilityCalculator:
    def __init__(self, window_size=14):
        self.window_size = window_size
        self.volatility_cache = {}  # Cache volatility results

    def calculate_atr(self, candles):
        """
        Calculate Average True Range (ATR) from candle data
        candles should be a list of [timestamp, open, close, high, low, volume]
        """
        if len(candles) < 2:
            return Decimal("0")
            
        true_ranges = []
        
        for i in range(1, min(len(candles), self.window_size + 1)):
            # Parse high, low, close from current and previous candles
            high = ensure_decimal(candles[i-1][3])
            low = ensure_decimal(candles[i-1][4])
            close_prev = ensure_decimal(candles[i][2])
            
            # Calculate true range components
            range1 = high - low
            range2 = abs(high - close_prev)
            range3 = abs(low - close_prev)
            
            # True range is the maximum of these components
            tr = max(range1, range2, range3)
            true_ranges.append(tr)
        
        # Calculate average
        if not true_ranges:
            return Decimal("0")
            
        atr = sum(true_ranges) / len(true_ranges)
        return atr

    def calculate_bollinger_bands(self, closes, std_dev_multiplier=2):
        """
        Calculate Bollinger Bands from close prices
        Returns: (middle_band, upper_band, lower_band)
        """
        if len(closes) < self.window_size:
            return None, None, None
            
        # Calculate SMA (middle band)
        window_closes = closes[:self.window_size]
        sma = sum(ensure_decimal(p) for p in window_closes) / Decimal(self.window_size)
        
        # Calculate standard deviation
        variance = sum((p - sma) ** 2 for p in window_closes) / Decimal(self.window_size)
        std_dev = variance.sqrt()
        
        # Calculate upper and lower bands
        upper_band = sma + (std_dev * std_dev_multiplier)
        lower_band = sma - (std_dev * std_dev_multiplier)
        
        return sma, upper_band, lower_band

    async def get_volatility_rank(self, api_client, symbol, period=30):
        """
        Calculate volatility rank compared to historical volatility
        Returns a value between 0-100, where 100 is highest volatility
        """
        try:
            # Check cache first
            cache_key = f"{symbol}_{period}"
            if cache_key in self.volatility_cache:
                cache_time, vol_rank = self.volatility_cache[cache_key]
                if time.time() - cache_time < 3600:  # Cache valid for 1 hour
                    return vol_rank
                    
            # Get historical candle data
            klines = await api_client.get_klines(symbol, "1day", limit=period)
            
            if not klines or len(klines) < period/2:
                return 50  # Return neutral value on insufficient data
                
            # Calculate daily volatility for each day
            daily_volatility = []
            for i in range(len(klines)-1):
                candle = klines[i:i+2]
                daily_vol = float(self.calculate_atr(candle)) / float(Decimal(candle[0][2])) * 100  # ATR as percentage of price
                daily_volatility.append(daily_vol)
            
            # Calculate current volatility (most recent day)
            current_volatility = daily_volatility[0] if daily_volatility else 0
            
            # Calculate percentile rank of current volatility
            if not daily_volatility:
                return 50
                
            daily_volatility.sort()
            rank = daily_volatility.index(current_volatility) / len(daily_volatility) * 100
            
            # Cache result
            self.volatility_cache[cache_key] = (time.time(), rank)
            
            return rank
            
        except Exception as e:
            print(f"Error calculating volatility rank: {e}")
            return 50  # Return neutral value on error
