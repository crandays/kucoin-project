import logging
from typing import Dict, Optional, List, Tuple, Any
import time
import base64
import hashlib
import hmac
import json
import aiohttp
from abc import ABC, abstractmethod

class Strategy(ABC):
    def __init__(self, config: dict, name: str):
        self.config = config
        self.name = name
        self.enabled_spot = config["enabled_spot"]
        self.enabled_futures = config["enabled_futures"]
        self.logger = logging.getLogger(f"Strategy_{name}")

    def can_trade_symbol(self, symbol: str) -> bool:
        """Check if strategy can trade the given symbol based on enabled markets"""
        is_spot = symbol.startswith("SPOT:")
        return (is_spot and self.enabled_spot) or (not is_spot and self.enabled_futures)

    def get_enabled_markets(self) -> str:
        """Get string representation of enabled markets"""
        markets = []
        if self.enabled_spot:
            markets.append("SPOT")
        if self.enabled_futures:
            markets.append("FUTURES")
        return "+".join(markets)
        
    async def generate_signals(self, market_data: dict, market_type=None) -> List[dict]:
        """Filter signals based on enabled markets"""
        signals = await self._generate_raw_signals(market_data)
        return [
            signal for signal in signals 
            if self.can_trade_symbol(signal["symbol"])
        ]
    
    @abstractmethod
    async def _generate_raw_signals(self, market_data: dict) -> List[dict]:
        """
        Generate raw trading signals from market data.
        Must be implemented by subclasses.
        
        Args:
            market_data: Dictionary containing market data
            
        Returns:
            List of signal dictionaries
        """
        pass
    
    def _create_kucoin_auth_headers(self, endpoint: str, query_params: str = "", 
                                   api_key: str = None, api_secret: str = None, 
                                   api_passphrase: str = None) -> Dict[str, str]:
        """
        Create authentication headers for KuCoin API v3.
        
        Args:
            endpoint: API endpoint path
            query_params: URL query parameters (including '?' if needed)
            api_key: KuCoin API key
            api_secret: KuCoin API secret
            api_passphrase: KuCoin API passphrase
            
        Returns:
            Dict: Headers for KuCoin API authentication
        """
        now = int(time.time() * 1000)
        str_to_sign = f"{now}GET{endpoint}{query_params}"
        signature = base64.b64encode(
            hmac.new(api_secret.encode('utf-8'), str_to_sign.encode('utf-8'), hashlib.sha256).digest()
        ).decode('utf-8')
        
        passphrase = base64.b64encode(
            hmac.new(api_secret.encode('utf-8'), api_passphrase.encode('utf-8'), hashlib.sha256).digest()
        ).decode('utf-8')
        
        return {
            "KC-API-KEY": api_key,
            "KC-API-SIGN": signature,
            "KC-API-TIMESTAMP": str(now),
            "KC-API-PASSPHRASE": passphrase,
            "KC-API-KEY-VERSION": "3"
        }
    
    async def _make_kucoin_request(self, endpoint: str, query_params: str = "", 
                                  api_key: str = None, api_secret: str = None, 
                                  api_passphrase: str = None) -> Dict:
        """
        Make a request to KuCoin API with proper authentication.
        
        Args:
            endpoint: API endpoint path
            query_params: URL query parameters (including '?' if needed)
            api_key: KuCoin API key
            api_secret: KuCoin API secret
            api_passphrase: KuCoin API passphrase
            
        Returns:
            Dict: Response data or error information
        """
        try:
            headers = self._create_kucoin_auth_headers(
                endpoint, query_params, api_key, api_secret, api_passphrase
            )
            
            url = f"https://api-futures.kucoin.com{endpoint}{query_params}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    data = await response.json()
                    
                    if response.status != 200:
                        self.logger.error(f"KuCoin API error: {response.status}, {data}")
                        return {"success": False, "error": data.get("msg", "Unknown error")}
                        
                    if data.get("code") != "200000":
                        self.logger.error(f"KuCoin error code: {data.get('code')}, {data.get('msg')}")
                        return {"success": False, "error": data.get("msg", "Unknown error")}
                        
                    return {"success": True, "data": data.get("data", {})}
        except Exception as e:
            self.logger.error(f"Error making KuCoin request: {e}")
            return {"success": False, "error": str(e)}
    
    # For backward compatibility
    def get_kucoin_funding_rate(self, symbol: str, api_key: str, api_secret: str, api_passphrase: str) -> float:
        """
        Synchronous version of get_kucoin_funding_rate.
        
        Args:
            symbol: Trading pair (e.g., "XBTUSDTM")
            api_key: KuCoin API key
            api_secret: KuCoin API secret
            api_passphrase: KuCoin API passphrase
            
        Returns:
            float: Current funding rate
        """
        import requests  # Import here to avoid importing if not used
        
        try:
            endpoint = "/api/v1/funding-rate"
            params = f"?symbol={symbol}"
            
            headers = self._create_kucoin_auth_headers(
                endpoint, params, api_key, api_secret, api_passphrase
            )
            
            url = f"https://api-futures.kucoin.com{endpoint}{params}"
            response = requests.get(url, headers=headers)
            data = response.json()
            
            if data.get("code") == "200000" and "data" in data:
                return float(data["data"]["fundingRate"])
            else:
                self.logger.error(f"Error in funding rate response: {data}")
                return 0.0
        except Exception as e:
            self.logger.error(f"Error fetching KuCoin funding rate: {e}")
            return 0.0
    
    async def get_kucoin_funding_rate_async(self, symbol: str, api_key: str = None, 
                                           api_secret: str = None, api_passphrase: str = None) -> float:
        """
        Fetch the current funding rate for a KuCoin futures market using API v3 (async version).
        
        Args:
            symbol: Trading pair (e.g., "XBTUSDTM")
            api_key: KuCoin API key
            api_secret: KuCoin API secret
            api_passphrase: KuCoin API passphrase
            
        Returns:
            float: Current funding rate
        """
        endpoint = "/api/v1/funding-rate"
        params = f"?symbol={symbol}"
        
        result = await self._make_kucoin_request(
            endpoint, params, api_key, api_secret, api_passphrase
        )
        
        if result["success"]:
            return float(result["data"]["fundingRate"])
        return 0.0
    
    async def is_funding_rate_favorable_async(self, symbol: str, direction: str, 
                                             threshold: float = 0.0001,
                                             api_key: str = None, 
                                             api_secret: str = None,
                                             api_passphrase: str = None) -> bool:
        """
        Check if funding rate is favorable for the intended position (async version).
        
        Args:
            symbol: Trading pair (e.g., "XBTUSDTM")
            direction: 'long' or 'short'
            threshold: Maximum acceptable funding rate (absolute value)
            api_key: KuCoin API key
            api_secret: KuCoin API secret
            api_passphrase: KuCoin API passphrase
                
        Returns:
            bool: True if funding rate is favorable or acceptable
        """
        funding_rate = await self.get_kucoin_funding_rate_async(
            symbol, api_key, api_secret, api_passphrase
        )
        
        # Funding rate settlement occurs every 8 hours
        # Positive rate: longs pay shorts
        # Negative rate: shorts pay longs
        if direction == "long":
            # For long positions, negative funding rates are favorable
            return funding_rate <= threshold
        else:  # short
            # For short positions, positive funding rates are favorable
            return funding_rate >= -threshold
    
    # For backward compatibility
    def is_funding_rate_favorable(self, symbol: str, direction: str, 
                                 threshold: float = 0.0001,
                                 api_key: str = None, 
                                 api_secret: str = None,
                                 api_passphrase: str = None) -> bool:
        """
        Check if funding rate is favorable for the intended position.
        
        Args:
            symbol: Trading pair (e.g., "XBTUSDTM")
            direction: 'long' or 'short'
            threshold: Maximum acceptable funding rate (absolute value)
            api_key: KuCoin API key
            api_secret: KuCoin API secret
            api_passphrase: KuCoin API passphrase
                
        Returns:
            bool: True if funding rate is favorable or acceptable
        """
        funding_rate = self.get_kucoin_funding_rate(symbol, api_key, api_secret, api_passphrase)
        
        # Funding rate settlement occurs every 8 hours
        # Positive rate: longs pay shorts
        # Negative rate: shorts pay longs
        if direction == "long":
            # For long positions, negative funding rates are favorable
            return funding_rate <= threshold
        else:  # short
            # For short positions, positive funding rates are favorable
            return funding_rate >= -threshold
    
    async def get_kucoin_funding_history_async(self, symbol: str, from_time: int = None, 
                                              to_time: int = None, api_key: str = None, 
                                              api_secret: str = None, api_passphrase: str = None) -> List[Dict]:
        """
        Get historical funding rates for a KuCoin futures contract (async version).
        
        Args:
            symbol: Contract symbol (e.g., "XBTUSDTM")
            from_time: Start time (milliseconds)
            to_time: End time (milliseconds)
            api_key: KuCoin API key
            api_secret: KuCoin API secret
            api_passphrase: KuCoin API passphrase
            
        Returns:
            List[Dict]: Historical funding rate data
        """
        endpoint = "/api/v1/funding-history"
        
        # Build query parameters
        params = [f"symbol={symbol}"]
        if from_time:
            params.append(f"from={from_time}")
        if to_time:
            params.append(f"to={to_time}")
        
        query_string = f"?{'&'.join(params)}"
        
        result = await self._make_kucoin_request(
            endpoint, query_string, api_key, api_secret, api_passphrase
        )
        
        if result["success"]:
            return result["data"]["dataList"]
        return []
    
    # For backward compatibility
    def get_kucoin_funding_history(self, symbol: str, from_time: int = None, 
                                  to_time: int = None, api_key: str = None, 
                                  api_secret: str = None, api_passphrase: str = None) -> List[Dict]:
        """
        Get historical funding rates for a KuCoin futures contract.
        
        Args:
            symbol: Contract symbol (e.g., "XBTUSDTM")
            from_time: Start time (milliseconds)
            to_time: End time (milliseconds)
            api_key: KuCoin API key
            api_secret: KuCoin API secret
            api_passphrase: KuCoin API passphrase
            
        Returns:
            List[Dict]: Historical funding rate data
        """
        try:
            import requests  # Import here to avoid importing if not used
            
            # KuCoin Futures API endpoint for funding history
            endpoint = "/api/v1/funding-history"
            
            # Build query parameters
            params = [f"symbol={symbol}"]
            if from_time:
                params.append(f"from={from_time}")
            if to_time:
                params.append(f"to={to_time}")
            
            query_string = "&".join(params)
            url = f"https://api-futures.kucoin.com{endpoint}?{query_string}"
            
            headers = self._create_kucoin_auth_headers(
                endpoint, f"?{query_string}", api_key, api_secret, api_passphrase
            )
            
            response = requests.get(url, headers=headers)
            data = response.json()
            
            if data.get("code") == "200000" and "data" in data:
                return data["data"]["dataList"]
            else:
                self.logger.error(f"Error in funding history response: {data}")
                return []
        except Exception as e:
            self.logger.error(f"Error fetching KuCoin funding history: {e}")
            return []
    
    async def analyze_funding_rate_trend_async(self, symbol: str, lookback_hours: int = 24,
                                              api_key: str = None, api_secret: str = None, 
                                              api_passphrase: str = None) -> Dict:
        """
        Analyze funding rate trend to make more informed trading decisions (async version).
        
        Args:
            symbol: Contract symbol (e.g., "XBTUSDTM")
            lookback_hours: Hours to look back
            api_key, api_secret, api_passphrase: API credentials
            
        Returns:
            Dict: Analysis results
        """
        now = int(time.time() * 1000)
        from_time = now - (lookback_hours * 60 * 60 * 1000)
        
        history = await self.get_kucoin_funding_history_async(
            symbol, from_time, now, api_key, api_secret, api_passphrase
        )
        
        if not history:
            return {"trend": "unknown", "avg_rate": 0, "volatility": 0, "samples": 0}
        
        rates = [float(item["fundingRate"]) for item in history]
        avg_rate = sum(rates) / len(rates)
        volatility = sum([abs(r - avg_rate) for r in rates]) / len(rates)
        
        # Determine trend
        if avg_rate > 0.0001:
            trend = "positive"  # Favors short positions
        elif avg_rate < -0.0001:
            trend = "negative"  # Favors long positions
        else:
            trend = "neutral"
            
        return {
            "trend": trend,
            "avg_rate": avg_rate,
            "volatility": volatility,
            "samples": len(rates)
        }
    
    def analyze_funding_rate_trend(self, symbol: str, lookback_hours: int = 24,
                                  api_key: str = None, api_secret: str = None, 
                                  api_passphrase: str = None) -> Dict:
        """
        Analyze funding rate trend to make more informed trading decisions.
        
        Args:
            symbol: Contract symbol (e.g., "XBTUSDTM")
            lookback_hours: Hours to look back
            api_key, api_secret, api_passphrase: API credentials
            
        Returns:
            Dict: Analysis results
        """
        now = int(time.time() * 1000)
        from_time = now - (lookback_hours * 60 * 60 * 1000)
        
        history = self.get_kucoin_funding_history(
            symbol, from_time, now, api_key, api_secret, api_passphrase
        )
        
        if not history:
            return {"trend": "unknown", "avg_rate": 0, "volatility": 0, "samples": 0}
        
        rates = [float(item["fundingRate"]) for item in history]
        avg_rate = sum(rates) / len(rates)
        volatility = sum([abs(r - avg_rate) for r in rates]) / len(rates)
        
        # Determine trend
        if avg_rate > 0.0001:
            trend = "positive"  # Favors short positions
        elif avg_rate < -0.0001:
            trend = "negative"  # Favors long positions
        else:
            trend = "neutral"
            
        return {
            "trend": trend,
            "avg_rate": avg_rate,
            "volatility": volatility,
            "samples": len(rates)
        }
    
    async def load_api_config_async(self, exchange: str = "kucoin") -> Dict[str, str]:
        """
        Load API configuration from a secure config file (async version).
        
        Args:
            exchange: The exchange name to load config for
            
        Returns:
            Dict containing API credentials
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with open("config/api_config.json", "r") as f:
                    config = json.loads(await f.read())
            
            if exchange in config:
                return config[exchange]
            else:
                self.logger.error(f"No configuration found for {exchange}")
                return {}
        except Exception as e:
            self.logger.error(f"Error loading API configuration: {e}")
            return {}
    
    def load_api_config(self, exchange: str = "kucoin") -> Dict[str, str]:
        """
        Load API configuration from a secure config file.
        
        Args:
            exchange: The exchange name to load config for
            
        Returns:
            Dict containing API credentials
        """
        try:
            with open("config/api_config.json", "r") as f:
                config = json.load(f)
            
            if exchange in config:
                return config[exchange]
            else:
                self.logger.error(f"No configuration found for {exchange}")
                return {}
        except Exception as e:
            self.logger.error(f"Error loading API configuration: {e}")
            return {}
