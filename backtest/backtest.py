from decimal import Decimal
import time
import pandas as pd
import asyncio
import matplotlib.pyplot as plt
from datetime import datetime
import os
from utilities.numeric_utils import ensure_decimal

class Backtest:
    def __init__(self, api_client, config, logger):
        self.api_client = api_client
        self.config = config
        self.logger = logger
        self.results = {}
        
        # Create a directory for backtest results if it doesn't exist
        os.makedirs("backtest_results", exist_ok=True)    

    async def run(self, strategy, symbol, start_time, end_time, market_type="spot", interval="1hour"):
        """
        Run backtest for a given strategy and symbol
        
        Args:
            strategy: Strategy instance
            symbol: Trading symbol
            start_time: Start timestamp
            end_time: End timestamp
            market_type: "spot" or "futures"
            interval: Candle interval
            
        Returns:
            Backtest results
        """
        strategy_name = strategy.__class__.__name__
        self.logger.info(f"Starting {market_type} backtest for {strategy_name} on {symbol} from {start_time} to {end_time}")
        klines = await self.api_client.get_klines(symbol, interval, start_time, end_time, limit=None, market_type=market_type)
        
        try:
            # Get historical data
            klines = await self.api_client.get_klines(symbol, interval, start_time, end_time, market_type)
            
            if not klines:
                self.logger.error(f"Failed to get historical data for {symbol}")
                return None
                
            # Convert to DataFrame for easier analysis
            df = self._convert_klines_to_dataframe(klines)
            
            # Initial capital
            initial_capital = Decimal(self.config.get("backtest", {}).get("initial_capital", "10000"))
            
            # Trading variables
            trades = []
            position = None
            entry_price = Decimal("0")
            capital = initial_capital
            equity_curve = [float(initial_capital)]
            
            # Leverage for futures
            leverage = Decimal(self.config.get("backtest", {}).get("leverage", "1"))
            if market_type == "spot":
                leverage = Decimal("1")  # No leverage in spot
            
            # Create market data structure that mimics the live trading environment
            market_data = {
                symbol: {
                    "klines": df
                }
            }
            
            # Run through each candle
            for i in range(len(df)):
                # Use data up to current candle for generating signals
                current_df = df.iloc[:i+1].copy()
                
                # Update market data with current slice
                market_data[symbol]["klines"] = current_df
                
                # Generate signal using the strategy's actual signal generation method
                signals = await strategy.generate_signals(market_data, market_type=market_type)
                
                if signals:
                    for signal in signals:
                        candle = df.iloc[i]
                        timestamp = candle["time"]
                        close = candle["close"]
                        
                        # Process the signal
                        if signal["action"] == "buy" and position is None:
                            # Enter long position
                            size = Decimal(signal["size"])
                            entry_price = close
                            position = size
                            trades.append({
                                "type": "buy",
                                "time": timestamp,
                                "price": close,
                                "size": size,
                                "capital": capital,
                                "reason": signal.get("reason", "")
                            })
                            self.logger.info(f"Backtest: BUY {size} {symbol} at {close}")
                            
                        elif signal["action"] == "sell" and position is not None and position > 0:
                            # Exit long position
                            pnl = ensure_decimal(position) * (ensure_decimal(close) - ensure_decimal(entry_price)) * ensure_decimal(leverage)
                            capital += pnl
                            trades.append({
                                "type": "sell",
                                "time": timestamp,
                                "price": close,
                                "size": position,
                                "pnl": pnl,
                                "capital": capital,
                                "reason": signal.get("reason", "")
                            })
                            self.logger.info(f"Backtest: SELL {position} {symbol} at {close}, PnL: {pnl}")
                            position = None
                            
                        elif signal["action"] == "sell" and position is None and market_type == "futures":
                            # Enter short position (futures only)
                            size = Decimal(signal["size"])
                            entry_price = close
                            position = -size  # Negative for short positions
                            trades.append({
                                "type": "short",
                                "time": timestamp,
                                "price": close,
                                "size": size,
                                "capital": capital,
                                "reason": signal.get("reason", "")
                            })
                            self.logger.info(f"Backtest: SHORT {size} {symbol} at {close}")
                            
                        elif signal["action"] == "buy" and position is not None and position < 0:
                            # Exit short position
                            pnl = ensure_decimal(abs(position)) * (ensure_decimal(entry_price) - ensure_decimal(close)) * ensure_decimal(leverage)
                            capital += pnl
                            trades.append({
                                "type": "cover",
                                "time": timestamp,
                                "price": close,
                                "size": abs(position),
                                "pnl": pnl,
                                "capital": capital,
                                "reason": signal.get("reason", "")
                            })
                            self.logger.info(f"Backtest: COVER {abs(position)} {symbol} at {close}, PnL: {pnl}")
                            position = None
                
                # Update equity curve at each step
                if position is not None:
                    # Calculate unrealized PnL
                    if position > 0:  # Long position
                        unrealized_pnl = ensure_decimal(position) * (ensure_decimal(close) - ensure_decimal(entry_price)) * ensure_decimal(leverage)
                    else:  # Short position
                        unrealized_pnl = ensure_decimal(abs(position)) * (ensure_decimal(entry_price) - ensure_decimal(close)) * ensure_decimal(leverage)
                    current_equity = capital + unrealized_pnl
                else:
                    current_equity = capital
                
                equity_curve.append(float(current_equity))
            
            # Close any open position at the end
            if position is not None:
                close = df.iloc[-1]["close"]
                if position > 0:  # Long position
                    pnl = ensure_decimal(position) * (ensure_decimal(close) - ensure_decimal(entry_price)) * ensure_decimal(leverage)
                    trade_type = "sell"
                else:  # Short position
                    pnl = ensure_decimal(abs(position)) * (ensure_decimal(entry_price) - ensure_decimal(close)) * ensure_decimal(leverage)
                    trade_type = "cover"
                    
                capital += pnl
                trades.append({
                    "type": trade_type,
                    "time": df.iloc[-1]["time"],
                    "price": close,
                    "size": abs(position),
                    "pnl": pnl,
                    "capital": capital,
                    "reason": "End of backtest"
                })

                self.logger.info(f"Backtest: Final {trade_type.upper()} {abs(position)} {symbol} at {close}, PnL: {pnl}")
            
            # Calculate performance metrics
            total_return = ((capital / initial_capital) - 1) * 100
            
            # Calculate trade metrics
            winning_trades = [t for t in trades if t.get("pnl", 0) > 0]
            losing_trades = [t for t in trades if t.get("pnl", 0) < 0]
            
            win_trades = len(winning_trades)
            loss_trades = len(losing_trades)
            total_trades = win_trades + loss_trades
            
            win_rate = win_trades / total_trades if total_trades > 0 else 0
            
            # Calculate profit factor
            gross_profit = sum(float(t["pnl"]) for t in winning_trades) if winning_trades else 0
            gross_loss = sum(abs(float(t["pnl"])) for t in losing_trades) if losing_trades else 0
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
            
            # Calculate max drawdown
            max_equity = float(initial_capital)
            max_drawdown = 0
            max_drawdown_pct = 0
            
            for i, equity in enumerate(equity_curve):
                equity = float(equity)
                if equity > max_equity:
                    max_equity = equity
                drawdown = max_equity - equity
                drawdown_pct = drawdown / max_equity * 100
                if drawdown_pct > max_drawdown_pct:
                    max_drawdown = drawdown
                    max_drawdown_pct = drawdown_pct
            
            results = {
                "symbol": symbol,
                "strategy": strategy_name,
                "market_type": market_type,
                "leverage": float(leverage),
                "start_time": start_time,
                "end_time": end_time,
                "initial_capital": float(initial_capital),
                "final_capital": float(capital),
                "total_return": float(total_return),
                "total_trades": total_trades,
                "win_trades": win_trades,
                "loss_trades": loss_trades,
                "win_rate": float(win_rate),
                "profit_factor": float(profit_factor),
                "max_drawdown": float(max_drawdown),
                "max_drawdown_pct": float(max_drawdown_pct),
                "trades": trades,
                "equity_curve": equity_curve
            }
            
            result_key = f"{symbol}_{strategy_name}_{market_type}"
            self.results[result_key] = results
            self.logger.info(f"{market_type.capitalize()} backtest completed: {symbol}, Return: {total_return:.2f}%, Win rate: {win_rate:.2f}")
            
            # Generate and save visualization
            self._generate_backtest_report(results)
            
            return results
            
        except Exception as e:
            self.logger.error(f"Error in backtest: {e}", exc_info=True)
            return None

    def _convert_klines_to_dataframe(self, klines):
        """Convert KuCoin klines to pandas DataFrame"""
        data = []
        for kline in reversed(klines):  # Reverse to get chronological order
            data.append({
                "time": kline[0],
                "open": Decimal(kline[1]),
                "close": Decimal(kline[2]),
                "high": Decimal(kline[3]),
                "low": Decimal(kline[4]),
                "volume": Decimal(kline[5])
            })
        return pd.DataFrame(data)

    def _generate_backtest_report(self, results):
        """Generate visual report for backtest results"""
        try:
            strategy = results["strategy"]
            symbol = results["symbol"]
            
            # Create figure with 2 subplots
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), gridspec_kw={'height_ratios': [3, 1]})
            
            # Plot equity curve
            ax1.plot([float(x) for x in results["equity_curve"]])
            ax1.set_title(f"{strategy} on {symbol} - Equity Curve")
            ax1.set_ylabel("Capital")
            ax1.grid(True)
            
            # Plot trade markers
            for trade in results["trades"]:
                idx = results["trades"].index(trade)
                if trade["type"] == "buy":
                    ax1.plot(idx, float(trade["capital"]), 'g^', markersize=8)
                elif trade["type"] == "sell" and trade.get("pnl", 0) > 0:
                    ax1.plot(idx, float(trade["capital"]), 'go', markersize=8)
                elif trade["type"] == "sell" and trade.get("pnl", 0) <= 0:
                    ax1.plot(idx, float(trade["capital"]), 'ro', markersize=8)
                elif trade["type"] == "short":
                    ax1.plot(idx, float(trade["capital"]), 'rv', markersize=8)
                elif trade["type"] == "cover" and trade.get("pnl", 0) > 0:
                    ax1.plot(idx, float(trade["capital"]), 'go', markersize=8)
                elif trade["type"] == "cover" and trade.get("pnl", 0) <= 0:
                    ax1.plot(idx, float(trade["capital"]), 'ro', markersize=8)
            
            # Plot drawdown
            equity_curve = results["equity_curve"]
            max_equity = equity_curve[0]
            drawdown_curve = []
            
            for equity in equity_curve:
                equity = float(equity)
                if equity > max_equity:
                    max_equity = equity
                drawdown_pct = (max_equity - equity) / max_equity * 100
                drawdown_curve.append(drawdown_pct)
            
            ax2.fill_between(range(len(drawdown_curve)), 0, drawdown_curve, color='red', alpha=0.3)
            ax2.set_title("Drawdown (%)")
            ax2.set_xlabel("Time")
            ax2.set_ylabel("Drawdown %")
            ax2.grid(True)
            
            # Add performance metrics as text
            metrics_text = (
                f"Total Return: {results['total_return']:.2f}%\n"
                f"Win Rate: {results['win_rate']*100:.2f}%\n"
                f"Profit Factor: {results['profit_factor']:.2f}\n"
                f"Max Drawdown: {results['max_drawdown_pct']:.2f}%\n"
                f"Total Trades: {results['total_trades']}"
            )
            
            ax1.text(0.02, 0.95, metrics_text, transform=ax1.transAxes, 
                     verticalalignment='top', bbox={'boxstyle': 'round', 'facecolor': 'wheat', 'alpha': 0.5})
            
            # Save figure
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"backtest_results/{strategy}_{symbol.replace('/', '_')}_{timestamp}.png"
            plt.tight_layout()
            plt.savefig(filename)
            plt.close(fig)
            
            self.logger.info(f"Backtest report saved to {filename}")
            
        except Exception as e:
            self.logger.error(f"Error generating backtest report: {e}")

    def compare_strategies(self, symbols=None):
        """Compare performance of different strategies"""
        if not self.results:
            self.logger.warning("No backtest results available for comparison")
            return None
            
        comparison = []
        
        for key, result in self.results.items():
            if symbols and result["symbol"] not in symbols:
                continue
                
            comparison.append({
                "strategy": result["strategy"],
                "symbol": result["symbol"],
                "return": result["total_return"],
                "win_rate": result["win_rate"],
                "profit_factor": result["profit_factor"],
                "max_drawdown": result["max_drawdown_pct"],
                "trades": result["total_trades"]
            })
            
        # Sort by return
        comparison.sort(key=lambda x: x["return"], reverse=True)
        
        # Generate comparison chart
        if comparison:
            self._generate_comparison_chart(comparison)
            
        return comparison
        
    def _generate_comparison_chart(self, comparison):
        """Generate chart comparing strategy performance"""
        try:
            strategies = [item["strategy"] + " (" + item["symbol"] + ")" for item in comparison]
            returns = [item["return"] for item in comparison]
            
            # Create figure
            fig, ax = plt.subplots(figsize=(12, 8))
            
            # Plot returns
            bars = ax.bar(strategies, returns)
            
            # Color bars based on return
            for i, bar in enumerate(bars):
                if returns[i] >= 0:
                    bar.set_color('green')
                else:
                    bar.set_color('red')
            
            # Add labels
            ax.set_title("Strategy Performance Comparison")
            ax.set_ylabel("Total Return (%)")
            ax.set_xlabel("Strategy")
            
            # Rotate x labels for better readability
            plt.xticks(rotation=45, ha='right')
            
            # Add values on top of bars
            for i, v in enumerate(returns):
                ax.text(i, v + 1, f"{v:.2f}%", ha='center')
            
            # Save figure
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"backtest_results/strategy_comparison_{timestamp}.png"
            plt.tight_layout()
            plt.savefig(filename)
            plt.close(fig)
            
            self.logger.info(f"Strategy comparison chart saved to {filename}")
            
        except Exception as e:
            self.logger.error(f"Error generating comparison chart: {e}")
