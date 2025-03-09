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
    Ichimoku Cloud trading strategy that identifies trend direction and potential
    support/resistance levels using the Ichimoku Kinko Hyo indicator system.
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
        Calculate Ichimoku Cloud signal
        
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
            
            # Calculate Ichimoku components
            # Tenkan-sen (Conversion Line): (highest high + lowest low)/2 for the past 9 periods
            high_tenkan = df['high'].rolling(window=self.tenkan_period).max()
            low_tenkan = df['low'].rolling(window=self.tenkan_period).min()
            df['tenkan_sen'] = (high_tenkan + low_tenkan) / 2
            
            # Kijun-sen (Base Line): (highest high + lowest low)/2 for the past 26 periods
            high_kijun = df['high'].rolling(window=self.kijun_period).max()
            low_kijun = df['low'].rolling(window=self.kijun_period).min()
            df['kijun_sen'] = (high_kijun + low_kijun) / 2
            
            # Senkou Span A (Leading Span A): (Conversion Line + Base Line)/2 displaced 26 periods ahead
            df['senkou_span_a'] = ((df['tenkan_sen'] + df['kijun_sen']) / 2).shift(self.displacement)
            
            # Senkou Span B (Leading Span B): (highest high + lowest low)/2 for past 52 periods, displaced 26 periods ahead
            high_senkou = df['high'].rolling(window=self.senkou_span_b_period).max()
            low_senkou = df['low'].rolling(window=self.senkou_span_b_period).min()
            df['senkou_span_b'] = ((high_senkou + low_senkou) / 2).shift(self.displacement)
            
            # Chikou Span (Lagging Span): Current closing price displaced 26 periods back
            df['chikou_span'] = df['close'].shift(-self.displacement)
            
            # Get latest values for analysis
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            
            # Current price
            current_price = Decimal(str(latest['close']))
            
            # Check for bullish signal
            bullish_signal = (
                # Price above the cloud
                latest['close'] > latest['senkou_span_a'] and 
                latest['close'] > latest['senkou_span_b'] and
                # Tenkan-sen crosses above Kijun-sen (bullish TK cross)
                latest['tenkan_sen'] > latest['kijun_sen'] and
                prev['tenkan_sen'] <= prev['kijun_sen'] and
                # Chikou Span is above the price from 26 periods ago
                df['chikou_span'].iloc[-self.displacement-1] > df['close'].iloc[-self.displacement-1] and
                # Cloud is green (bullish) ahead
                latest['senkou_span_a'] > latest['senkou_span_b']
            )
            
            # Check for bearish signal
            bearish_signal = (
                # Price below the cloud
                latest['close'] < latest['senkou_span_a'] and 
                latest['close'] < latest['senkou_span_b'] and
                # Tenkan-sen crosses below Kijun-sen (bearish TK cross)
                latest['tenkan_sen'] < latest['kijun_sen'] and
                prev['tenkan_sen'] >= prev['kijun_sen'] and
                # Chikou Span is below the price from 26 periods ago
                df['chikou_span'].iloc[-self.displacement-1] < df['close'].iloc[-self.displacement-1] and
                # Cloud is red (bearish) ahead
                latest['senkou_span_a'] < latest['senkou_span_b']
            )
            
            # Generate signal
            if bullish_signal:
                # Calculate position size based on risk parameters
                position_size = min(
                    self.max_position_size_usd / current_price,
                    Decimal('0.2')  # Max 20% of available balance
                )
                
                return {
                    "symbol": symbol,
                    "action": "buy",
                    "price": str(current_price),
                    "size": str(position_size),
                    "order_type": "market",
                    "reason": f"Ichimoku bullish signal: TK Cross with price above cloud",
                    "risk_score": 0.5,  # Medium risk
                    "take_profit": str(ensure_decimal(current_price) * (1 + self.profit_target_pct/100)),
                    "stop_loss": str(ensure_decimal(current_price) * (1 - self.stop_loss_pct/100))
                }
            elif bearish_signal and self.market_type == "futures":
                # For futures, we can go short
                position_size = min(
                    self.max_position_size_usd / current_price,
                    Decimal('0.2')  # Max 20% of available balance
                )
                
                return {
                    "symbol": symbol,
                    "action": "sell",
                    "price": str(current_price),
                    "size": str(position_size),
                    "order_type": "market",
                    "reason": f"Ichimoku bearish signal: TK Cross with price below cloud",
                    "risk_score": 0.5,  # Medium risk
                    "take_profit": str(current_price * (1 - self.profit_target_pct/100)),
                    "stop_loss": str(current_price * (1 + self.stop_loss_pct/100))
                }
                
            return None
            
        except Exception as e:
            self.logger.error(f"Error calculating Ichimoku signal for {symbol}: {e}")
            return None
