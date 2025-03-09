# backtest_cli.py
import argparse
import asyncio
import sys
from pathlib import Path
from datetime import datetime, timedelta
import json

# Add project root to Python path
sys.path.append(str(Path(__file__).parent))

from utilities.logger import setup_logger
from strategies.trend_following import TrendFollowingStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.scalping import ScalpingStrategy
from strategies.arbitrage import ArbitrageStrategy
from strategies.breakout_trading import BreakoutTradingStrategy
from strategies.momentum_strategy import MomentumStrategy
from strategies.ichimoku_strategy import IchimokuStrategy

def get_strategy_instance(strategy_name, config):
    if strategy_name == "trend_following":
        return TrendFollowingStrategy(config["strategies"]["trend_following"], "trend_following")
    elif strategy_name == "mean_reversion":
        return MeanReversionStrategy(config["strategies"]["mean_reversion"], "mean_reversion")
    elif strategy_name == "ichimoku":
        return IchimokuStrategy(config["strategies"]["ichimoku"], "ichimoku")
    elif strategy_name == "breakout_trading":
        return BreakoutStrategy(config["strategies"]["breakout_trading"], "breakout_trading")
    elif strategy_name == "scalping":
        return ScalpingStrategy(config["strategies"]["scalping"], "scalping")
    elif strategy_name == "arbitrage":
        return ArbitrageStrategy(config["strategies"]["arbitrage"], "arbitrage")
    elif strategy_name == "momentum":
        return MomentumStrategy(config["strategies"]["momentum"], "momentum")
    else:
        raise ValueError(f"Unknown strategy: {strategy_name}")

