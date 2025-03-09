# optimize_strategy.py
import asyncio
import json
import logging
from datetime import datetime, timedelta
import sys
import os
from pathlib import Path
import itertools
import copy

# Add project root to Python path
sys.path.append(str(Path(__file__).parent))

from core.api_client import KuCoinClient
from backtest import Backtest
from strategies.trend_following import TrendFollowingStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.scalping import ScalpingStrategy
from strategies.arbitrage import ArbitrageStrategy
from strategies.breakout_trading import BreakoutTradingStrategy
from strategies.momentum_strategy import MomentumStrategy
from strategies.ichimoku_strategy import IchimokuStrategy
from utilities.logger import setup_logger
from utilities.numeric_utils import ensure_decimal

async def optimize_strategy(strategy_name, symbol, param_ranges, start_date, end_date, config, logger):
    """
    Optimize strategy parameters by running backtests with different parameter combinations
    
    Args:
        strategy_name: Name of the strategy to optimize
        symbol: Trading symbol to test on
        param_ranges: Dictionary of parameter names and their possible values
        start_date: Backtest start date (timestamp)
        end_date: Backtest end date (timestamp)
        config: Configuration dictionary
        logger: Logger instance
        
    Returns:
        Dictionary with optimization results
    """
    api_client = KuCoinClient(config, logger)
    
    # Generate all parameter combinations
    param_names = list(param_ranges.keys())
    param_values = list(param_ranges.values())
    combinations = list(itertools.product(*param_values))
    
    logger.info(f"Optimizing {strategy_name} with {len(combinations)} parameter combinations")
    
    results = []
    best_result = None
    best_return = -float('inf')
    
    # Test each parameter combination
    for i, combo in enumerate(combinations):
        # Create parameter dictionary for this combination
        params = {name: value for name, value in zip(param_names, combo)}
        logger.info(f"Testing combination {i+1}/{len(combinations)}: {params}")
        
        # Create a copy of the config and update with test parameters
        test_config = copy.deepcopy(config)
        
        # Update strategy parameters in config
        if strategy_name in test_config["strategies"]:
            if "parameters" not in test_config["strategies"][strategy_name]:
                test_config["strategies"][strategy_name]["parameters"] = {}
                
            for param_name, param_value in params.items():
                test_config["strategies"][strategy_name]["parameters"][param_name] = param_value
        
        # Initialize strategy with test parameters
        if strategy_name == "trend_following":
            strategy = TrendFollowingStrategy(test_config, "trend_following")
        elif strategy_name == "mean_reversion":
            strategy = MeanReversionStrategy(test_config, "mean_reversion")
        elif strategy_name == "scalping":
            strategy = ScalpingStrategy(test_config, "scalping")
        elif strategy_name == "arbitrage":
            strategy = ArbitrageStrategy(test_config, "arbitrage")
        elif strategy_name == "breakout_trading":
            strategy = BreakoutTradingStrategy(test_config, "breakout_trading")
        elif strategy_name == "momentum":
            strategy = MomentumStrategy(test_config, "momentum")
        elif strategy_name == "ichimoku":
            strategy = IchimokuStrategy(test_config, "ichimoku")
        else:
            logger.error(f"Unknown strategy: {strategy_name}")
            continue
        
        # Set API client for the strategy
        strategy.set_api_client(api_client)
        
        # Run backtest with this parameter set
        backtest = Backtest(api_client, test_config, logger)
        result = await backtest.run(strategy, symbol, start_date, end_date)
        
        if result:
            # Store result with parameters
            result_with_params = {
                "params": params,
                "return": result["total_return"],
                "win_rate": result["win_rate"],
                "profit_factor": result["profit_factor"],
                "max_drawdown": result["max_drawdown_pct"],
                "trades": result["total_trades"]
            }
            
            results.append(result_with_params)
            
            # Update best result if this one is better
            if ensure_decimal(result["total_return"]) > ensure_decimal(best_return):
                best_return = result["total_return"]
                best_result = result_with_params
    
    # Sort results by return
    results.sort(key=lambda x: x["return"], reverse=True)
    
    # Save optimization results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = f"backtest_results/{strategy_name}_optimization_{symbol.replace('/', '_')}_{timestamp}.json"
    
    with open(results_file, 'w') as f:
        json.dump({
            "strategy": strategy_name,
            "symbol": symbol,
            "start_date": start_date,
            "end_date": end_date,
            "best_params": best_result["params"] if best_result else None,
            "best_return": best_return,
            "all_results": results
        }, f, indent=2, default=str)
    
    logger.info(f"Optimization results saved to {results_file}")
    logger.info(f"Best parameters: {best_result['params'] if best_result else None}, Return: {best_return}")
    
    # Clean up resources
    await api_client.close()
    
    return {
        "strategy": strategy_name,
        "symbol": symbol,
        "best_params": best_result["params"] if best_result else None,
        "best_return": best_return,
        "results": results
    }

