import asyncio
import logging
from typing import Dict, List, Optional
from decimal import Decimal
import numpy as np
from collections import defaultdict
import time
from datetime import datetime
from utilities.trade_sizing import calculate_kucoin_liquidation_price
from utilities.numeric_utils import ensure_decimal

class PositionManager:
    def __init__(self, exchange, config, notification_manager, logger=None):
        self.exchange = exchange
        self.config = config
        self.notification_manager = notification_manager
        self.logger = logger or logging.getLogger(__name__)
        
        self.positions = {}
        self.position_locks = defaultdict(asyncio.Lock)
        self.correlation_cache = {}
        self.cache_timestamp = 0
        self.cache_duration = 300  # 5 minutes
        
        self.risk_config = config['position_management']
        self.max_position_size_pct = self.risk_config['max_position_size_pct']
        self.max_correlation_exposure = self.risk_config['max_correlation_exposure']
        self.correlation_period = self.risk_config['correlation_period']
        self.high_correlation_threshold = self.risk_config['high_correlation_threshold']

    async def initialize(self):
        """Initialize position manager and sync positions"""
        try:
            await self.sync_positions()
            self.logger.info("Position manager initialized successfully")
        except Exception as e:
            await self.notification_manager.send_error_notification(
                f"Failed to initialize position manager: {str(e)}"
            )
            raise

    async def sync_positions(self):
        """Synchronize positions with exchange data"""
        try:
            positions = await self.exchange.fetch_positions()
            
            # Debug log to see the actual structure
            
            self.logger.debug(f"Position data format: {positions[0] if positions else 'No positions'}")
            
            current_positions = {}
            
            for position in positions:
                # For KuCoin spot trading
                if 'currency' in position and 'balance' in position:
                    symbol = position['currency']
                    size = float(position['balance'])
                    entry_price = 0  # Spot doesn't have entry price concept
                    
                # For KuCoin futures trading
                elif 'symbol' in position and 'currentQty' in position:
                    symbol = position['symbol']
                    size = float(position['currentQty'])
                    entry_price = float(position.get('avgEntryPrice', 0))
                    
                else:
                    # Log the unexpected position format for debugging
                    self.logger.warning(f"Unexpected position format: {position}")
                    continue
                    
                if size != 0:  # Only track non-zero positions
                    current_positions[symbol] = {
                        'symbol': symbol,
                        'size': size,
                        'entry_price': entry_price,
                        'current_price': await self.exchange.get_current_price(symbol),
                        'last_update': datetime.now()
                    }
            
            self.positions = current_positions
            self.logger.debug(f"Synced {len(self.positions)} active positions")
            
        except Exception as e:
            self.logger.error(f"Position sync failed: {e}")

    async def can_open_position(self, symbol: str, size: float, price: float) -> bool:
        """Check if a new position can be opened based on risk parameters."""
        try:
            # Get account balance
            balance = await self.exchange.get_balance()  # Fetch balance from the API
            equity = float(balance.get('USDT', 0))  # Ensure 'USDT' is the key for the base currency

            # Check if equity is zero
            if equity == 0:
                self.logger.error(f"Cannot open position: Account equity is zero for symbol {symbol}.")
                return False

            # Calculate position size as a percentage of equity
            position_value = ensure_decimal(size) * ensure_decimal(price)
            position_size_pct = position_value / equity

            # Check if the position size exceeds the allowed maximum
            if position_size_pct > self.max_position_size_pct:
                self.logger.warning(
                    f"Position size {position_size_pct:.2%} exceeds maximum allowed size "
                    f"{self.max_position_size_pct:.2%} for symbol {symbol}."
                )
                return False

            # Check correlation risk
            if not await self.check_correlation_risk(symbol):
                return False

            # Check margin requirements
            required_margin = await self.calculate_required_margin(symbol, size, price)
            available_margin = float(balance.get('USDT', 0))  # Ensure 'USDT' is the key for free margin

            if required_margin > available_margin:
                self.logger.warning(
                    f"Insufficient margin for symbol {symbol}: Required={required_margin}, Available={available_margin}."
                )
                return False

            return True

        except Exception as e:
            self.logger.error(f"Error in can_open_position for symbol {symbol}: {str(e)}")
            return False


    async def calculate_required_margin(self, symbol: str, size: float, price: float) -> float:
        """Calculate required margin for position"""
        try:
            market = await self.exchange.fetch_market(symbol)
            
            # Get leverage from configuration instead of market data
            is_futures = symbol.startswith("FUTURES:") or self.config["trading"]["mode"] == "futures"
            if is_futures:
                leverage = self.config["trading"]["futures"]["leverage"]
            else:
                leverage = 1  # For spot trading
                
            maintenance_margin_rate = market.get('maintenance_margin_rate', 0.005)  # Default if not provided
            
            position_value = size * price
            initial_margin = position_value / ensure_decimal(leverage)
            maintenance_margin = position_value * ensure_decimal(maintenance_margin_rate)
            
            return max(initial_margin, maintenance_margin) * 1.1  # 10% buffer
        except Exception as e:
            self.logger.error(f"Error calculating margin: {str(e)}")
            return float('inf')


    async def check_correlation_risk(self, new_symbol: str) -> bool:
        """Check if new position would exceed correlation risk limits"""
        try:
            # Update correlation cache if expired
            current_time = time.time()
            if current_time - self.cache_timestamp > self.cache_duration:
                await self.update_correlation_cache()
            
            # Count highly correlated active positions
            correlated_exposure = 0
            for symbol in self.positions:
                if symbol == new_symbol:
                    continue
                    
                correlation = self.get_correlation(new_symbol, symbol)
                if correlation > self.high_correlation_threshold:
                    correlated_exposure += 1
            
            if correlated_exposure >= self.max_correlation_exposure:
                self.logger.warning(f"Correlation exposure limit reached: {correlated_exposure}")
                return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error in correlation check: {str(e)}")
            return False

    async def update_correlation_cache(self):
        """Update correlation cache with recent price data"""
        try:
            symbols = list(self.positions.keys())
            
            # Fetch OHLCV data for correlation calculation
            price_data = {}
            for symbol in symbols:
                ohlcv = await self.exchange.fetch_ohlcv(
                    symbol,
                    timeframe='1h',
                    limit=self.correlation_period
                )
                price_data[symbol] = [x[4] for x in ohlcv]  # Close prices
            
            # Calculate correlations
            for i, sym1 in enumerate(symbols):
                for sym2 in symbols[i+1:]:
                    if len(price_data[sym1]) == len(price_data[sym2]):
                        correlation = np.corrcoef(price_data[sym1], price_data[sym2])[0,1]
                        self.correlation_cache[(sym1, sym2)] = correlation
                        self.correlation_cache[(sym2, sym1)] = correlation
            
            self.cache_timestamp = time.time()
            
        except Exception as e:
            self.logger.error(f"Error updating correlation cache: {str(e)}")
            
    async def monitor_positions(self):
        """Monitor open positions for risk management"""
        try:
            # First sync to make sure we have latest data
            await self.sync_positions()
            
            for symbol, position in self.positions.items():
                # Update current price
                current_price = await self.exchange.get_current_price(symbol)
                if current_price <= 0:
                    continue  # Skip positions without valid prices
                    
                position['current_price'] = current_price
                    
                # Calculate unrealized PnL
                entry_price = position.get('entry_price', 0)
                size = position.get('size', 0)
                
                if entry_price > 0 and size != 0:
                    # Calculate PnL - different for long vs short positions
                    if size > 0:  # Long position
                        pnl_pct = ((ensure_decimal(current_price) - ensure_decimal(entry_price)) / ensure_decimal(entry_price)) * 100
                    else:  # Short position
                        pnl_pct = ((ensure_decimal(entry_price) - ensure_decimal(current_price)) / ensure_decimal(entry_price)) * 100
                    position['unrealized_pnl_pct'] = pnl_pct
                    
                    # Log significant PnL changes
                    if abs(pnl_pct) > 5:  # 5% threshold
                        self.logger.info(f"{symbol} position {pnl_pct:.2f}% {'profit' if pnl_pct > 0 else 'loss'}")
                        
                        # Check for stop loss updates if price has moved significantly
                        if abs(pnl_pct) > 3:  # 3% move
                            await self.update_stop_loss(symbol)
                
                if 'liquidation_price' in position and current_price > 0:
                    liq_price = position['liquidation_price']
                    proximity = abs(liq_price - current_price) / current_price
                    
                    if proximity < 0.05:  # Within 5% of liquidation
                        await self.notification_manager.send_warning_notification(
                            f"WARNING: {symbol} position near liquidation! Price: {current_price}, Liquidation: {liq_price}"
                        )
                
        except Exception as e:
            self.logger.error(f"Position monitoring error: {str(e)}")
            import traceback
            self.logger.error(traceback.format_exc())


    def get_correlation(self, symbol1: str, symbol2: str) -> float:
        """Get correlation between two symbols from cache"""
        return self.correlation_cache.get((symbol1, symbol2), 0.0)

    async def update_position(self, symbol: str, trade_update: dict):
        """Update position details after trade execution"""
        async with self.position_locks[symbol]:
            try:
                if symbol not in self.positions:
                    self.positions[symbol] = {
                        'symbol': symbol,
                        'size': 0,
                        'entry_price': 0,
                        'current_price': 0,
                        'last_update': datetime.now()
                    }
                
                position = self.positions[symbol]
                is_futures = symbol.startswith("FUTURES:") or self.config["trading"]["mode"] == "futures"
                
                # Update position details - safely handle potentially missing keys
                position['size'] = float(trade_update.get('size', 0))
                position['entry_price'] = float(trade_update.get('entry_price', 0))
                position['current_price'] = float(trade_update.get('current_price', 0))
                
                # Safely handle futures-specific fields
                if 'liquidation_price' in trade_update:
                    position['liquidation_price'] = float(trade_update['liquidation_price'])
                if 'margin' in trade_update:
                    position['margin'] = float(trade_update['margin'])
                if 'leverage' in trade_update:
                    position['leverage'] = float(trade_update['leverage'])
                if is_futures and 'leverage' in trade_update:
                    leverage = Decimal(str(trade_update['leverage']))
                    entry_price = Decimal(str(trade_update['entry_price']))
                    size = Decimal(str(trade_update['size']))
                    direction = 'long' if size > 0 else 'short'
                    
                    liquidation_price = calculate_kucoin_liquidation_price(
                        entry_price=entry_price,
                        leverage=leverage,
                        direction=direction,
                        position_size=abs(size)
                    )
                    
                    position['liquidation_price'] = liquidation_price
    
                position['last_update'] = datetime.now()
                
                # Check for stop loss
                await self.update_stop_loss(symbol)
                
            except Exception as e:
                self.logger.error(f"Error updating position {symbol}: {str(e)}")
                import traceback
                self.logger.error(traceback.format_exc())
                
                await self.notification_manager.send_error_notification(
                    f"Error updating position {symbol}: {str(e)}"
                )


    async def update_stop_loss(self, symbol: str):
        """Update stop loss orders for position"""
        try:
            position = self.positions.get(symbol)
            if not position or position['size'] == 0:
                return
            
            # Calculate dynamic stop loss based on volatility
            atr = await self.calculate_atr(symbol)
            stop_distance = atr * 2  # 2x ATR for stop loss
            
            entry_price = position['entry_price']
            current_price = position['current_price']
            
            if position['size'] > 0:  # Long position
                stop_price = current_price - stop_distance
            else:  # Short position
                stop_price = current_price + stop_distance
            
            # Place or update stop loss order
            await self.exchange.create_order(
                symbol=symbol,
                type='stop_loss',
                side='sell' if position['size'] > 0 else 'buy',
                amount=abs(position['size']),
                price=stop_price
            )
            
        except Exception as e:
            self.logger.error(f"Error updating stop loss for {symbol}: {str(e)}")

    async def calculate_atr(self, symbol: str, period: int = 14) -> float:
        """Calculate Average True Range for dynamic stop loss"""
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe='1h', limit=period+1)
            
            high_prices = [x[2] for x in ohlcv]
            low_prices = [x[3] for x in ohlcv]
            close_prices = [x[4] for x in ohlcv]
            
            tr_values = []
            for i in range(1, len(close_prices)):
                hl = ensure_decimal(high_prices[i]) - ensure_decimal(low_prices[i])
                hc = abs(ensure_decimal(high_prices[i]) - ensure_decimal(close_prices[i-1]))
                lc = abs(ensure_decimal(low_prices[i]) - ensure_decimal(close_prices[i-1]))
                tr = max(hl, hc, lc)
                tr_values.append(tr)
            
            return sum(tr_values) / len(tr_values)
            
        except Exception as e:
            self.logger.error(f"Error calculating ATR: {str(e)}")
            return 0

    async def cleanup_positions(self):
        """Clean up closed positions and update state"""
        try:
            await self.sync_positions()
            
            # Remove closed positions
            closed_positions = []
            for symbol in list(self.positions.keys()):
                if symbol not in self.positions or self.positions[symbol]['size'] == 0:
                    closed_positions.append(symbol)
                    
            for symbol in closed_positions:
                del self.positions[symbol]
                
        except Exception as e:
            self.logger.error(f"Error in position cleanup: {str(e)}")
