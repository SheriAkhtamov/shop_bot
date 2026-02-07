from decimal import Decimal, InvalidOperation


def normalize_amount(amount) -> int:
    try:
        value = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Invalid amount") from exc

    if not value.is_finite():
        raise ValueError("Invalid amount")

    if value != value.to_integral_value():
        raise ValueError("Invalid amount")

    return int(value)
