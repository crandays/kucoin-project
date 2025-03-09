from decimal import Decimal
from typing import List, Dict
from .base import Strategy
from utilities.numeric_utils import ensure_decimal

class ScalpingStrategy(Strategy):
    def __init__(self, config: dict, name: str = "scalping"):
        super().__init__(config["strategies"]["scalping"], name)
        self.api_client = None
        self.spread_threshold = Decimal(self.config.get("parameters", {}).get("spread_threshold", "0.2"))
        self.order_book_depth = self.config.get("parameters", {}).get("order_book_depth", 20)
        self.min_volume = Decimal(self.config.get("parameters", {}).get("min_volume", "10000"))

    def set_api_client(self, api_client):
        """Set the API client after initialization"""
        self.api_client = api_client

    async def _generate_raw_signals(self, market_data: dict) -> List[dict]:
        """
        Scalping strategy: exploits small price gaps between bid and ask prices
        
        Args:
            market_data: Dictionary containing market data
            
        Returns:
            List of trading signals
        """
        try:
            signals = []
            
            # Use market_data if available, otherwise fetch from API
            pairs = market_data.get("pairs", [])
            order_books = market_data.get("order_books", {})
            
            if not pairs and self.api_client:
                pairs = await self.api_client.get_liquid_pairs(self.min_volume)
            
            for pair in pairs[:15]:  # Limit to 15 pairs for efficiency with order book requests
                # Get order book data
                order_book = None
                if pair in order_books:
                    order_book = order_books[pair]
                elif self.api_client:
                    order_book_response = await self.api_client.request(
                        "GET", 
                        f"/api/v1/market/orderbook/level2_{self.order_book_depth}?symbol={pair}"
                    )
                    if order_book_response and "data" in order_book_response:
                        order_book = order_book_response["data"]
                
                if not order_book:
                    continue
                    
                bids = order_book.get("bids", [])
                asks = order_book.get("asks", [])
                
                if not bids or not asks:
                    continue
                    
                # Get best bid and ask
                best_bid = ensure_decimal(bids[0][0])
                best_ask = ensure_decimal(asks[0][0])
                
                # Calculate spread as percentage
                spread_pct = ((ensure_decimal(best_ask) / ensure_decimal(best_bid)) - 1) * 100
                
                # Calculate bid/ask volumes
                bid_volume = sum(ensure_decimal(bid[1]) for bid in bids[:3])  # Top 3 bid levels
                ask_volume = sum(ensure_decimal(ask[1]) for ask in asks[:3])  # Top 3 ask levels
                
                # Volume imbalance indicates potential price move
                volume_ratio = ensure_decimal(bid_volume) / ensure_decimal(ask_volume) if ask_volume > 0 else Decimal("999")
                
                # Signals based on spread and volume imbalance
                if spread_pct > self.spread_threshold:
                    # Scalping opportunity detected
                    
                    # If bid volume significantly higher than ask volume, price likely to rise
                    if volume_ratio > 2:
                        self.logger.info(f"Scalping buy opportunity on {pair}: spread {spread_pct:.2f}%, volume ratio {volume_ratio:.2f}")
                        
                        # Calculate size based on current price and risk settings
                        size = await self._calculate_position_size(pair, best_ask)
                        
                        signals.append({
                            "symbol": pair,
                            "action": "buy",
                            "price": str(ensure_decimal(best_ask)),
                            "size": str(size),
                            "order_type": "limit",
                            "reason": f"Scalping: spread {spread_pct:.2f}%, buy volume {volume_ratio:.1f}x sell volume",
                            "risk_score": 0.7  # Scalping has higher risk
                        })
                    
                    # If ask volume significantly higher than bid volume, price likely to fall
                    elif volume_ratio < 0.5:
                        self.logger.info(f"Scalping sell opportunity on {pair}: spread {spread_pct:.2f}%, volume ratio {volume_ratio:.2f}")
                        
                        # Calculate size based on current price and risk settings
                        size = await self._calculate_position_size(pair, best_bid)
                        
                        signals.append({
                            "symbol": pair,
                            "action": "sell",
                            "price": str(ensure_decimal(best_bid)),
                            "size": str(size),
                            "order_type": "limit",
                            "reason": f"Scalping: spread {spread_pct:.2f}%, sell volume {1/volume_ratio:.1f}x buy volume",
                            "risk_score": 0.7
                        })
            
            return signals
            
        except Exception as e:
            self.logger.error(f"Error in Scalping strategy: {e}")
            return []
            
    async def _calculate_position_size(self, symbol, price):
        """Calculate appropriate position size based on risk parameters"""
        try:
            from utilities.trade_sizing import calculate_trade_size
            
            # Convert price to Decimal if it's not already
            if not isinstance(price, Decimal):
                price = Decimal(str(price))
            
            # Use the utility function to calculate the size
            if self.api_client:
                size = await calculate_trade_size(
                    self.api_client,
                    symbol,
                    price,
                    self.config
                )
            else:
                size = Decimal("0.01")  # Default minimal size
                self.logger.warning(f"API client not available, using default position size for {symbol}")
            
            return size
        except Exception as e:
            self.logger.error(f"Error calculating position size: {e}")
            return Decimal("0.01")  # Default minimal size on error
