from decimal import Decimal
import numpy as np

class CorrelationAnalyzer:
    def __init__(self, api_client, logger, config):
        self.api_client = api_client
        self.logger = logger
        self.config = config
        self.correlation_period = config.get("correlation_period", 30)
        self.high_correlation_threshold = Decimal(config.get("high_correlation_threshold", "0.7"))

    async def full_analysis(self, symbols):
        """
        Analyze correlations between symbols
        Returns dict with correlation matrix and high correlation pairs
        """
        if len(symbols) < 2:
            return None
            
        try:
            # Get prices for all symbols
            price_data = {}
            
            for symbol in symbols:
                try:
                    # For crypto, we need to append the quote currency
                    trading_symbol = f"{symbol}-USDT"  # Assuming USDT pairing
                    
                    response = await self.api_client.get_klines(trading_symbol, "1day", limit=self.correlation_period)
                    
                    # Validate response structure
                    if not response:
                        self.logger.warning(f"No kline data received for {trading_symbol}")
                        continue
                        
                    # Extract close prices
                    closes = []
                    for kline in response:
                        try:
                            closes.append(float(kline[2]))
                        except (IndexError, ValueError) as e:
                            self.logger.warning(f"Invalid kline data for {trading_symbol}: {e}")
                            continue
                            
                    if len(closes) >= self.correlation_period:
                        price_data[symbol] = closes
                    else:
                        self.logger.warning(f"Insufficient price data for {trading_symbol}")
                        
                except Exception as e:
                    self.logger.error(f"Error processing {symbol}: {e}")
                    continue
                    
            # Need at least 2 symbols with data
            if len(price_data) < 2:
                self.logger.warning("Not enough price data for correlation analysis")
                return None
                
            # Calculate correlation matrix
            symbols_with_data = list(price_data.keys())
            price_matrix = np.array([price_data[s] for s in symbols_with_data])
            correlation_matrix = np.corrcoef(price_matrix)
            
            # Find high correlations
            high_correlations = []
            for i in range(len(symbols_with_data)):
                for j in range(i+1, len(symbols_with_data)):
                    if abs(correlation_matrix[i, j]) > float(self.high_correlation_threshold):
                        high_correlations.append((
                            symbols_with_data[i], 
                            symbols_with_data[j],
                            correlation_matrix[i, j]
                        ))
            
            # Sort by correlation strength
            high_correlations.sort(key=lambda x: abs(x[2]), reverse=True)
            
            # Create result
            result = {
                "correlation_matrix": correlation_matrix.tolist(),
                "symbols": symbols_with_data,
                "high_correlations": high_correlations,
                "timestamp": time.time()
            }
            
            self.logger.info(f"Correlation analysis completed: found {len(high_correlations)} high correlations")
            return result
            
        except Exception as e:
            self.logger.error(f"Error in correlation analysis: {str(e)}", exc_info=True)
            return None
