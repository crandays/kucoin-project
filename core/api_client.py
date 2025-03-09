import aiohttp
import hashlib
import hmac
import base64
import time
import json
from decimal import Decimal

class KuCoinClient:
    def __init__(self, config, logger):
        self.config = config
        self.api_key = config["api"]["key"]
        self.api_secret = config["api"]["secret"]
        self.api_passphrase = config["api"]["passphrase"]
        self.api_url = config["api"]["url"]
        self.logger = logger
        self.session = aiohttp.ClientSession()  # Initialize session

    async def request(self, method, endpoint, params=None, data=None, auth=True):
        """Make a request to the KuCoin API"""
        url = f"{self.api_url}{endpoint}"
        headers = {"Content-Type": "application/json"}
        method = method.upper()
        
        # For POST requests, merge params into data instead of URL
        if method not in ['GET', 'DELETE']:
            if params:
                data = {**params, **(data or {})}
            params_str = ""
        else:
            # Only append params to URL for GET/DELETE
            params_str = ""
            if params:
                params_str = "?" + "&".join([f"{k}={v}" for k, v in sorted(params.items())])
                url = f"{url}{params_str}"
        
        if auth:
            timestamp = str(int(time.time() * 1000))
            data_json = json.dumps(data) if data else ""
            
            # Generate signature string
            str_to_sign = f"{timestamp}{method}{endpoint}{params_str}{data_json}"
            signature = base64.b64encode(
                hmac.new(self.api_secret.encode('utf-8'), str_to_sign.encode('utf-8'), hashlib.sha256).digest()
            )
            
            # Generate encrypted passphrase
            passphrase = base64.b64encode(
                hmac.new(self.api_secret.encode('utf-8'), self.api_passphrase.encode('utf-8'), hashlib.sha256).digest()
            )
            
            headers.update({
                "KC-API-SIGN": signature.decode('utf-8'),
                "KC-API-TIMESTAMP": timestamp,
                "KC-API-KEY": self.api_key,
                "KC-API-PASSPHRASE": passphrase.decode('utf-8'),
                "KC-API-KEY-VERSION": "3"
            })
        
        try:
            async with self.session.request(method, url, headers=headers, json=data) as response:
                response_data = await response.json()
                if response.status != 200:
                    raise Exception(f"{endpoint}{params_str} - {response_data}")
                return response_data
        except Exception as e:
            self.logger.error(f"API error: {endpoint}{params_str} - {str(e)}")
            raise


    def _generate_signature(self, timestamp, method, endpoint, body=""):
        message = f"{timestamp}{method}{endpoint}{body}"
        hmac_key = self.api_secret.encode("utf-8")  # Use raw secret
        signature = hmac.new(hmac_key, message.encode("utf-8"), hashlib.sha256).digest()
        return base64.b64encode(signature).decode()
        
    def _encrypt_passphrase(self):
        hmac_key = self.api_secret.encode("utf-8")  # Use raw secret
        signature = hmac.new(hmac_key, self.api_passphrase.encode("utf-8"), hashlib.sha256).digest()
        return base64.b64encode(signature).decode()
        
    async def get_account_balances(self):
        """Get both spot and futures account balances for USDT"""
        try:
            # Get spot balance
            spot_response = await self.request("GET", "/api/v1/accounts?currency=USDT")
            spot_balance = Decimal("0")
            if spot_response and "data" in spot_response:
                for account in spot_response["data"]:
                    if account["currency"] == "USDT" and account["type"] == "trade":
                        spot_balance = Decimal(account["available"])
                        break
            
            # Get futures balance
            futures_balance = Decimal("0")
            # Check if futures trading is enabled in config
            if self.config["trading"].get("futures", {}).get("enabled", False):
                futures_api_url = self.config["trading"]["futures"].get("api_url")
                
                # Store original API URL
                original_api_url = self.api_url
                
                try:
                    # Temporarily change API URL to futures API URL
                    self.api_url = futures_api_url
                    
                    # Make request to futures API
                    futures_response = await self.request(
                        "GET", 
                        "/api/v1/account-overview?currency=USDT", 
                        auth=True
                    )
                    
                    if futures_response and "data" in futures_response:
                        futures_balance = Decimal(futures_response["data"].get("availableBalance", "0"))
                finally:
                    # Restore original API URL
                    self.api_url = original_api_url
            
            return {
                "spot": spot_balance,
                "futures": futures_balance
            }
        except Exception as e:
            self.logger.error(f"Error getting account balances: {e}")
            return {"spot": Decimal("0"), "futures": Decimal("0")}
        
    async def get_current_price(self, symbol):
        """Get current price for a symbol from KuCoin API"""
        try:
            # Special handling for stablecoins
            if symbol in ['USDT', 'USDC', 'DAI', 'BUSD', 'TUSD']:
                return 1.0
                
            # For KuCoin spot trading
            # Convert symbol format if needed (e.g., "BTC" to "BTC-USDT")
            ticker_symbol = symbol if "-" in symbol else f"{symbol}-USDT"
            
            # Get ticker price from KuCoin
            response = await self.request("GET", f"/api/v1/market/orderbook/level1?symbol={ticker_symbol}")
            
            if response and isinstance(response, dict) and "data" in response and response["data"] and "price" in response["data"]:
                return float(response["data"]["price"])
            
            self.logger.warning(f"Could not fetch price for {symbol}, response: {response}")
            return 0  # Return 0 as a fallback value
        except Exception as e:
            self.logger.error(f"Error fetching current price for {symbol}: {e}")
            return 0  # Return 0 as a fallback value
            
    async def get_market_data(self):
        """Get comprehensive market data for strategy analysis"""
        try:
            # Get all tickers
            tickers_response = await self.request("GET", "/api/v1/market/allTickers")
            tickers = tickers_response.get("data", {})
            
            # Get liquid pairs
            pairs = await self.get_liquid_pairs(1000)
            
            # Get 24h stats
            stats = {}
            for pair in pairs[:20]:  # Limit to 20 pairs for efficiency
                stats_response = await self.request("GET", f"/api/v1/market/stats?symbol={pair}")
                if stats_response and "data" in stats_response:
                    stats[pair] = stats_response["data"]
            
            return {
                "tickers": tickers,
                "pairs": pairs,
                "stats": stats,
                "timestamp": int(time.time())
            }
        except Exception as e:
            self.logger.error(f"Error getting market data: {e}")
            return {"tickers": {}, "pairs": [], "stats": {}, "timestamp": int(time.time())}
            
    async def set_leverage(self, symbol, leverage):
        """Set leverage for a symbol in futures trading"""
        try:
            endpoint = "/api/v1/position/risk-limit-level/change"
            params = {
                "symbol": symbol,
                "leverage": leverage
            }
            return await self.request("POST", endpoint, params)
        except Exception as e:
            self.logger.error(f"Error setting leverage for {symbol}: {e}")
            return None
    
    async def create_advanced_order(self, order_type, params):
        """Create advanced order types"""
        endpoint_map = {
            "trailing_stop": "/api/v1/stop-order",
            "oco": "/api/v1/oco/order",
            "iceberg": "/api/v1/iceberg/order"
        }
        return await self.request("POST", endpoint_map[order_type], params)
    
    async def get_klines(self, symbol, interval, start_time=None, end_time=None, limit=None, market_type="spot"):
        """
        Get kline/candlestick data for a symbol
        
        Args:
            symbol: Trading symbol
            interval: Candle interval (e.g., "1hour")
            start_time: Start timestamp in seconds or milliseconds
            end_time: End timestamp in seconds or milliseconds
            limit: Maximum number of candles to return
            market_type: "spot" or "futures"
            
        Returns:
            List of klines
        """
        try:
            # Prepare parameters based on market type
            params = {"symbol": symbol}
            
            if market_type == "spot":
                endpoint = "/api/v1/market/candles"
                params["type"] = interval
                
                if start_time:
                    # Convert to seconds if in milliseconds
                    params["startAt"] = int(start_time / 1000) if start_time > 10000000000 else start_time
                
                if end_time:
                    # Convert to seconds if in milliseconds
                    params["endAt"] = int(end_time / 1000) if end_time > 10000000000 else end_time
                
                if limit:
                    params["limit"] = limit
                    
            else:  # futures
                endpoint = "/api/v1/kline/query"
                
                # Convert interval to granularity (in minutes)
                interval_to_minutes = {
                    '1min': 1,
                    '5min': 5,
                    '15min': 15,
                    '30min': 30,
                    '1hour': 60,
                    '2hour': 120,
                    '4hour': 240,
                    '8hour': 480,
                    '12hour': 720,
                    '1day': 1440,
                    '1week': 10080
                }
                
                # Map common intervals to granularity values
                if interval == "1hour":
                    granularity = 60
                elif interval == "4hour":
                    granularity = 240
                elif interval == "1day":
                    granularity = 1440
                else:
                    # Try to parse from the interval string (e.g., "15min" -> 15)
                    try:
                        if "min" in interval:
                            granularity = int(interval.replace("min", ""))
                        elif "hour" in interval:
                            granularity = int(interval.replace("hour", "")) * 60
                        elif "day" in interval:
                            granularity = int(interval.replace("day", "")) * 1440
                        else:
                            granularity = interval_to_minutes.get(interval, 60)  # Default to 60 if not found
                    except:
                        granularity = 60  # Default to 1 hour if parsing fails
                
                params["granularity"] = granularity
                
                if start_time:
                    # Make sure it's in milliseconds for futures API
                    params["from"] = start_time * 1000 if len(str(start_time)) <= 10 else start_time
                
                if end_time:
                    # Make sure it's in milliseconds for futures API
                    params["to"] = end_time * 1000 if len(str(end_time)) <= 10 else end_time
            
            # Make API request
            query = "&".join([f"{key}={value}" for key, value in params.items()])
            full_endpoint = f"{endpoint}?{query}"
            
            self.logger.info(f"Requesting klines from: {full_endpoint}")
            response = await self.request("GET", full_endpoint)
            
            if response and "data" in response:
                return response["data"]
            else:
                self.logger.error(f"Failed to get klines for {symbol} ({market_type}): {full_endpoint} - {response}")
                return []
                
        except Exception as e:
            self.logger.error(f"Error getting klines for {symbol} ({market_type}): {full_endpoint if 'full_endpoint' in locals() else endpoint} - {e}")
            return []


            
    def _convert_interval(self, interval):
        """Convert interval to KuCoin format"""
        # Map common intervals to KuCoin format
        interval_map = {
            "1min": "1min",
            "3min": "3min",
            "5min": "5min",
            "15min": "15min",
            "30min": "30min",
            "1hour": "1hour",
            "2hour": "2hour",
            "4hour": "4hour",
            "6hour": "6hour",
            "8hour": "8hour",
            "12hour": "12hour",
            "1day": "1day",
            "1week": "1week"
        }
        
        return interval_map.get(interval, "1hour")

    
    async def get_liquid_pairs(self, min_volume):
        """Get trading pairs with volume above threshold"""
        try:
            tickers = await self.request("GET", "/api/v1/market/allTickers")
            if not tickers or "data" not in tickers:
                self.logger.error("Failed to get tickers")
                return []
                
            liquid_pairs = [
                ticker["symbol"] for ticker in tickers["data"]["ticker"] 
                if Decimal(ticker["vol"]) > min_volume
            ]
            self.logger.info(f"Found {len(liquid_pairs)} liquid pairs with volume > {min_volume}")
            return liquid_pairs
        except Exception as e:
            self.logger.error(f"Error analyzing liquid pairs: {e}")
            return []

    async def get_balance(self):
        """Get account balances"""
        response = await self.request("GET", "/api/v1/accounts")
        if not response or "data" not in response:
            self.logger.error("Failed to get account balance")
            return {}
        
        return {item["currency"]: Decimal(item["available"]) for item in response["data"]}
    
    async def get_ticker(self, symbol):
        """Get ticker info for a symbol"""
        response = await self.request("GET", f"/api/v1/market/orderbook/level1?symbol={symbol}")
        if not response or "data" not in response:
            self.logger.error(f"Failed to get ticker for {symbol}")
            return None
        return response["data"]
        
    async def fetch_positions(self):
        """Fetch current account balances from KuCoin (spot trading equivalent of positions)"""
        try:
            # For spot trading, we use account balances as "positions"
            response = await self.request("GET", "/api/v1/accounts")
            if response and "data" in response:
                return response["data"]
            self.logger.info("No account data available")
            return []
        except Exception as e:
            self.logger.error(f"Error fetching positions: {e}")
            return []    
        
    async def close(self):
        await self.session.close()        
