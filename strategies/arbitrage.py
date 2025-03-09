from decimal import Decimal
from typing import List, Dict
from .base import Strategy
from utilities.numeric_utils import ensure_decimal

class ArbitrageStrategy(Strategy):
    def __init__(self, config: dict, name: str = "arbitrage"):
        super().__init__(config["strategies"]["arbitrage"], name)
        self.api_client = None
        self.min_profit = Decimal(self.config.get("parameters", {}).get("min_profit_percentage", "0.5"))

    def set_api_client(self, api_client):
        """Set the API client after initialization"""
        self.api_client = api_client

    async def _generate_raw_signals(self, market_data: dict) -> List[dict]:
        """
        Arbitrage strategy: detects price gaps between different markets
        
        Args:
            market_data: Dictionary containing market data
            
        Returns:
            List of trading signals if opportunities found
        """
        try:
            signals = []
            
            # Use market_data if available, otherwise fetch from API
            tickers = market_data.get("tickers", None)
            
            if not tickers and self.api_client:
                tickers_response = await self.api_client.request("GET", "/api/v1/market/allTickers")
                if tickers_response and "data" in tickers_response:
                    tickers = tickers_response["data"]
            
            if not tickers or "ticker" not in tickers:
                self.logger.error("Failed to get tickers for arbitrage strategy")
                return signals

            opportunities = []
            for ticker in tickers["ticker"]:
                symbol = ticker["symbol"]
                bid_price = ensure_decimal(ticker.get("buy", "0"))
                ask_price = ensure_decimal(ticker.get("sell", "0"))
                
                # Basic check for arbitrage opportunity in the same market
                if bid_price > ask_price:
                    spread = ((ensure_decimal(bid_price) / ensure_decimal(ask_price)) - 1) * 100  # percentage
                    opportunities.append((symbol, spread, bid_price, ask_price))

            # Sort by profit potential
            opportunities.sort(key=lambda x: x[1], reverse=True)
            
            # Generate signals for viable opportunities
            for opportunity in opportunities[:3]:  # Take top 3
                symbol, spread, bid_price, ask_price = opportunity
                
                if spread > self.min_profit:
                    self.logger.info(f"Arbitrage opportunity on {symbol} with {spread:.2f}% spread")
                    
                    # Calculate size based on available balance and risk limits
                    buy_size = await self._calculate_position_size(symbol, ask_price)
                    sell_size = await self._calculate_position_size(symbol, bid_price)
                    
                    # Create buy signal at ask price
                    signals.append({
                        "symbol": symbol,
                        "action": "buy",
                        "price": str(ask_price),
                        "size": str(buy_size),
                        "order_type": "limit",
                        "reason": f"Arbitrage: {spread:.2f}% spread",
                        "risk_score": 0.3  # Arbitrage is relatively lower risk
                    })
                    
                    # Create sell signal at bid price
                    signals.append({
                        "symbol": symbol,
                        "action": "sell",
                        "price": str(bid_price),
                        "size": str(sell_size),
                        "order_type": "limit",
                        "reason": f"Arbitrage: {spread:.2f}% spread",
                        "risk_score": 0.3
                    })

            return signals
            
        except Exception as e:
            self.logger.error(f"Error in Arbitrage strategy: {e}")
            return []
            
    async def _calculate_position_size(self, symbol, price):
        """Calculate appropriate position size based on risk parameters"""
        try:
            from utilities.trade_sizing import calculate_trade_size
            
            # Convert price to Decimal if it's not already
            if not isinstance(price, Decimal):
                price = Decimal(str(price))
            
            # Use the utility function to calculate the size if api_client is available
            if self.api_client:
                size = await calculate_trade_size(
                    self.api_client,
                    symbol,
                    price,
                    self.config
                )
            else:
                # Fallback to a default calculation if api_client not available
                size = Decimal("0.01")  # Minimal default size
                self.logger.warning(f"API client not available, using default position size for {symbol}")
            
            return size
        except Exception as e:
            self.logger.error(f"Error calculating position size: {e}")
            return Decimal("0.01")  # Default minimal size on error

            
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
