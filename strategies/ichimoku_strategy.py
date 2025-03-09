# ichimoku_strategy.py
import logging
import numpy as np
import pandas as pd
from decimal import Decimal
from typing import List, Dict, Optional
import time
from utilities.numeric_utils import ensure_decimal

from .base import Strategy

class IchimokuStrategy(Strategy):
    """
    Enhanced Ichimoku Cloud trading strategy that identifies trend direction and potential
    support/resistance levels using the Ichimoku Kinko Hyo indicator system.
    Includes ADX filter for trend strength and multiple confirmation layers.
    """
    
    def __init__(self, config: dict, name: str = "ichimoku"):
        super().__init__(config, name)
        self.logger = logging.getLogger(f"strategy.{name}")
        
        # Load parameters from config
        params = self.config.get("parameters", {})
        self.market_type = params.get("market_type", "spot")  # 'spot' or 'futures'
        
        # Load market-specific parameters if available
        if self.market_type == "spot" and "spot_parameters" in self.config:
            market_params = self.config.get("spot_parameters", {})
            params = {**params, **market_params}  # Override with spot-specific params
        elif self.market_type == "futures" and "futures_parameters" in self.config:
            market_params = self.config.get("futures_parameters", {})
            params = {**params, **market_params}  # Override with futures-specific params
        
        # Ichimoku parameters
        self.tenkan_period = params.get("tenkan_period", 9)
        self.kijun_period = params.get("kijun_period", 26)
        self.senkou_span_b_period = params.get("senkou_span_b_period", 52)
        self.displacement = params.get("displacement", 26)
        
        # ADX parameters for trend filtering
        self.adx_period = params.get("adx_period", 14)
        self.adx_threshold = params.get("adx_threshold", 25)
        
        # Trade parameters
        self.profit_target_pct = Decimal(str(params.get("profit_target_pct", 2.0)))
        self.stop_loss_pct = Decimal(str(params.get("stop_loss_pct", 1.0)))
        self.max_position_size_usd = Decimal(str(params.get("max_position_size_usd", 100)))
        
        # Cooldown to avoid overtrading
        self.signal_cooldown_hours = params.get("signal_cooldown_hours", 6)
        self.last_signal_time = {}  # Track last signal time per symbol
        
        # API client will be set later
        self.api_client = None

    def set_api_client(self, api_client):
        """Set the API client after initialization"""
        self.api_client = api_client

    def calculate_adx(self, df: pd.DataFrame) -> pd.Series:
        """
        Calculate Average Directional Index (ADX) to measure trend strength
        
        Args:
            df: DataFrame with OHLCV data
            
        Returns:
            Series containing ADX values
        """
        # Calculate True Range
        high_low = df['high'] - df['low']
        high_close_prev = abs(df['high'] - df['close'].shift(1))
        low_close_prev = abs(df['low'] - df['close'].shift(1))
        
        tr = pd.concat([high_low, high_close_prev, low_close_prev], axis=1).max(axis=1)
        
        # Calculate +DM and -DM
        plus_dm = df['high'].diff()
        minus_dm = df['low'].shift(1) - df['low']
        
        plus_dm = plus_dm.where(plus_dm > 0, 0)
        minus_dm = minus_dm.where(minus_dm > 0, 0)
        
        # Conditions for +DM and -DM
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
        
        # Smooth with Wilder's smoothing
        period = self.adx_period
        
        # Initialize
        smoothed_tr = tr.copy()
        smoothed_plus_dm = plus_dm.copy()
        smoothed_minus_dm = minus_dm.copy()
        
        # Apply smoothing
        for i in range(1, len(df)):
            smoothed_tr.iloc[i] = smoothed_tr.iloc[i-1] - (smoothed_tr.iloc[i-1] / period) + tr.iloc[i]
            smoothed_plus_dm.iloc[i] = smoothed_plus_dm.iloc[i-1] - (smoothed_plus_dm.iloc[i-1] / period) + plus_dm.iloc[i]
            smoothed_minus_dm.iloc[i] = smoothed_minus_dm.iloc[i-1] - (smoothed_minus_dm.iloc[i-1] / period) + minus_dm.iloc[i]
        
        # Calculate +DI and -DI
        plus_di = 100 * (smoothed_plus_dm / smoothed_tr)
        minus_di = 100 * (smoothed_minus_dm / smoothed_tr)
        
        # Calculate DX
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
        
        # Calculate ADX
        adx = dx.rolling(window=period).mean()
        
        return adx

    async def _generate_raw_signals(self, market_data: dict) -> List[dict]:
        """
        Generate Ichimoku Cloud trading signals based on market data
        
        Args:
            market_data: Dictionary containing market data with structure:
                {
                    "symbol": {
                        "klines": DataFrame with OHLCV data,
                        "ticker": Current ticker data,
                        "orderbook": Current orderbook data
                    },
                    ...
                }
                
        Returns:
            List of trading signals
        """
        signals = []
        current_time = time.time()
        
        try:
            for symbol, data in market_data.items():
                # Skip if we don't have kline data
                if "klines" not in data or data["klines"].empty:
                    continue
                    
                # Check cooldown period
                if symbol in self.last_signal_time:
                    hours_since_last_signal = (current_time - self.last_signal_time[symbol]) / 3600
                    if hours_since_last_signal < self.signal_cooldown_hours:
                        continue
                
                # Get OHLCV data
                df = data["klines"].copy()
                
                # Calculate Ichimoku Cloud components
                signal = await self._calculate_ichimoku_signal(df, symbol)
                
                if signal:
                    # Update last signal time
                    self.last_signal_time[symbol] = current_time
                    signals.append(signal)
            
            return signals
            
        except Exception as e:
            self.logger.error(f"Error in Ichimoku strategy: {e}")
            return []
    
    async def _calculate_ichimoku_signal(self, df: pd.DataFrame, symbol: str) -> Optional[dict]:
        """
        Calculate Ichimoku Cloud signal with enhanced confirmation and ADX filter
        
        Args:
            df: DataFrame with OHLCV data
            symbol: Trading symbol
            
        Returns:
            Signal dictionary or None if no signal
        """
        try:
            # Ensure we have enough data
            required_periods = max(self.tenkan_period, self.kijun_period, 
                                  self.senkou_span_b_period) + self.displacement + 10
            if len(df) < required_periods:
                return None
            
            # Convert Decimal columns to float for calculations
            df_float = df.copy()
            for col in ['open', 'high', 'low', 'close', 'volume']:
                if col in df_float.columns:
                    df_float[col] = df_float[col].astype(float)
            
            # Calculate Ichimoku components
            # Tenkan-sen (Conversion Line): (highest high + lowest low)/2 for the past 9 periods
            high_tenkan = df_float['high'].rolling(window=self.tenkan_period).max()
            low_tenkan = df_float['low'].rolling(window=self.tenkan_period).min()
            df_float['tenkan_sen'] = (high_tenkan + low_tenkan) / 2
            
            # Kijun-sen (Base Line): (highest high + lowest low)/2 for the past 26 periods
            high_kijun = df_float['high'].rolling(window=self.kijun_period).max()
            low_kijun = df_float['low'].rolling(window=self.kijun_period).min()
            df_float['kijun_sen'] = (high_kijun + low_kijun) / 2
            
            # Senkou Span A (Leading Span A): (Conversion Line + Base Line)/2 displaced 26 periods ahead
            df_float['senkou_span_a'] = ((df_float['tenkan_sen'] + df_float['kijun_sen']) / 2).shift(self.displacement)
            
            # Senkou Span B (Leading Span B): (highest high + lowest low)/2 for past 52 periods, displaced 26 periods ahead
            high_senkou = df_float['high'].rolling(window=self.senkou_span_b_period).max()
            low_senkou = df_float['low'].rolling(window=self.senkou_span_b_period).min()
            df_float['senkou_span_b'] = ((high_senkou + low_senkou) / 2).shift(self.displacement)
            
            # Chikou Span (Lagging Span): Current closing price displaced 26 periods back
            df_float['chikou_span'] = df_float['close'].shift(-self.displacement)
            
            # Calculate ADX for trend strength
            df_float['adx'] = self.calculate_adx(df_float)
            
            # Get latest values for analysis
            latest_idx = len(df_float) - 1
            prev_idx = latest_idx - 1
            
            # Check if we have a strong trend (ADX filter)
            adx_value = df_float['adx'].iloc[latest_idx]
            strong_trend = adx_value > self.adx_threshold
            
            if not strong_trend:
                self.logger.info(f"No signal for {symbol}: ADX {adx_value:.2f} below threshold {self.adx_threshold}")
                return None
            
            # Current price - use the original Decimal value from df
            current_price = df['close'].iloc[-1]
            
            # Enhanced bullish signal with multiple confirmation layers
            bullish_signal = (
                # 1. Price position relative to cloud - must be above the cloud
                df_float['close'].iloc[latest_idx] > df_float['senkou_span_a'].iloc[latest_idx] and 
                df_float['close'].iloc[latest_idx] > df_float['senkou_span_b'].iloc[latest_idx] and
                
                # 2. Tenkan-sen/Kijun-sen relationship - bullish TK cross or Tenkan above Kijun
                (
                    (df_float['tenkan_sen'].iloc[latest_idx] > df_float['kijun_sen'].iloc[latest_idx] and
                     df_float['tenkan_sen'].iloc[prev_idx] <= df_float['kijun_sen'].iloc[prev_idx])  # Fresh cross
                    or 
                    (df_float['tenkan_sen'].iloc[latest_idx] > df_float['kijun_sen'].iloc[latest_idx] and
                     df_float['tenkan_sen'].iloc[latest_idx] > df_float['tenkan_sen'].iloc[prev_idx])  # Tenkan rising
                ) and
                
                # 3. Chikou Span confirmation - above price from 26 periods ago
                df_float['chikou_span'].iloc[-self.displacement-1] > df_float['close'].iloc[-self.displacement-1] and
                
                # 4. Cloud structure - bullish (green) cloud ahead
                df_float['senkou_span_a'].iloc[latest_idx] > df_float['senkou_span_b'].iloc[latest_idx]
            )
            
            # Enhanced bearish signal with multiple confirmation layers
            bearish_signal = (
                # 1. Price position relative to cloud - must be below the cloud
                df_float['close'].iloc[latest_idx] < df_float['senkou_span_a'].iloc[latest_idx] and 
                df_float['close'].iloc[latest_idx] < df_float['senkou_span_b'].iloc[latest_idx] and
                
                # 2. Tenkan-sen/Kijun-sen relationship - bearish TK cross or Tenkan below Kijun
                (
                    (df_float['tenkan_sen'].iloc[latest_idx] < df_float['kijun_sen'].iloc[latest_idx] and
                     df_float['tenkan_sen'].iloc[prev_idx] >= df_float['kijun_sen'].iloc[prev_idx])  # Fresh cross
                    or
                    (df_float['tenkan_sen'].iloc[latest_idx] < df_float['kijun_sen'].iloc[latest_idx] and
                     df_float['tenkan_sen'].iloc[latest_idx] < df_float['tenkan_sen'].iloc[prev_idx])  # Tenkan falling
                ) and
                
                # 3. Chikou Span confirmation - below price from 26 periods ago
                df_float['chikou_span'].iloc[-self.displacement-1] < df_float['close'].iloc[-self.displacement-1] and
                
                # 4. Cloud structure - bearish (red) cloud ahead
                df_float['senkou_span_a'].iloc[latest_idx] < df_float['senkou_span_b'].iloc[latest_idx]
            )
            
            # Generate signal
            if bullish_signal:
                # Calculate position size based on risk parameters
                position_size = min(
                    self.max_position_size_usd / current_price,
                    Decimal('0.2')  # Max 20% of available balance
                )
                
                self.logger.info(f"Bullish signal for {symbol}: ADX {adx_value:.2f}, strong trend with multiple confirmations")
                
                return {
                    "symbol": symbol,
                    "action": "buy",
                    "price": str(current_price),
                    "size": str(position_size),
                    "order_type": "market",
                    "reason": f"Ichimoku bullish signal: TK Cross with price above cloud, ADX: {adx_value:.2f}",
                    "risk_score": 0.5,  # Medium risk
                    "take_profit": str(current_price * (1 + self.profit_target_pct/100)),
                    "stop_loss": str(current_price * (1 - self.stop_loss_pct/100))
                }
            elif bearish_signal and self.market_type == "futures":
                # For futures, we can go short
                position_size = min(
                    self.max_position_size_usd / current_price,
                    Decimal('0.2')  # Max 20% of available balance
                )
                
                self.logger.info(f"Bearish signal for {symbol}: ADX {adx_value:.2f}, strong trend with multiple confirmations")
                
                return {
                    "symbol": symbol,
                    "action": "sell",
                    "price": str(current_price),
                    "size": str(position_size),
                    "order_type": "market",
                    "reason": f"Ichimoku bearish signal: TK Cross with price below cloud, ADX: {adx_value:.2f}",
                    "risk_score": 0.5,  # Medium risk
                    "take_profit": str(current_price * (1 - self.profit_target_pct/100)),
                    "stop_loss": str(current_price * (1 + self.stop_loss_pct/100))
                }
                
            return None
            
        except Exception as e:
            self.logger.error(f"Error calculating Ichimoku signal for {symbol}: {e}")
            return None

