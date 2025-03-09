from typing import Dict, List, Any
import logging
from .base import Strategy
from .trend_following import TrendFollowingStrategy
from .mean_reversion import MeanReversionStrategy
from .scalping import ScalpingStrategy
from .arbitrage import ArbitrageStrategy
from .breakout_trading import BreakoutTradingStrategy
from .momentum_strategy import MomentumStrategy
from .ichimoku_strategy import IchimokuStrategy

class StrategyManager:
    """Manages the lifecycle and execution of trading strategies"""
    
    def __init__(self, config: dict, api_client=None):
        """
        Initialize the strategy manager
        
        Args:
            config: Configuration dictionary
            api_client: API client instance
        """
        self.config = config
        self.api_client = api_client
        self.strategies: Dict[str, Strategy] = {}
        self.logger = logging.getLogger("StrategyManager")
        
        # Initialize strategies
        self._initialize_strategies()
    
    def _initialize_strategies(self):
        """Initialize all configured strategies"""
        strategy_configs = self.config.get("strategies", {})
        
        # Initialize each strategy if enabled
        if strategy_configs.get("trend_following", {}).get("enabled", False):
            self.strategies["trend_following"] = TrendFollowingStrategy(self.config)
            
        if strategy_configs.get("mean_reversion", {}).get("enabled", False):
            self.strategies["mean_reversion"] = MeanReversionStrategy(self.config)
            
        if strategy_configs.get("scalping", {}).get("enabled", False):
            self.strategies["scalping"] = ScalpingStrategy(self.config)
            
        if strategy_configs.get("arbitrage", {}).get("enabled", False):
            self.strategies["arbitrage"] = ArbitrageStrategy(self.config)
            
        if strategy_configs.get("breakout_trading", {}).get("enabled", False):
            self.strategies["breakout_trading"] = BreakoutTradingStrategy(self.config)
            
        if strategy_configs.get("momentum_strategy", {}).get("enabled", False):
            self.strategies["momentum_strategy"] = MomentumStrategy(self.config)
            
        if strategy_configs.get("ichimoku", {}).get("enabled", False):
            self.strategies["ichimoku"] = IchimokuStrategy(self.config)
        
        # Set API client for all strategies
        if self.api_client:
            for strategy in self.strategies.values():
                strategy.set_api_client(self.api_client)
                
        self.logger.info(f"Initialized {len(self.strategies)} strategies: {', '.join(self.strategies.keys())}")
    
    async def generate_signals(self, market_data: dict) -> List[Dict[str, Any]]:
        """
        Generate trading signals from all enabled strategies
        
        Args:
            market_data: Market data dictionary
            
        Returns:
            List of trading signals from all strategies
        """
        all_signals = []
        
        for strategy_name, strategy in self.strategies.items():
            try:
                self.logger.debug(f"Generating signals for {strategy_name} strategy")
                signals = await strategy.generate_signals(market_data)
                
                # Add strategy name to each signal
                for signal in signals:
                    signal["strategy"] = strategy_name
                
                all_signals.extend(signals)
                self.logger.info(f"{strategy_name} generated {len(signals)} signals")
            except Exception as e:
                self.logger.error(f"Error generating signals for {strategy_name}: {e}")
        
        return all_signals
    
    def get_strategy(self, name: str) -> Strategy:
        """Get a strategy by name"""
        return self.strategies.get(name)
    
    def get_enabled_strategies(self) -> List[str]:
        """Get list of enabled strategy names"""
        return list(self.strategies.keys())
