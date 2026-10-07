from datetime import date
from decimal import Decimal

import pytest

from just_a_sheet.ids import make_id_bradesco, make_id_nubank


@pytest.mark.parametrize(("n", "esperado"), [(1, "b937d3338efd"), (2, "0e46baa057ce")])
def test_id_nubank(n, esperado):
    assert (
        make_id_nubank(date(2026, 1, 5), "Padaria Exemplo", Decimal("-12.50"), n)
        == esperado
    )


def test_id_bradesco_docto_com_e_sem_zeros():
    assert make_id_bradesco(date(2026, 1, 7), "1234567", Decimal("-45.90")) == (
        "84af0b36a645"
    )
    assert make_id_bradesco(date(2026, 1, 7), "01000007", Decimal("1500.00")) == (
        "6f418734ac92"
    )
    assert make_id_bradesco(date(2026, 1, 7), 1000007, 1500) == "6f418734ac92"
