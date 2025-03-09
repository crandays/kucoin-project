# backtest/run_backtests.py
import asyncio
import json
import logging
from datetime import datetime, timedelta
import sys
import os
from pathlib import Path

# Add project root to Python path
sys.path.append(str(Path(__file__).parent.parent))

from core.api_client import KuCoinClient
from backtest.backtest import Backtest
from strategies.trend_following import TrendFollowingStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.scalping import ScalpingStrategy
from strategies.arbitrage import ArbitrageStrategy
from strategies.breakout_trading import BreakoutTradingStrategy
from strategies.momentum_strategy import MomentumStrategy
from strategies.ichimoku_strategy import IchimokuStrategy
from utilities.logger import setup_logger

async def run_strategy_backtest(strategy_name, symbols, start_date, end_date, config, logger, market_type="spot", leverage=1):
    """Run backtest for a specific strategy on given symbols"""
    api_client = KuCoinClient(config, logger)
    
    # Create futures config with leverage if needed
    if market_type == "futures":
        futures_config = config.copy()
        if "backtest" not in futures_config:
            futures_config["backtest"] = {}
        futures_config["backtest"]["leverage"] = leverage
        backtest = Backtest(api_client, futures_config, logger)
    else:
        backtest = Backtest(api_client, config, logger)
    
    # Initialize the appropriate strategy
    if strategy_name == "trend_following":
        strategy = TrendFollowingStrategy(config, "trend_following")
    elif strategy_name == "mean_reversion":
        strategy = MeanReversionStrategy(config, "mean_reversion")
    elif strategy_name == "scalping":
        strategy = ScalpingStrategy(config, "scalping")
    elif strategy_name == "arbitrage":
        strategy = ArbitrageStrategy(config, "arbitrage")
    elif strategy_name == "breakout_trading":
        strategy = BreakoutTradingStrategy(config, "breakout_trading")
    elif strategy_name == "momentum":
        strategy = MomentumStrategy(config, "momentum")
    elif strategy_name == "ichimoku":
        strategy = IchimokuStrategy(config, "ichimoku")
    else:
        logger.error(f"Unknown strategy: {strategy_name}")
        return None
    
    # Set API client for the strategy
    strategy.set_api_client(api_client)
    
    # Run backtest for each symbol
    results = {}
    for symbol in symbols:
        logger.info(f"Running {market_type} backtest for {strategy_name} on {symbol}")
        result = await backtest.run(strategy, symbol, start_date, end_date, market_type=market_type)
        if result:
            results[symbol] = result
    
    # Compare results across symbols
    comparison = backtest.compare_strategies(symbols)
    
    # Clean up resources
    await api_client.close()
    
    return {
        "strategy": strategy_name,
        "market_type": market_type,
        "leverage": leverage if market_type == "futures" else None,
        "results": results,
        "comparison": comparison
    }

async def run_all_backtests(config_path, strategies=None, symbols=None, market_type="spot", leverage=1):
    """Run backtests for all specified strategies"""
    # Setup logging
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"backtest_{market_type}_{datetime.now().strftime('%Y%m%d')}.log"
    logger = setup_logger("backtest", log_file)
    
    # Load configuration
    with open(config_path) as f:
        config = json.load(f)
    
    # Set leverage for futures
    if market_type == "futures":
        if "backtest" not in config:
            config["backtest"] = {}
        config["backtest"]["leverage"] = leverage
    
    # Default strategies if none specified
    if not strategies:
        strategies = [
            "trend_following", 
            "mean_reversion", 
            "scalping", 
            "arbitrage", 
            "breakout_trading", 
            "momentum", 
            "ichimoku"
        ]
    
    # Default symbols if none specified
    if not symbols:
        symbols = ["BTC-USDT", "ETH-USDT", "ADA-USDT", "SOL-USDT", "XRP-USDT"]
    
    # Default to last 90 days if not specified
    end_date = int(datetime.now().timestamp() * 1000)
    start_date = int((datetime.now() - timedelta(days=90)).timestamp() * 1000)
    
    # Run backtests for each strategy
    all_results = {}
    for strategy_name in strategies:
        logger.info(f"Starting {market_type} backtests for {strategy_name}")
        results = await run_strategy_backtest(
            strategy_name, 
            symbols,
            start_date,
            end_date,
            config,
            logger,
            market_type=market_type,
            leverage=leverage
        )
        all_results[strategy_name] = results
        logger.info(f"Completed backtests for {strategy_name}")
    
    # Generate overall comparison
    logger.info(f"Generating overall {market_type} strategy comparison")
    
    # Create a directory for backtest results if it doesn't exist
    os.makedirs("backtest_results", exist_ok=True)
    
    # Save all results to JSON file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = f"backtest_results/all_{market_type}_backtest_results_{timestamp}.json"
    
    with open(results_file, 'w') as f:
        json.dump(all_results, f, indent=2, default=str)
    
    logger.info(f"All {market_type} backtest results saved to {results_file}")
    
    return all_results
