import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from decimal import Decimal

from core.api_client import KuCoinClient
from core.trading_engine import TradingEngine
from utilities.notifications import Notifications as NotificationManager
from core.position_manager import PositionManager
from core.risk_manager import RiskManager
from core.order_executor import OrderExecutor
# Add this import for StrategyManager
from strategies.strategy_manager import StrategyManager

def setup_logger(name, log_file, level=logging.INFO):
    """Set up logger with file and console handlers"""
    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Create handlers
    file_handler = logging.FileHandler(log_file)
    console_handler = logging.StreamHandler()
    
    # Create formatter and add to handlers
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    # Add handlers to logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

async def main():
    # Setup logging
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"trading_bot_{datetime.now().strftime('%Y%m%d')}.log"
    logger = setup_logger("trading_bot", log_file)
    logger.info("Starting KuCoin Trading Bot...")
    
    api_client = None  # Define outside try block for access in finally
    trading_engine = None
    
    try:
        # Load configuration
        with open('config/config.json') as f:
            config = json.load(f)
        logger.info("Configuration loaded successfully")
        
        # Initialize components
        api_client = KuCoinClient(config, logger)
        logger.info("API client initialized")
        
        notifications = NotificationManager(config["notifications"], logger)
        logger.info("Notification manager initialized")
        
        risk_manager = RiskManager(config, logger)
        logger.info("Risk manager initialized")
        
        position_mgr = PositionManager(api_client, config, notifications, logger)
        logger.info("Position manager initialized")
        
        order_exec = OrderExecutor(api_client, config, logger)
        logger.info("Order executor initialized")
        
        # Initialize the StrategyManager
        strategy_manager = StrategyManager(config, api_client)
        logger.info(f"Strategy manager initialized with strategies: {', '.join(strategy_manager.get_enabled_strategies())}")
        
        # Initialize TradingEngine with the strategy_manager
        trading_engine = TradingEngine(
            api_client,         # exchange
            position_mgr,       # position_manager
            notifications,      # notification_manager
            config,             # config
            logger,             # logger
            strategy_manager=strategy_manager,  
            risk_manager=risk_manager,          
            order_executor=order_exec
        )
        
        # Check account balances before starting
        balances = await api_client.get_account_balances()
        spot_balance = balances["spot"]
        futures_balance = balances["futures"]
        
        logger.info(f"Account balances: Spot USDT: {spot_balance}, Futures USDT: {futures_balance}")
        
        if spot_balance < Decimal("10") and futures_balance < Decimal("10"):
            logger.warning("Insufficient balance in both spot and futures accounts (< 10 USDT). Exiting.")
            await notifications.send_notification(
                "⚠️ Trading bot stopped: Insufficient balance in both accounts (< 10 USDT)"
            )
            return
        
        # Start trading engine
        logger.info("Starting trading engine...")
        await trading_engine.start()
        
        # Keep the program running
        logger.info("Trading bot is now running. Press Ctrl+C to stop.")
        
        # Run indefinitely until interrupted
        while True:
            await asyncio.sleep(60)  # Check every minute
            
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down...")
    except Exception as e:
        logger.critical(f"Critical error in main process: {e}", exc_info=True)
        if 'notifications' in locals():
            await notifications.send_notification(f"🚨 CRITICAL ERROR: Trading bot stopped! Error: {str(e)}")
    finally:
        # Stop the trading engine gracefully
        if trading_engine and trading_engine.is_running:
            try:
                logger.info("Stopping trading engine...")
                await trading_engine.stop()
                logger.info("Trading engine stopped")
            except Exception as e:
                logger.error(f"Error stopping trading engine: {e}")
        
        # Clean up resources
        if api_client:
            logger.info("Closing API client session")
            await api_client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nTrading bot stopped manually.")
    except Exception as e:
        print(f"Unhandled exception: {e}")
