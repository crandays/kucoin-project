from decimal import Decimal
import time
import asyncio
from strategies.base import Strategy
from utilities.numeric_utils import ensure_decimal

class OrderExecutor:
    def __init__(self, api_client, config, logger):
        self.api = api_client
        self.config = config
        self.logger = logger
        self.order_counter = 0
        self.last_orders = {}  # Track recent orders to prevent excessive trading

        self.strategy_priorities = {
            "spot": {
                "arbitrage": 1,
                "breakout_trading": 2,
                "momentum": 3,
                "ichimoku": 3,  # Same priority as momentum in spot markets
                "trend_following": 4,
                "mean_reversion": 5,
                "scalping": 6  # Lower priority in spot markets
            },
            "futures": {
                "arbitrage": 1,
                "scalping": 2,  # Higher priority in futures markets
                "momentum": 2,  # Higher priority in futures due to leverage
                "ichimoku": 3,  # Medium priority in futures
                "breakout_trading": 3,
                "trend_following": 4,
                "mean_reversion": 5
            }
        }
        
    async def execute_strategy_order(self, strategy: Strategy, order_params: dict):
        """Execute order with market validation"""
        symbol = order_params["symbol"]
        market_type = "SPOT" if symbol.startswith("SPOT:") else "FUTURES"
        
        if not strategy.can_trade_symbol(symbol):
            self.logger.warning(
                f"Strategy '{strategy.name}' attempted to trade {market_type} "
                f"symbol {symbol} but {market_type} trading is disabled"
            )
            return None
            
        try:
            return await self.execute_order(
                order_params["type"],
                order_params,
                strategy_name=strategy.name
            )
        except Exception as e:
            self.logger.error(
                f"Order execution failed for strategy '{strategy.name}' "
                f"({market_type}): {e}"
            )
            return None

    async def execute_order(self, order_type, params, strategy_name=None):
        """Execute order of specified type with given parameters"""
        # Check if we've made too many orders for this symbol recently
        symbol = params.get("symbol")
        current_time = time.time()
        
        if symbol in self.last_orders:
            last_time = self.last_orders[symbol]
            cooldown = self.config.get("trading_engine", {}).get("order_cooldown", 300)
            market_type = "spot" if symbol.startswith("SPOT:") else "futures"
            strategy_priority = self.strategy_priorities.get(market_type, {}).get(strategy_name, 999)
            
            
            # Allow high priority strategies to bypass cooldown
            if strategy_priority <= 2:  # High priority (1-2) can bypass cooldown
                pass  # Allow execution
            else:
                time_diff = current_time - last_time
                if time_diff < cooldown:
                    self.logger.info(f"Order for {symbol} skipped - cooldown period active...")
                    return None
        
        try:
            # Update last order time for this symbol
            self.last_orders[symbol] = current_time
            
            if order_type == "trailing_stop":
                return await self._execute_trailing_stop(params)
            elif order_type == "oco":
                return await self._execute_oco_order(params)
            elif order_type == "iceberg":
                return await self._execute_iceberg_order(params)
            elif order_type == "market":
                return await self._execute_market_order(params)
            elif order_type == "limit":
                return await self._execute_limit_order(params)
            else:
                self.logger.error(f"Unsupported order type: {order_type}")
                raise ValueError(f"Unsupported order type: {order_type}")
                
        except Exception as e:
            self.logger.error(f"Error executing {order_type} order for {symbol}: {str(e)}", exc_info=True)
            return None

    async def _execute_trailing_stop(self, params):
        """Execute trailing stop order"""
        config = self.config["order_types"]["trailing_stop"]
        
        # Get current price if needed for activation price
        current_price = await self._get_current_price(params["symbol"])
        if not current_price:
            self.logger.error(f"Failed to get price for {params['symbol']}, can't execute trailing stop")
            return None
            
        # Calculate activation price
        activation_percent = Decimal(config["activation_percent"])
        if params["side"] == "buy":
            activation_price = ensure_decimal(current_price) * (Decimal("1") + activation_percent / Decimal("100"))
        else:
            activation_price = ensure_decimal(current_price) * (Decimal("1") - activation_percent / Decimal("100"))
            
        params.update({
            "callbackRate": str(config["trail_percent"]),
            "activationPrice": str(activation_price)
        })
        
        result = await self.api.create_advanced_order("trailing_stop", params)
        if result:
            self.logger.info(f"Trailing stop order executed for {params['symbol']}")
            self.order_counter += 1
        return result

    async def _execute_oco_order(self, params):
        """Execute OCO (One-Cancels-the-Other) order"""
        config = self.config["order_types"]["oco"]
        spread = Decimal(config["spread_percent"])/100
        
        # Get current price if not provided
        price = Decimal(params.get("price", "0"))
        if price == Decimal("0"):
            price = await self._get_current_price(params["symbol"])
            if not price:
                self.logger.error(f"Failed to get price for {params['symbol']}, can't execute OCO order")
                return None
        
        # Set stop price based on the side
        if params["side"] == "buy":
            params["stopPrice"] = str(ensure_decimal(price) * (Decimal("1") + spread))
        else:
            params["stopPrice"] = str(ensure_decimal(price) * (Decimal("1") - spread))
            
        params["stopLimitPrice"] = params["stopPrice"]
        
        result = await self.api.create_advanced_order("oco", params)
        if result:
            self.logger.info(f"OCO order executed for {params['symbol']}")
            self.order_counter += 1
        return result

    async def _execute_iceberg_order(self, params):
        """Execute iceberg order (partially visible order)"""
        config = self.config["order_types"]["iceberg"]
        params["visibleSize"] = str(ensure_decimal(params["size"]) * Decimal(config["max_visible_size"]))
        
        result = await self.api.create_advanced_order("iceberg", params)
        if result:
            self.logger.info(f"Iceberg order executed for {params['symbol']}")
            self.order_counter += 1
        return result
        
    async def _execute_market_order(self, params):
        """Execute market order"""
        endpoint = "/api/v1/orders"
        params["type"] = "market"
        
        result = await self.api.request("POST", endpoint, params)
        if result:
            self.logger.info(f"Market order executed for {params['symbol']}")
            self.order_counter += 1
        return result
        
    async def _execute_limit_order(self, params):
        """Execute limit order"""
        endpoint = "/api/v1/orders"
        params["type"] = "limit"
        
        # Ensure price is included
        if "price" not in params:
            current_price = await self._get_current_price(params["symbol"])
            if not current_price:
                self.logger.error(f"Failed to get price for {params['symbol']}, can't execute limit order")
                return None
                
            # Set price slightly better than market for better execution
            modifier = Decimal("0.999") if params["side"] == "buy" else Decimal("1.001")
            params["price"] = str(ensure_decimal(current_price) * modifier)
            
        result = await self.api.request("POST", endpoint, params)
        if result:
            self.logger.info(f"Limit order executed for {params['symbol']}")
            self.order_counter += 1
        return result

    async def _get_current_price(self, symbol):
        """Get current price for a symbol"""
        ticker = await self.api.get_ticker(symbol)
        if not ticker or "price" not in ticker:
            return None
        return Decimal(ticker["price"])
