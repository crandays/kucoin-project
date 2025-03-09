from decimal import Decimal, getcontext, ROUND_DOWN
import logging
from typing import Dict, Any, Optional

# Set precision for Decimal calculations
getcontext().prec = 10  # Sufficient precision for most cryptocurrency calculations

logger = logging.getLogger(__name__)

def round_to_precision(value: Decimal, precision: int) -> Decimal:

    if precision <= 0:
        return value.quantize(Decimal('1'), rounding=ROUND_DOWN)
    
    precision_str = '0.' + '0' * (precision - 1) + '1'
    return value.quantize(Decimal(precision_str), rounding=ROUND_DOWN)
    
async def get_symbol_precision(api_client, symbol: str) -> int:

    try:
        # Remove FUTURES: prefix if present
        clean_symbol = symbol.replace("FUTURES:", "")
        
        # Get market info
        markets = await api_client.get_markets()
        
        if clean_symbol in markets:
            # Extract precision from market info
            return markets[clean_symbol].get("precision", {}).get("amount", 4)
        
        # Default precision if not found
        return 4
    except Exception as e:
        logger.error(f"Error getting precision for {symbol}: {e}")
        return 4

async def get_account_balance(api_client, is_futures: bool) -> Decimal:

    try:
        balances = await api_client.get_account_balances()
        
        if is_futures:
            # For futures, we use the USDT balance in the futures account
            return Decimal(str(balances.get("futures", {}).get("USDT", {}).get("available", 0)))
        else:
            # For spot, we use the USDT balance in the spot account
            return Decimal(str(balances.get("spot", {}).get("USDT", {}).get("available", 0)))
    except Exception as e:
        logger.error(f"Error getting account balance: {e}")
        return Decimal("0")

async def calculate_trade_size(api_client, symbol: str, price: Decimal, config: Dict[str, Any]) -> Decimal:

    try:
        # Ensure price is a Decimal
        if not isinstance(price, Decimal):
            price = Decimal(str(price))
        
        # Determine if we're dealing with spot or futures
        is_futures = symbol.startswith("FUTURES:") or (":" not in symbol and config["trading"]["mode"] == "futures")
        
        # Get the relevant config section
        trade_config = config["trading"]["futures"] if is_futures else config["trading"]["spot"]
        
        # Get symbol precision
        precision = await get_symbol_precision(api_client, symbol)
        
        # Get leverage (only applicable for futures)
        leverage = Decimal("1")  # Default for spot
        if is_futures:
            leverage = Decimal(str(trade_config.get("leverage", 1)))
        
        # Calculate size based on configuration
        if trade_config.get("fix_amount_per_order", False):
            # Fixed amount approach
            fixed_amount = Decimal(str(trade_config.get("fixed_amount_value", 1.0)))
            
            # For futures, we can use leverage to increase position size
            if is_futures:
                # The fixed amount is the collateral, multiply by leverage to get position size
                position_value = fixed_amount * leverage
            else:
                position_value = fixed_amount
            
            # Convert to base currency amount
            if price > Decimal("0"):
                size = position_value / price
            else:
                logger.error(f"Invalid price {price} for {symbol}")
                return Decimal("0")
        else:
            # Percentage-based approach
            percentage = Decimal(str(trade_config.get("percentage_per_order", 1.0))) / Decimal("100")
            
            # Get account balance
            balance = await get_account_balance(api_client, is_futures)
            
            # Calculate trade value based on percentage of balance
            trade_value = balance * percentage
            
            # For futures, we can use leverage to increase position size
            if is_futures:
                position_value = trade_value * leverage
            else:
                position_value = trade_value
            
            # Convert to base currency amount
            if price > Decimal("0"):
                size = position_value / price
            else:
                logger.error(f"Invalid price {price} for {symbol}")
                return Decimal("0")
        
        # Round to appropriate precision
        size = round_to_precision(size, precision)
        
        # Log the calculation details
        logger.info(f"Calculated trade size for {symbol}: {size} (price: {price}, is_futures: {is_futures}, leverage: {leverage})")
        
        return size
    except Exception as e:
        logger.error(f"Error calculating trade size: {e}")
        return Decimal("0")

async def adjust_size_for_min_notional(api_client, symbol: str, size: Decimal, price: Decimal) -> Decimal:

    try:
        # Remove FUTURES: prefix if present
        clean_symbol = symbol.replace("FUTURES:", "")
        
        # Get market info
        markets = await api_client.get_markets()
        
        if clean_symbol in markets:
            market = markets[clean_symbol]
            min_notional = Decimal(str(market.get("limits", {}).get("cost", {}).get("min", 0)))
            
            # Calculate current notional value
            notional = size * price
            
            # If below minimum, adjust size
            if min_notional > Decimal("0") and notional < min_notional:
                adjusted_size = min_notional / price
                precision = market.get("precision", {}).get("amount", 4)
                return round_to_precision(adjusted_size, precision)
        
        return size
    except Exception as e:
        logger.error(f"Error adjusting size for min notional: {e}")
        return size
        
def calculate_kucoin_liquidation_price(entry_price: Decimal, leverage: Decimal, direction: str, position_size: Decimal,maintenance_margin_ratio: Decimal = Decimal("0.005")) -> Decimal:

    try:
        # Convert inputs to Decimal if they aren't already
        if not isinstance(entry_price, Decimal):
            entry_price = Decimal(str(entry_price))
        if not isinstance(leverage, Decimal):
            leverage = Decimal(str(leverage))
        if not isinstance(position_size, Decimal):
            position_size = Decimal(str(position_size))
        if not isinstance(maintenance_margin_ratio, Decimal):
            maintenance_margin_ratio = Decimal(str(maintenance_margin_ratio))
            
        if leverage <= Decimal("1"):
            # No liquidation risk with 1x leverage
            return Decimal("0") if direction == "long" else Decimal("Infinity")
        
        # KuCoin's liquidation formula is based on the maintenance margin ratio
        # and the initial margin ratio (1/leverage)
        initial_margin_ratio = Decimal("1") / leverage
        
        # Calculate liquidation price
        if direction == "long":
            # For long positions, price drops to liquidation
            liquidation_price = entry_price * (Decimal("1") - initial_margin_ratio + maintenance_margin_ratio)
        else:  # short
            # For short positions, price rises to liquidation
            liquidation_price = entry_price * (Decimal("1") + initial_margin_ratio - maintenance_margin_ratio)
        
        logger.debug(f"Calculated liquidation price for {direction} position: {liquidation_price}")
        return liquidation_price
        
    except Exception as e:
        logger.error(f"Error calculating liquidation price: {e}")
        # Return a safe default value
        return Decimal("0") if direction == "long" else Decimal("999999")

async def set_leverage_for_symbol(api_client, symbol: str, leverage: int) -> bool:

    try:
        # Remove FUTURES: prefix if present
        clean_symbol = symbol.replace("FUTURES:", "")
        
        # Set leverage via API
        result = await api_client.set_leverage(clean_symbol, leverage)
        
        if result and result.get("success", False):
            logger.info(f"Successfully set leverage {leverage}x for {clean_symbol}")
            return True
        else:
            logger.warning(f"Failed to set leverage for {clean_symbol}: {result}")
            return False
    except Exception as e:
        logger.error(f"Error setting leverage for {symbol}: {e}")
        return False
