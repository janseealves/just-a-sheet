from datetime import date

import pytest

from just_a_sheet.sheet import para_data, parse_config


def test_para_data_formatos():
    assert para_data(46143) == date(2026, 5, 1)  # serial do Sheets
    assert para_data("2026-09-01") == date(2026, 9, 1)
    assert para_data("01/09/2026") == date(2026, 9, 1)
    assert para_data("set./2026") == date(2026, 9, 1)
    assert para_data("fev/2026") == date(2026, 2, 1)
    assert para_data("") is None


def test_parse_config():
    cfg = parse_config(
        [
            ["dia_fechamento_nubank", 9],
            ["dia_vencimento_nubank", "16"],
            ["inicio_dados", 46143],
            ["outra", "x", "formula"],
        ]
    )
    assert cfg.dia_fechamento_nubank == 9
    assert cfg.dia_vencimento_nubank == 16
    assert cfg.inicio_dados == date(2026, 5, 1)


def test_parse_config_parametro_ausente():
    with pytest.raises(ValueError, match="inicio_dados"):
        parse_config([["dia_fechamento_nubank", 9], ["dia_vencimento_nubank", 16]])
