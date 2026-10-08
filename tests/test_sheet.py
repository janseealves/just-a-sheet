from datetime import date

import pytest

from just_a_sheet.sheet import PlanilhaGspread, para_data, parse_config


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


class FakeWorksheet:
    def __init__(self, coluna: list[str], row_count: int):
        self.coluna = coluna
        self.row_count = row_count
        self.chamadas: list = []

    def col_values(self, n: int) -> list[str]:
        self.chamadas.append(("col_values", n))
        return self.coluna

    def add_rows(self, n: int) -> None:
        self.chamadas.append(("add_rows", n))
        self.row_count += n

    def update(self, range_name, values, value_input_option):
        self.chamadas.append(("update", range_name, values, value_input_option))


class FakeSpreadsheet:
    def __init__(self, ws: FakeWorksheet):
        self.ws = ws

    def worksheet(self, nome: str) -> FakeWorksheet:
        return self.ws


def _planilha(ws: FakeWorksheet) -> PlanilhaGspread:
    p = PlanilhaGspread.__new__(PlanilhaGspread)
    p._sh = FakeSpreadsheet(ws)
    return p


def test_append_grava_apos_ultimo_id_mesmo_com_false_na_coluna_h():
    # col_values(11) só traz até a última célula preenchida da coluna K; a
    # coluna H (FALSE nas linhas vazias) não influencia.
    ws = FakeWorksheet(["id", "a1", "a2", "", "a3"], row_count=1966)
    _planilha(ws).append_lancamentos([["x"] * 11, ["y"] * 11])
    assert ("col_values", 11) in ws.chamadas
    upd = [c for c in ws.chamadas if c[0] == "update"]
    assert len(upd) == 1
    assert upd[0][1] == "A6:K7"
    assert not any(c[0] == "add_rows" for c in ws.chamadas)


def test_append_so_cabecalho_grava_na_linha_2():
    ws = FakeWorksheet(["id"], row_count=1000)
    _planilha(ws).append_lancamentos([["x"] * 11])
    assert [c for c in ws.chamadas if c[0] == "update"][0][1] == "A2:K2"


def test_append_coluna_vazia_grava_na_linha_2():
    ws = FakeWorksheet([], row_count=1000)
    _planilha(ws).append_lancamentos([["x"] * 11])
    assert [c for c in ws.chamadas if c[0] == "update"][0][1] == "A2:K2"


def test_append_adiciona_linhas_quando_nao_cabe():
    ws = FakeWorksheet(["id", "a1", "a2"], row_count=4)
    _planilha(ws).append_lancamentos([["x"] * 11] * 3)
    assert ws.chamadas[-2] == ("add_rows", 2)
    assert ws.chamadas[-1][0] == "update"
    assert ws.chamadas[-1][1] == "A4:K6"


def test_append_importacao_usa_coluna_b():
    ws = FakeWorksheet(["drive_file_id", "f1"], row_count=100)
    _planilha(ws).append_importacao(["x"] * 11)
    assert ("col_values", 2) in ws.chamadas
    assert [c for c in ws.chamadas if c[0] == "update"][0][1] == "A3:K3"
