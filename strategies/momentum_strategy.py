# momentum_strategy.py
import logging
import numpy as np
import pandas as pd
from decimal import Decimal
from typing import List, Dict, Optional
import time
from utilities.numeric_utils import ensure_decimal

from .base import Strategy

class MomentumStrategy(Strategy):
    """
    Momentum trading strategy that identifies and trades with established market trends.
    Uses a combination of moving averages, RSI, and volume analysis to identify strong momentum.
    """
    
    def __init__(self, config: dict, name: str = "momentum"):
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
        
        # Timeframes and lookback periods
        self.timeframe = params.get("timeframe", "15m")
        self.short_period = params.get("short_period", 9)
        self.medium_period = params.get("medium_period", 21)
        self.long_period = params.get("long_period", 50)
        self.rsi_period = params.get("rsi_period", 14)
        self.volume_lookback = params.get("volume_lookback", 20)
        
        # Threshold parameters
        self.rsi_overbought = params.get("rsi_overbought", 70)
        self.rsi_oversold = params.get("rsi_oversold", 30)
        self.min_volume_percentile = params.get("min_volume_percentile", 70)
        self.min_price_change_pct = Decimal(str(params.get("min_price_change_pct", 0.5)))
        
        # Trade parameters
        self.profit_target_pct = Decimal(str(params.get("profit_target_pct", 1.5)))
        self.stop_loss_pct = Decimal(str(params.get("stop_loss_pct", 0.8)))
        self.max_position_size_usd = Decimal(str(params.get("max_position_size_usd", 100)))
        
        # Cooldown to avoid overtrading
        self.signal_cooldown_hours = params.get("signal_cooldown_hours", 4)
        self.last_signal_time = {}  # Track last signal time per symbol
        
        # API client will be set later
        self.api_client = None

    def set_api_client(self, api_client):
        """Set the API client after initialization"""
        self.api_client = api_client

    async def _generate_raw_signals(self, market_data: dict) -> List[dict]:
        """
        Generate momentum trading signals based on market data
        
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
                
                # Calculate technical indicators
                signal = await self._calculate_momentum_signal(df, symbol)
                
                if signal:
                    # Update last signal time
                    self.last_signal_time[symbol] = current_time
                    signals.append(signal)
            
            return signals
            
        except Exception as e:
            self.logger.error(f"Error in momentum strategy: {e}")
            return []
    
    async def _calculate_momentum_signal(self, df: pd.DataFrame, symbol: str) -> Optional[dict]:
        """
        Calculate momentum signal based on technical indicators
        
        Args:
            df: DataFrame with OHLCV data
            symbol: Trading symbol
            
        Returns:
            Signal dictionary or None if no signal
        """
        try:
            # Ensure we have enough data
            if len(df) < self.long_period + 10:
                return None
            
            # Calculate moving averages
            df['sma_short'] = df['close'].rolling(window=self.short_period).mean()
            df['sma_medium'] = df['close'].rolling(window=self.medium_period).mean()
            df['sma_long'] = df['close'].rolling(window=self.long_period).mean()
            
            # Calculate RSI
            delta = df['close'].diff()
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)
            avg_gain = gain.rolling(window=self.rsi_period).mean()
            avg_loss = loss.rolling(window=self.rsi_period).mean()
            rs = avg_gain / avg_loss.replace(0, 0.001)  # Avoid division by zero
            df['rsi'] = 100 - (100 / (1 + rs))
            
            # Calculate volume metrics
            df['volume_ma'] = df['volume'].rolling(window=self.volume_lookback).mean()
            df['volume_ratio'] = df['volume'] / df['volume_ma']
            
            # Calculate price momentum
            df['price_change_pct'] = (df['close'].pct_change(5) * 100)
            
            # Get latest values
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            
            # Check for buy signal
            buy_signal = (
                latest['sma_short'] > latest['sma_medium'] > latest['sma_long'] and
                prev['sma_short'] <= prev['sma_medium'] and
                latest['rsi'] > 50 and latest['rsi'] < self.rsi_overbought and
                latest['volume_ratio'] > 1.2 and
                float(latest['price_change_pct']) > float(self.min_price_change_pct)
            )
            
            # Check for sell signal (for futures or existing positions)
            sell_signal = (
                latest['sma_short'] < latest['sma_medium'] < latest['sma_long'] and
                prev['sma_short'] >= prev['sma_medium'] and
                latest['rsi'] < 50 and latest['rsi'] > self.rsi_oversold and
                latest['volume_ratio'] > 1.2 and
                float(latest['price_change_pct']) < -float(self.min_price_change_pct)
            )
            
            # Get current price
            current_price = Decimal(str(latest['close']))
            
            # Generate signal
            if buy_signal:
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
                    "reason": f"Momentum buy signal: RSI={latest['rsi']:.1f}, Volume ratio={latest['volume_ratio']:.2f}",
                    "risk_score": 0.6,  # Medium-high risk
                    "take_profit": str(ensure_decimal(current_price) * (1 + self.profit_target_pct/100)),
                    "stop_loss": str(ensure_decimal(current_price) * (1 - self.stop_loss_pct/100))
                }
            elif sell_signal and self.market_type == "futures":
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
                    "reason": f"Momentum sell signal: RSI={latest['rsi']:.1f}, Volume ratio={latest['volume_ratio']:.2f}",
                    "risk_score": 0.6,  # Medium-high risk
                    "take_profit": str(current_price * (1 - self.profit_target_pct/100)),
                    "stop_loss": str(current_price * (1 + self.stop_loss_pct/100))
                }
                
            return None
            
        except Exception as e:
            self.logger.error(f"Error calculating momentum signal for {symbol}: {e}")
            return None