async def main():
    # Setup logging
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"optimize_{datetime.now().strftime('%Y%m%d')}.log"
    logger = setup_logger("optimize", log_file)
    
    # Load configuration
    with open("config/config.json") as f:
        config = json.load(f)
    
    # Example parameter ranges for trend following strategy
    param_ranges = {
        "sma_short_period": [5, 9, 14],
        "sma_long_period": [21, 30, 50],
        "adx_threshold": [20, 25, 30]
    }
    
    # Set date range (last 90 days)
    end_date = int(datetime.now().timestamp() * 1000)
    start_date = int((datetime.now() - timedelta(days=90)).timestamp() * 1000)
    
    # Run optimization
    results = await optimize_strategy(
        "trend_following",
        "BTC-USDT",
        param_ranges,
        start_date,
        end_date,
        config,
        logger
    )
    
    print(f"Optimization complete. Best parameters: {results['best_params']}")
    print(f"Best return: {results['best_return']:.2f}%")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Optimize trading strategy parameters")
    parser.add_argument("--strategy", required=True, help="Strategy to optimize")
    parser.add_argument("--symbol", required=True, help="Symbol to test on")
    parser.add_argument("--config", default="config/config.json", help="Path to config file")
    parser.add_argument("--params", help="JSON string with parameter ranges")
    
    args = parser.parse_args()
    
    if args.params:
        param_ranges = json.loads(args.params)
    else:
        # Default parameter ranges based on strategy
        if args.strategy == "trend_following":
            param_ranges = {
                "sma_short_period": [5, 9, 14],
                "sma_long_period": [21, 30, 50],
                "adx_threshold": [20, 25, 30]
            }
        elif args.strategy == "mean_reversion":
            param_ranges = {
                "sma_period": [10, 20, 30],
                "deviation_threshold": [1.5, 2.0, 2.5],
                "rsi_period": [10, 14, 21],
                "rsi_oversold": [20, 30, 40],
                "rsi_overbought": [60, 70, 80]
            }
        elif args.strategy == "breakout_trading":
            param_ranges = {
                "resistance_period": [10, 20, 30],
                "confirmation_candles": [2, 3, 4],
                "min_volume_increase": [1.5, 2.0, 2.5]
            }
        elif args.strategy == "scalping":
            param_ranges = {
                "spread_threshold": [0.1, 0.2, 0.3],
                "order_book_depth": [10, 20, 30],
                "min_volume": [5000, 10000, 20000]
            }
        elif args.strategy == "momentum":
            param_ranges = {
                "short_period": [5, 9, 14],
                "medium_period": [15, 21, 30],
                "long_period": [40, 50, 60],
                "rsi_period": [10, 14, 21]
            }
        elif args.strategy == "ichimoku":
            param_ranges = {
                "tenkan_period": [7, 9, 11],
                "kijun_period": [22, 26, 30],
                "senkou_span_b_period": [44, 52, 60],
                "displacement": [22, 26, 30]
            }
        else:
            print(f"No default parameter ranges for {args.strategy}. Please provide --params.")
            sys.exit(1)
    
    # Set date range (last 90 days)
    end_date = int(datetime.now().timestamp() * 1000)
    start_date = int((datetime.now() - timedelta(days=90)).timestamp() * 1000)
    
    # Load configuration
    with open(args.config) as f:
        config = json.load(f)
    
    # Setup logging
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"optimize_{args.strategy}_{datetime.now().strftime('%Y%m%d')}.log"
    logger = setup_logger("optimize", log_file)
    
    # Run optimization
    asyncio.run(optimize_strategy(
        args.strategy,
        args.symbol,
        param_ranges,
        start_date,
        end_date,
        config,
        logger
    ))
