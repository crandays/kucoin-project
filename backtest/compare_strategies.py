import asyncio
import json
import logging
from datetime import datetime, timedelta
import sys
import os
from pathlib import Path
import matplotlib.pyplot as plt
import pandas as pd

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
from utilities.numeric_utils import ensure_decimal

async def compare_all_strategies(symbol, start_date, end_date, config, logger, market_type="spot", leverage=3):
    """
    Compare all strategies on the same symbol and date range
    
    Args:
        symbol: Trading symbol to test on
        start_date: Backtest start date (timestamp)
        end_date: Backtest end date (timestamp)
        config: Configuration dictionary
        logger: Logger instance
        market_type: "spot" or "futures"
        leverage: Leverage to use for futures
        
    Returns:
        Dictionary with comparison results
    """
    api_client = KuCoinClient(config, logger)
    
    # Create futures config with leverage if needed
    if market_type == "futures":
        if "backtest" not in config:
            config["backtest"] = {}
        config["backtest"]["leverage"] = leverage
    
    backtest = Backtest(api_client, config, logger)
    
    # Initialize all strategies
    strategies = {
        "trend_following": TrendFollowingStrategy(config, "trend_following"),
        "mean_reversion": MeanReversionStrategy(config, "mean_reversion"),
        "scalping": ScalpingStrategy(config, "scalping"),
        "arbitrage": ArbitrageStrategy(config, "arbitrage"),
        "breakout_trading": BreakoutTradingStrategy(config, "breakout_trading"),
        "momentum": MomentumStrategy(config, "momentum"),
        "ichimoku": IchimokuStrategy(config, "ichimoku")
    }
    
    # Set API client for all strategies
    for strategy in strategies.values():
        strategy.set_api_client(api_client)
    
    # Run backtest for each strategy
    results = {}
    equity_curves = {}
    
    for name, strategy in strategies.items():
        logger.info(f"Running {market_type} backtest for {name} on {symbol}")
        result = await backtest.run(strategy, symbol, start_date, end_date, market_type=market_type)
        
        if result:
            results[name] = {
                "return": result["total_return"],
                "win_rate": result["win_rate"],
                "profit_factor": result["profit_factor"],
                "max_drawdown": result["max_drawdown_pct"],
                "trades": result["total_trades"]
            }
            equity_curves[name] = result["equity_curve"]
    
    # Generate comparison chart
    await generate_comparison_chart(results, equity_curves, symbol, start_date, end_date, market_type, leverage)
    
    # Clean up resources
    await api_client.close()
    
    return {
        "symbol": symbol,
        "market_type": market_type,
        "leverage": leverage if market_type == "futures" else None,
        "start_date": start_date,
        "end_date": end_date,
        "results": results
    }

async def generate_comparison_chart(results, equity_curves, symbol, start_date, end_date, market_type, leverage=None):
    """Generate charts comparing strategy performance"""
    try:
        # Create directory for results
        os.makedirs("backtest_results", exist_ok=True)
        
        # Convert timestamps to readable dates
        start_date_str = datetime.fromtimestamp(start_date / 1000).strftime('%Y-%m-%d')
        end_date_str = datetime.fromtimestamp(end_date / 1000).strftime('%Y-%m-%d')
        
        # Create figure with 2 subplots
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), gridspec_kw={'height_ratios': [3, 1]})
        
        # Plot equity curves
        for strategy_name, curve in equity_curves.items():
            # Normalize equity curves to start at 100 for fair comparison
            normalized_curve = [ensure_decimal(value) * 100 / ensure_decimal(curve[0]) for value in curve]
            ax1.plot(normalized_curve, label=strategy_name)
        
        ax1.set_title(f"Strategy Comparison on {symbol} ({start_date_str} to {end_date_str})")
        ax1.set_ylabel("Equity (normalized to 100)")
        ax1.legend()
        ax1.grid(True)
        
        # Plot returns as bar chart
        strategies = list(results.keys())
        returns = [ensure_decimal(results[s]["return"]) for s in strategies]
        
        bars = ax2.bar(strategies, returns)
        
        # Color bars based on return
        for i, bar in enumerate(bars):
            if returns[i] >= 0:
                bar.set_color('green')
            else:
                bar.set_color('red')
        
        ax2.set_title("Total Returns (%)")
        ax2.set_ylabel("Return (%)")
        
        # Rotate x labels for better readability
        plt.xticks(rotation=45, ha='right')
        
        # Add values on top of bars
        for i, v in enumerate(returns):
            ax2.text(i, ensure_decimal(v) + 1, f"{v:.2f}%", ha='center')
        
        # Create a table with detailed metrics
        metrics_df = pd.DataFrame(results).T
        metrics_df = metrics_df.round(2)
        
        # Add table below the chart
        table_data = []
        table_columns = ["Strategy", "Return (%)", "Win Rate", "Profit Factor", "Max DD (%)", "Trades"]
        
        for strategy, metrics in results.items():
            table_data.append([
                strategy,
                f"{metrics['return']:.2f}%",
                f"{metrics['win_rate']*100:.2f}%",
                f"{metrics['profit_factor']:.2f}",
                f"{metrics['max_drawdown']:.2f}%",
                metrics['trades']
            ])
        
        # Save metrics to CSV
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_filename = f"backtest_results/strategy_comparison_{symbol.replace('/', '_')}_{timestamp}.csv"
        metrics_df.to_csv(csv_filename)
        
        # Save figure
        plt.tight_layout()
        fig_filename = f"backtest_results/strategy_comparison_{symbol.replace('/', '_')}_{timestamp}.png"
        plt.savefig(fig_filename)
        plt.close(fig)
        
        print(f"Comparison chart saved to {fig_filename}")
        print(f"Metrics saved to {csv_filename}")
        
    except Exception as e:
        print(f"Error generating comparison chart: {e}")

async def main():
    # Parse command line arguments
    import argparse
    
    parser = argparse.ArgumentParser(description="Compare all trading strategies")
    parser.add_argument("--symbol", default="BTC-USDT", help="Symbol to test on")
    parser.add_argument("--config", default="config/config.json", help="Path to config file")
    parser.add_argument("--days", type=int, default=90, help="Number of days to backtest")
    
    args = parser.parse_args()
    
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
    
    # Run comparison
    await compare_all_strategies(
        args.symbol,
        start_date,
        end_date,
        config,
        logger
    )

if __name__ == "__main__":
    asyncio.run(main())
