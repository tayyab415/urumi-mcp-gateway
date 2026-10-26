"""How the gateway words an upstream tool's failures."""

from __future__ import annotations

from gateway.core import tidy_error_text


def test_validation_errors_lose_the_library_internals():
    multiline = (
        "Error executing tool list_orders: 1 validation error for list_ordersArguments\n"
        "period\n"
        "  Input should be 'today', 'yesterday', 'last_7_days' or 'last_30_days' [type=literal_error, input_value='nonsense', input_type=str]\n"
        "    For further information visit https://errors.pydantic.dev/2.14/v/literal_error"
    )
    # Some clients flatten whitespace; the same text must still be understood.
    flat = " ".join(multiline.split())
    for text in (multiline, flat):
        out = tidy_error_text(text)
        assert out == "Invalid arguments for list_orders. period: Input should be 'today', 'yesterday', 'last_7_days' or 'last_30_days'."
    two = (
        "Error executing tool quote_shipping: 2 validation errors for quote_shippingArguments\n"
        "weight_kg\n  Input should be greater than 0 [type=greater_than, input_value=0, input_type=int]\n"
        "    For further information visit https://errors.pydantic.dev/2.14/v/greater_than\n"
        "items.0.sku\n  Field required [type=missing, input_value={'quantity': 1}, input_type=dict]\n"
        "    For further information visit https://errors.pydantic.dev/2.14/v/missing"
    )
    assert tidy_error_text(two) == (
        "Invalid arguments for quote_shipping. weight_kg: Input should be greater than 0; items.0.sku: Field required."
    )
    # Anything else passes through untouched.
    assert tidy_error_text("Order 999999 does not exist.") == "Order 999999 does not exist."