async def main():
    parser = argparse.ArgumentParser(description="Trading Strategy Backtesting Tools")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # Single strategy backtest command
    backtest_parser = subparsers.add_parser("backtest", help="Run backtest for a single strategy")
    backtest_parser.add_argument("--strategy", required=True, help="Strategy to backtest")
    backtest_parser.add_argument("--symbol", required=True, help="Symbol to test on")
    backtest_parser.add_argument("--market", choices=["spot", "futures", "both"], default="spot", help="Market type")
    backtest_parser.add_argument("--leverage", type=int, default=3, help="Leverage for futures")
    backtest_parser.add_argument("--days", type=int, default=90, help="Number of days to backtest")
    backtest_parser.add_argument("--interval", default="1hour", help="Candle interval")
    backtest_parser.add_argument("--config", default="config/config.json", help="Path to config file")
    
    # Optimize strategy command
    optimize_parser = subparsers.add_parser("optimize", help="Optimize strategy parameters")
    optimize_parser.add_argument("--strategy", required=True, help="Strategy to optimize")
    optimize_parser.add_argument("--symbol", required=True, help="Symbol to test on")
    optimize_parser.add_argument("--market", choices=["spot", "futures"], default="spot", help="Market type")
    optimize_parser.add_argument("--leverage", type=int, default=3, help="Leverage for futures")
    optimize_parser.add_argument("--days", type=int, default=90, help="Number of days to backtest")
    optimize_parser.add_argument("--params", help="JSON string with parameter ranges")
    optimize_parser.add_argument("--config", default="config/config.json", help="Path to config file")
    
    # Compare all strategies command
    compare_parser = subparsers.add_parser("compare", help="Compare all strategies")
    compare_parser.add_argument("--symbol", default="BTC-USDT", help="Symbol to test on")
    compare_parser.add_argument("--market", choices=["spot", "futures"], default="spot", help="Market type")
    compare_parser.add_argument("--leverage", type=int, default=3, help="Leverage for futures")
    compare_parser.add_argument("--days", type=int, default=90, help="Number of days to backtest")
    compare_parser.add_argument("--config", default="config/config.json", help="Path to config file")
    
    # Compare spot vs futures command
    market_parser = subparsers.add_parser("compare-markets", help="Compare spot vs futures for a strategy")
    market_parser.add_argument("--strategy", required=True, help="Strategy to compare")
    market_parser.add_argument("--symbol", required=True, help="Symbol to test on")
    market_parser.add_argument("--leverage", type=int, default=3, help="Leverage for futures")
    market_parser.add_argument("--days", type=int, default=90, help="Number of days to backtest")
    market_parser.add_argument("--config", default="config/config.json", help="Path to config file")
    
    # Run all backtests command
    all_parser = subparsers.add_parser("all", help="Run backtests for all strategies")
    all_parser.add_argument("--symbols", nargs="+", default=["BTC-USDT", "ETH-USDT"], help="Symbols to test on")
    all_parser.add_argument("--market", choices=["spot", "futures", "both"], default="spot", help="Market type")
    all_parser.add_argument("--leverage", type=int, default=3, help="Leverage for futures")
    all_parser.add_argument("--days", type=int, default=90, help="Number of days to backtest")
    all_parser.add_argument("--config", default="config/config.json", help="Path to config file")
    
    args = parser.parse_args()
    
    if args.command == "backtest":
        from backtest.backtest import Backtest
        from core.api_client import KuCoinClient
        
        # Setup logging
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / f"backtest_{args.strategy}_{datetime.now().strftime('%Y%m%d')}.log"
        logger = setup_logger("backtest", log_file)
        
        # Load configuration
        with open(args.config) as f:
            config = json.load(f)
        
        # Set date range
        end_date = int(datetime.now().timestamp() * 1000)
        start_date = int((datetime.now() - timedelta(days=args.days)).timestamp() * 1000)
        
        # Initialize API client and strategy
        api_client = KuCoinClient(config, logger)
        strategy = get_strategy_instance(args.strategy, config)
        strategy.set_api_client(api_client)
        
        # Create futures config with leverage
        futures_config = config.copy()
        if "backtest" not in futures_config:
            futures_config["backtest"] = {}
        futures_config["backtest"]["leverage"] = args.leverage
        
        try:
            if args.market == "spot" or args.market == "both":
                # Run spot backtest
                backtest = Backtest(api_client, config, logger)
                await backtest.run(
                    strategy, 
                    args.symbol, 
                    start_date, 
                    end_date, 
                    market_type="spot", 
                    interval=args.interval
                )
            
            if args.market == "futures" or args.market == "both":
                # Run futures backtest
                futures_backtest = Backtest(api_client, futures_config, logger)
                await futures_backtest.run(
                    strategy, 
                    args.symbol, 
                    start_date, 
                    end_date, 
                    market_type="futures", 
                    interval=args.interval
                )
                
            # If both, generate comparison
            if args.market == "both":
                from backtest.compare_markets import generate_market_comparison_chart
                spot_result = backtest.results[f"{args.symbol}_{args.strategy}_spot"]
                futures_result = futures_backtest.results[f"{args.symbol}_{args.strategy}_futures"]
                await generate_market_comparison_chart(
                    spot_result, 
                    futures_result, 
                    args.symbol, 
                    start_date, 
                    end_date, 
                    args.leverage
                )
                
        finally:
            await api_client.close()
        
    elif args.command == "optimize":
        from backtest.optimize_strategy import optimize_strategy
        
        # Setup logging
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / f"optimize_{args.strategy}_{datetime.now().strftime('%Y%m%d')}.log"
        logger = setup_logger("optimize", log_file)
        
        # Load configuration
        with open(args.config) as f:
            config = json.load(f)
        
        # Set date range
        end_date = int(datetime.now().timestamp() * 1000)
        start_date = int((datetime.now() - timedelta(days=args.days)).timestamp() * 1000)
        
        # Create futures config with leverage if needed
        if args.market == "futures":
            if "backtest" not in config:
                config["backtest"] = {}
            config["backtest"]["leverage"] = args.leverage
        
        # Parse parameter ranges
        if args.params:
            param_ranges = json.loads(args.params)
        else:
            logger.warning(f"No parameter ranges specified for {args.strategy}. Using empty set.")
            param_ranges = {}
    
        # Run optimization
        await optimize_strategy(
            args.strategy,
            args.symbol,
            param_ranges,
            start_date,
            end_date,
            config,
            logger,
            market_type=args.market
        )
        
    elif args.command == "compare":
        from backtest.compare_strategies import compare_all_strategies
        
        # Setup logging
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / f"compare_{datetime.now().strftime('%Y%m%d')}.log"
        logger = setup_logger("compare", log_file)
        
        # Load configuration
        with open(args.config) as f:
            config = json.load(f)
        
        # Set date range
        end_date = int(datetime.now().timestamp() * 1000)
        start_date = int((datetime.now() - timedelta(days=args.days)).timestamp() * 1000)
        
        # Create futures config with leverage if needed
        if args.market == "futures":
            if "backtest" not in config:
                config["backtest"] = {}
            config["backtest"]["leverage"] = args.leverage
        
        # Run comparison
        await compare_all_strategies(
            args.symbol,
            start_date,
            end_date,
            config,
            logger,
            market_type=args.market
        )
        
    elif args.command == "compare-markets":
        from backtest.compare_markets import compare_spot_futures
        
        # Setup logging
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / f"compare_markets_{args.strategy}_{datetime.now().strftime('%Y%m%d')}.log"
        logger = setup_logger("compare_markets", log_file)
        
        # Load configuration
        with open(args.config) as f:
            config = json.load(f)
        
        # Set date range
        end_date = int(datetime.now().timestamp() * 1000)
        start_date = int((datetime.now() - timedelta(days=args.days)).timestamp() * 1000)
        
        # Initialize strategy
        strategy = get_strategy_instance(args.strategy, config)
        
        # Run market comparison
        await compare_spot_futures(
            strategy,
            args.symbol,
            start_date,
            end_date,
            config,
            logger,
            leverage=args.leverage
        )
        
    elif args.command == "all":
        from backtest.run_backtests import run_all_backtests
        
        # Load configuration
        with open(args.config) as f:
            config = json.load(f)
        
        # Create futures config with leverage if needed
        if args.market == "futures" or args.market == "both":
            futures_config = config.copy()
            if "backtest" not in futures_config:
                futures_config["backtest"] = {}
            futures_config["backtest"]["leverage"] = args.leverage
        
        # Run all backtests
        if args.market == "spot" or args.market == "both":
            await run_all_backtests(
                args.config,
                symbols=args.symbols,
                market_type="spot"
            )
        
        if args.market == "futures" or args.market == "both":
            await run_all_backtests(
                args.config,
                symbols=args.symbols,
                market_type="futures",
                leverage=args.leverage
            )
    
    else:
        parser.print_help()

if __name__ == "__main__":
    asyncio.run(main())
