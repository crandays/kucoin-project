from decimal import Decimal

def ensure_decimal(value):
    """Convert value to Decimal if it's not already"""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
