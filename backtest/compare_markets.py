# backtest/compare_markets.py
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
from utilities.logger import setup_logger
from utilities.numeric_utils import ensure_decimal

async def compare_spot_futures(strategy, symbol, start_date, end_date, config, logger, leverage=3):
    """
    Compare spot and futures performance for the same strategy
    
    Args:
        strategy: Strategy instance
        symbol: Trading symbol
        start_date: Backtest start date (timestamp)
        end_date: Backtest end date (timestamp)
        config: Configuration dictionary
        logger: Logger instance
        leverage: Leverage to use for futures
        
    Returns:
        Dictionary with comparison results
    """
    api_client = KuCoinClient(config, logger)
    
    # Create a copy of config for futures with leverage
    futures_config = config.copy()
    if "backtest" not in futures_config:
        futures_config["backtest"] = {}
    futures_config["backtest"]["leverage"] = leverage
    
    # Create backtest instances for spot and futures
    spot_backtest = Backtest(api_client, config, logger)
    futures_backtest = Backtest(api_client, futures_config, logger)
    
    # Run spot backtest
    logger.info(f"Running spot backtest for {strategy.__class__.__name__} on {symbol}")
    spot_result = await spot_backtest.run(strategy, symbol, start_date, end_date, market_type="spot")
    
    # Run futures backtest
    logger.info(f"Running futures backtest for {strategy.__class__.__name__} on {symbol}")
    futures_result = await futures_backtest.run(strategy, symbol, start_date, end_date, market_type="futures")
    
    # Generate comparison chart if both results are available
    if spot_result and futures_result:
        await generate_market_comparison_chart(spot_result, futures_result, symbol, start_date, end_date, leverage)
    
    # Clean up resources
    await api_client.close()
    
    return {
        "spot": spot_result,
        "futures": futures_result
    }

async def generate_market_comparison_chart(spot_result, futures_result, symbol, start_date, end_date, leverage):
    """Generate chart comparing spot and futures performance"""
    try:
        # Create directory for results
        os.makedirs("backtest_results", exist_ok=True)
        
        # Convert timestamps to readable dates
        start_date_str = datetime.fromtimestamp(start_date / 1000).strftime('%Y-%m-%d')
        end_date_str = datetime.fromtimestamp(end_date / 1000).strftime('%Y-%m-%d')
        
        strategy_name = spot_result["strategy"]
        
        # Create figure with 2 subplots
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), gridspec_kw={'height_ratios': [3, 1]})
        
        # Plot equity curves
        # Normalize equity curves to start at 100 for fair comparison
        spot_curve = [ensure_decimal(value) * 100 / ensure_decimal(spot_result["equity_curve"][0]) for value in spot_result["equity_curve"]]
        futures_curve = [ensure_decimal(value) * 100 / ensure_decimal(futures_result["equity_curve"][0]) for value in futures_result["equity_curve"]]
        
        ax1.plot(spot_curve, label=f"Spot")
        ax1.plot(futures_curve, label=f"Futures (x{leverage})")
        
        ax1.set_title(f"Spot vs Futures: {strategy_name} on {symbol} ({start_date_str} to {end_date_str})")
        ax1.set_ylabel("Equity (normalized to 100)")
        ax1.legend()
        ax1.grid(True)
        
        # Plot returns as bar chart
        markets = ["Spot", f"Futures (x{leverage})"]
        returns = [ensure_decimal(spot_result["total_return"]), ensure_decimal(futures_result["total_return"])]
        
        bars = ax2.bar(markets, returns)
        
        # Color bars based on return
        for i, bar in enumerate(bars):
            if returns[i] >= 0:
                bar.set_color('green')
            else:
                bar.set_color('red')
        
        ax2.set_title("Total Returns (%)")
        ax2.set_ylabel("Return (%)")
        
        # Add values on top of bars
        for i, v in enumerate(returns):
            ax2.text(i, ensure_decimal(v) + 1, f"{v:.2f}%", ha='center')
        
        # Create a table with detailed metrics
        metrics = pd.DataFrame({
            "Metric": ["Return (%)", "Win Rate (%)", "Profit Factor", "Max Drawdown (%)", "Total Trades"],
            "Spot": [
                f"{spot_result['total_return']:.2f}%",
                f"{spot_result['win_rate']*100:.2f}%",
                f"{spot_result['profit_factor']:.2f}",
                f"{spot_result['max_drawdown_pct']:.2f}%",
                spot_result['total_trades']
            ],
            "Futures": [
                f"{futures_result['total_return']:.2f}%",
                f"{futures_result['win_rate']*100:.2f}%",
                f"{futures_result['profit_factor']:.2f}",
                f"{futures_result['max_drawdown_pct']:.2f}%",
                futures_result['total_trades']
            ]
        })
        
        # Save metrics to CSV
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_filename = f"backtest_results/market_comparison_{strategy_name}_{symbol.replace('/', '_')}_{timestamp}.csv"
        metrics.to_csv(csv_filename, index=False)
        
        # Save figure
        plt.tight_layout()
        fig_filename = f"backtest_results/market_comparison_{strategy_name}_{symbol.replace('/', '_')}_{timestamp}.png"
        plt.savefig(fig_filename)
        plt.close(fig)
        
        print(f"Market comparison chart saved to {fig_filename}")
        print(f"Metrics saved to {csv_filename}")
        
    except Exception as e:
        print(f"Error generating market comparison chart: {e}")
