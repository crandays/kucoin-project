import asyncio
import logging
from typing import Dict, Optional
from decimal import Decimal
import time
from collections import defaultdict
from utilities.numeric_utils import ensure_decimal

class TradingEngine:
    def __init__(self, exchange, position_manager, notification_manager, config, logger, strategy_manager=None, risk_manager=None, order_executor=None):
        self.exchange = exchange
        self.position_manager = position_manager
        self.notification_manager = notification_manager
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        self.risk_manager = risk_manager
        self.order_executor = order_executor
        self.strategies = {}
        self.strategy_manager = strategy_manager
        # Trading state
        self.is_running = False
        self.active_orders = {}
        self.order_locks = defaultdict(asyncio.Lock)
        self.last_trade_time = defaultdict(float)
        
        self.tasks = []
        
        # Cache for market data
        self.market_cache = {}
        self.cache_timestamp = 0
        self.cache_duration = 60  # 1 minute cache
        
        # Rate limiting
        self.order_cooldown = 150  # seconds between orders
        self.rate_limit_counter = defaultdict(int)
        self.rate_limit_last_reset = time.time()
        
        # Performance tracking
        self.trade_history = []
        self.performance_metrics = {
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'total_pnl': 0
        }

    async def start(self):
        """Start the trading engine"""
        try:
            self.is_running = True
            await self.position_manager.initialize()
            await self.notification_manager.send_notification("Trading engine started")
            
            # Start background tasks
            self.tasks = [
                asyncio.create_task(self.monitor_positions()),
                asyncio.create_task(self.cleanup_orders()),
                asyncio.create_task(self.update_performance_metrics()),
                asyncio.create_task(self.scan_market())  # Add this line
            ]
            
            # Run all tasks
            await asyncio.gather(*self.tasks)
            
        except Exception as e:
            await self.notification_manager.send_error_notification(
                f"Failed to start trading engine: {str(e)}"
            )
            raise

    async def stop(self):
        """Stop the trading engine"""
        self.is_running = False
        for task in self.tasks:
            task.cancel()
        await self.notification_manager.send_notification("Trading engine stopped")
        
    def _initialize_strategy(self, strategy_name, strategy_config):
        """Initialize a trading strategy based on its name and configuration"""
        try:
            # Import the appropriate strategy class based on strategy name
            if strategy_name == "trend_following":
                from strategies.trend_following import TrendFollowingStrategy
                strategy_class = TrendFollowingStrategy
            elif strategy_name == "mean_reversion":
                from strategies.mean_reversion import MeanReversionStrategy
                strategy_class = MeanReversionStrategy
            elif strategy_name == "breakout_trading":
                from strategies.breakout_trading import BreakoutTradingStrategy
                strategy_class = BreakoutTradingStrategy
            elif strategy_name == "scalping":
                from strategies.scalping import ScalpingStrategy
                strategy_class = ScalpingStrategy
            elif strategy_name == "arbitrage":
                from strategies.arbitrage import ArbitrageStrategy
                strategy_class = ArbitrageStrategy
            elif strategy_name == "momentum_strategy":
                from strategies.momentum_strategy import MomentumStrategy
                strategy_class = MomentumStrategy
            elif strategy_name == "ichimoku":
                from strategies.ichimoku_strategy import IchimokuStrategy
                strategy_class = IchimokuStrategy
                
            else:
                raise ValueError(f"Unknown strategy: {strategy_name}")

            # Create a strategy-specific config that includes the enabled modes
            strategy_specific_config = self.config.copy()
            strategy_specific_config["strategy_params"] = strategy_config
            
            # Initialize the strategy with the exchange, enhanced config, and logger
            strategy = strategy_class(self.exchange, strategy_specific_config, self.logger)
            
            # Store the market compatibility information for later use
            strategy.market_compatibility = {
                "spot": strategy_config.get("enabled_spot", False),
                "futures": strategy_config.get("enabled_futures", False)
            }
            
            return strategy
            
        except Exception as e:
            self.logger.error(f"Error initializing strategy {strategy_name}: {e}")
            raise
        
    def _validate_strategy_markets(self, strategy_name: str, strategy_config: dict) -> tuple:
        """Validate strategy markets against global trading mode"""
        global_mode = self.config["trading"]["mode"]
        enabled_spot = strategy_config["enabled_spot"]
        enabled_futures = strategy_config["enabled_futures"]
        
        if global_mode == "spot":
            enabled_futures = False
        elif global_mode == "futures":
            enabled_spot = False
            
        if not (enabled_spot or enabled_futures):
            self.logger.warning(
                f"Strategy '{strategy_name}' has no enabled markets "
                f"compatible with global mode '{global_mode}'"
            )
            return False, False
            
        return enabled_spot, enabled_futures

    async def load_strategies(self):
        """Load strategies with market validation"""
        for strategy_name, strategy_config in self.config["strategies"].items():
            if not strategy_config["enabled"]:
                continue
                
            enabled_spot, enabled_futures = self._validate_strategy_markets(
                strategy_name, strategy_config
            )
            
            if not (enabled_spot or enabled_futures):
                continue
                
            # Update config with validated markets
            strategy_config["enabled_spot"] = enabled_spot
            strategy_config["enabled_futures"] = enabled_futures
            
            try:
                strategy = self._initialize_strategy(strategy_name, strategy_config)
                self.strategies[strategy_name] = strategy
                self.logger.info(
                    f"Loaded strategy '{strategy_name}' for "
                    f"{strategy.get_enabled_markets()} trading"
                )
            except Exception as e:
                self.logger.error(f"Failed to load strategy '{strategy_name}': {e}")

    async def update_strategy_markets(self, strategy_name: str, 
                                    enable_spot: bool, enable_futures: bool):
        """Dynamically update strategy markets"""
        if strategy_name not in self.strategies:
            raise ValueError(f"Strategy '{strategy_name}' not found")
            
        strategy = self.strategies[strategy_name]
        strategy.enabled_spot = enable_spot
        strategy.enabled_futures = enable_futures
        
        self.logger.info(
            f"Updated strategy '{strategy_name}' markets: "
            f"{strategy.get_enabled_markets()}"
        )

    async def execute_trade(self, symbol: str, side: str, size: float, price: float, 
                          order_type: str = 'limit', stop_loss: float = None, 
                          take_profit: float = None, trailing_stop: float = None):
        """Execute trade with advanced order types"""
        async with self.order_locks[symbol]:
            try:
                # Check rate limits
                if not self.check_rate_limits(symbol):
                    return None
                
                # Set leverage for futures trading
                if symbol.startswith("FUTURES:") or self.config["trading"]["mode"] == "futures":
                    # Set leverage before placing the order
                    leverage = self.config["trading"]["futures"]["leverage"]
                    await self.exchange.set_leverage(symbol, leverage)
                
                # Verify position can be opened
                if not await self.position_manager.can_open_position(symbol, size, price):
                    return None
                
                # Place main order
                order_params = {
                    'symbol': symbol,
                    'type': order_type,
                    'side': side,
                    'amount': size,
                    'price': price
                }
                
                if order_type == 'market':
                    del order_params['price']
                
                main_order = await self.exchange.create_order(**order_params)
                self.active_orders[main_order['id']] = main_order
                
                # Place stop loss and take profit (OCO order)
                if stop_loss and take_profit:
                    oco_order = await self.exchange.create_oco_order(
                        symbol=symbol,
                        side='sell' if side == 'buy' else 'buy',
                        amount=size,
                        stop_price=stop_loss,
                        price=take_profit
                    )
                    self.active_orders[oco_order['id']] = oco_order
                
                # Place trailing stop if specified
                elif trailing_stop:
                    trail_params = {
                        'symbol': symbol,
                        'side': 'sell' if side == 'buy' else 'buy',
                        'amount': size,
                        'trailing_delta': trailing_stop
                    }
                    trail_order = await self.exchange.create_trailing_stop_order(**trail_params)
                    self.active_orders[trail_order['id']] = trail_order
                
                # Update position manager
                await self.position_manager.update_position(symbol, main_order)
                
                # Record trade
                self.record_trade(main_order)
                
                # Update last trade time
                self.last_trade_time[symbol] = time.time()
                
                return main_order
                
            except Exception as e:
                await self.notification_manager.send_error_notification(
                    f"Trade execution failed for {symbol}: {str(e)}"
                )
                return None

    def check_rate_limits(self, symbol: str) -> bool:
        """Check and enforce rate limits"""
        current_time = time.time()
        
        # Reset rate limits if needed
        if current_time - self.rate_limit_last_reset > 3600:
            self.rate_limit_counter.clear()
            self.rate_limit_last_reset = current_time
        
        # Check cooldown period
        if current_time - self.last_trade_time[symbol] < self.order_cooldown:
            return False
        
        # Check hourly limit (default to 5 if not specified)
        max_hourly_trades = self.config.get('trading_engine', {}).get('max_hourly_trades', 5)
        if self.rate_limit_counter[symbol] >= max_hourly_trades:
            return False
        
        self.rate_limit_counter[symbol] += 1
        return True  # Explicitly return True
        
    async def scan_market(self):
        self.logger.info("Starting market scanner...")
        await self.load_strategies()

        # First check account balances
        balances = await self.exchange.get_account_balances()
        spot_balance = balances["spot"]
        futures_balance = balances["futures"]
        
        # Log balances
        self.logger.info(f"Current account balances: Spot USDT: {spot_balance}, Futures USDT: {futures_balance}")
        await self.notification_manager.send_notification(
            f"💰 Account balances:\nSpot: {spot_balance} USDT\nFutures: {futures_balance} USDT"
        )
        
        # Determine which markets to trade based on balances
        trade_spot = spot_balance >= Decimal("10")
        trade_futures = futures_balance >= Decimal("10")
        
        if not trade_spot and not trade_futures:
            self.logger.warning("Insufficient balance in both spot and futures accounts (< 10 USDT). Exiting.")
            await self.notification_manager.send_notification(
                "⚠️ Trading stopped: Insufficient balance in both accounts (< 10 USDT)"
            )
            return
        
        if not trade_spot:
            self.logger.warning("Insufficient spot balance (< 10 USDT). Skipping spot trading.")
            await self.notification_manager.send_notification(
                "ℹ️ Spot trading disabled: Insufficient balance (< 10 USDT)"
            )
        
        if not trade_futures:
            self.logger.warning("Insufficient futures balance (< 10 USDT). Skipping futures trading.")
            await self.notification_manager.send_notification(
                "ℹ️ Futures trading disabled: Insufficient balance (< 10 USDT)"
            )

        while self.is_running:
            try:
                self.logger.info("Scanning market for opportunities...")
                pairs = await self.exchange.get_liquid_pairs(1000)
                if not pairs:
                    self.logger.warning("No liquid pairs found")
                    await asyncio.sleep(60)
                    continue

                self.logger.info(f"Analyzing {len(pairs)} liquid pairs")
                
                # Call analyze_markets to use the StrategyManager
                if self.strategy_manager:
                    await self.analyze_markets()

                for name, strategy in self.strategies.items():
                    # Skip spot strategies if spot balance is low
                    if not trade_spot and strategy.enabled_spot and self.config["trading"]["mode"] == "spot":
                        continue
                    # Skip futures strategies if futures balance is low
                    if not trade_futures and strategy.enabled_futures and self.config["trading"]["mode"] == "futures":
                        continue
                    
                    # Skip if strategy isn't enabled for the current trading mode
                    if not strategy.enabled_spot and self.config["trading"]["mode"] == "spot":
                        continue
                    if not strategy.enabled_futures and self.config["trading"]["mode"] == "futures":
                        continue

                    self.logger.info(f"Executing {name} strategy")
                    signals = await strategy.execute()

                    for signal in signals:
                        await self.process_signal(signal)

                interval = self.config["trading_engine"].get("scan_interval", 300)
                self.logger.info(f"Market scan complete, waiting {interval} seconds")
                await asyncio.sleep(interval)

            except Exception as e:
                self.logger.error(f"Error in market scanner: {str(e)}")
                await asyncio.sleep(60)

    async def analyze_markets(self):
        """Analyze markets and generate trading signals"""
        try:
            # Get market data
            market_data = await self.exchange.get_market_data()
            
            # Generate signals using strategy_manager if available
            signals = []
            if self.strategy_manager:
                signals = await self.strategy_manager.generate_signals(market_data)
                self.logger.info(f"Generated {len(signals)} signals from strategy manager")
            
            # Process signals
            for signal in signals:
                await self.process_signal(signal)
                
        except Exception as e:
            self.logger.error(f"Error analyzing markets: {e}")
            
    async def process_signal(self, signal):
        # validation
        if not self.risk_manager or not self.order_executor:
            self.logger.error("Cannot process signal: risk_manager or order_executor not initialized")
            return
        try:
            # Extract signal details
            symbol = signal.get("symbol")
            action = signal.get("action")
            price = signal.get("price")
            size = signal.get("size")
            order_type = signal.get("order_type", "limit")
            reason = signal.get("reason", "")
            strategy = signal.get("strategy", "unknown")
            
            self.logger.info(f"Processing signal: {action} {symbol} at {price} from {strategy} strategy")
            
            # Check if the trade passes risk management
            if not await self.risk_manager.validate_trade(symbol, action, price, size):
                self.logger.warning(f"Signal rejected by risk manager: {action} {symbol}")
                return
                
            # Execute the order
            order_result = await self.order_executor.execute_order(
                symbol=symbol,
                side=action,
                price=price,
                size=size,
                order_type=order_type
            )
            
            # Update position manager
            if order_result and order_result.get("orderId"):
                await self.position_manager.update_position(order_result)
                
                # Send notification
                await self.notification_manager.send_notification(
                    f"🔔 {action.upper()} {symbol} at {price}\n"
                    f"Size: {size}\n"
                    f"Strategy: {strategy}\n"
                    f"Reason: {reason}"
                )
                
        except Exception as e:
            self.logger.error(f"Error processing signal: {e}")

    async def monitor_positions(self):
        """Monitor open positions and manage risk"""
        self.logger.info("Starting position monitoring...")
        while self.is_running:
            try:
                await self.position_manager.sync_positions()
                
                for symbol, position in self.position_manager.positions.items():
                    # Check for liquidation risk
                    if self.check_liquidation_risk(position):
                        await self.close_position(symbol)
                        continue
                    
                    # Update trailing stops
                    await self.update_trailing_stops(symbol, position)
                    
                await asyncio.sleep(10)
                
            except Exception as e:
                self.logger.error(f"Position monitoring error: {str(e)}")
                await asyncio.sleep(30)

    def check_liquidation_risk(self, position: dict) -> bool:
        """Check if position is at risk of liquidation"""
        # Skip liquidation check for spot positions
        if 'liquidation_price' not in position:
            return False
            
        current_price = position['current_price']
        liquidation_price = position['liquidation_price']
        buffer = 0.05  # 5% buffer
        
        if position['size'] > 0:  # Long position
            return ensure_decimal(current_price) < ensure_decimal(liquidation_price) * (Decimal("1") + Decimal(str(buffer)))
        else:  # Short position
            return ensure_decimal(current_price) > ensure_decimal(liquidation_price) * (Decimal("1") - Decimal(str(buffer)))

    async def update_trailing_stops(self, symbol: str, position: dict):
        """Update trailing stop orders"""
        try:
            for order_id, order in self.active_orders.items():
                if order['symbol'] == symbol and order['type'] == 'trailing_stop':
                    # Calculate new stop price
                    current_price = position['current_price']
                    trail_delta = order['trailing_delta']
                    
                    if position['size'] > 0:  # Long position
                        new_stop = ensure_decimal(current_price) - ensure_decimal(trail_delta)
                    else:  # Short position
                        new_stop = ensure_decimal(current_price) + ensure_decimal(trail_delta)
                    
                    # Update stop price if needed
                    if abs(ensure_decimal(new_stop) - ensure_decimal(order['stop_price'])) > ensure_decimal(trail_delta) * Decimal("0.1"):
                        await self.exchange.edit_order(
                            id=order_id,
                            symbol=symbol,
                            stop_price=new_stop
                        )
                        
        except Exception as e:
            self.logger.error(f"Error updating trailing stops: {str(e)}")

    async def cleanup_orders(self):
        """Clean up completed or cancelled orders"""
        self.logger.info("Starting order cleanup process...")
        while self.is_running:
            try:
                for order_id in list(self.active_orders.keys()):
                    order = await self.exchange.fetch_order(order_id)
                    if order['status'] in ['filled', 'cancelled']:
                        del self.active_orders[order_id]
                
                await asyncio.sleep(60)
                
            except Exception as e:
                self.logger.error(f"Order cleanup error: {str(e)}")
                await asyncio.sleep(30)

    def record_trade(self, order: dict):
        """Record trade for performance tracking"""
        self.trade_history.append({
            'timestamp': time.time(),
            'symbol': order['symbol'],
            'side': order['side'],
            'size': order['amount'],
            'price': order['price'],
            'type': order['type']
        })
        self.performance_metrics['total_trades'] += 1

    async def update_performance_metrics(self):
        """Update trading performance metrics"""
        self.logger.info("Starting performance metrics updates...")
        while self.is_running:
            try:
                total_pnl = 0
                winning_trades = 0
                losing_trades = 0
                
                for trade in self.trade_history:
                    if 'pnl' in trade:
                        total_pnl += trade['pnl']
                        if trade['pnl'] > 0:
                            winning_trades += 1
                        else:
                            losing_trades += 1
                
                self.performance_metrics.update({
                    'winning_trades': winning_trades,
                    'losing_trades': losing_trades,
                    'total_pnl': total_pnl
                })
                
                await asyncio.sleep(300)  # Update every 5 minutes
                
            except Exception as e:
                self.logger.error(f"Performance update error: {str(e)}")
                await asyncio.sleep(60)
