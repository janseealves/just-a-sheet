from decimal import Decimal

import pytest

from just_a_sheet.money import parse_valor_ptbr


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("12,50", Decimal("12.50")),
        ("- 3.215,95", Decimal("-3215.95")),
        ("1.000,00", Decimal("1000.00")),
        ("-0,01", Decimal("-0.01")),
        ("5.043,49", Decimal("5043.49")),
    ],
)
def test_parse_valor_ptbr(texto, esperado):
    assert parse_valor_ptbr(texto) == esperado


def test_valor_invalido():
    with pytest.raises(ValueError):
        parse_valor_ptbr("abc")
