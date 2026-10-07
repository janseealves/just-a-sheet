from datetime import date
from decimal import Decimal

import pytest

from just_a_sheet.ids import make_id_bradesco
from just_a_sheet.parsers import detectar_tipo
from just_a_sheet.parsers.bradesco_pdf import (
    ErroExtrato,
    extrair_texto,
    parse_pdf,
    parse_texto,
)
from tests.conftest import make_pdf

ESPERADAS = [
    (
        date(2026, 1, 2),
        "-3.07",
        "PIX QR CODE DINAMICO DES: PADARIA EXEMPLO LTDA",
        1924349,
    ),
    (date(2026, 1, 5), "5000.00", "PIX RECEBIDO REM: EMPRESA EXEMPLO SA", 1608289),
    (date(2026, 1, 5), "-53.45", "PIX QR CODE DINAMICO DES: MERCADO EXEMPLO", 1205332),
    (date(2026, 1, 6), "0.01", "RENTAB.INVEST FACILCRED*", 2),
    (
        date(2026, 1, 6),
        "-2000.00",
        "PAGTO ELETRON COBRANCA IMOBILIARIA EXEMPLO ADM",
        67,
    ),
    (date(2026, 1, 6), "-1000.00", "APLICACAO CDB", 2572034),
    (date(2026, 1, 6), "-43.49", "PIX ENVIADO DES: FULANO DE TAL", 1300140),
    (date(2026, 1, 31), "-31.88", "PAGTO ELETRON COBRANCA SEGURADORA EXEMPLO", 70),
    (
        date(2026, 2, 3),
        "-5.54",
        "PIX QR CODE DINAMICO DES: PADARIA EXEMPLO LTDA",
        1003078,
    ),
]


def test_texto_exemplo_gera_transacoes_certas(texto_bradesco):
    transacoes = parse_texto(texto_bradesco)  # lê as duas seções
    assert len(transacoes) == len(ESPERADAS)
    for t, (data, valor, desc, docto) in zip(transacoes, ESPERADAS, strict=True):
        assert t.data == data
        assert t.mes_ref == data.replace(day=1)
        assert t.valor == Decimal(valor)
        assert t.descricao_original == desc
        assert t.origem == "bradesco"
        assert t.parcela == ""
        assert t.id == make_id_bradesco(data, docto, Decimal(valor))


def test_cabecalho_nao_vaza(texto_bradesco):
    for t in parse_texto(texto_bradesco):
        assert "TITULAR" not in t.descricao_original
        assert "Agencia" not in t.descricao_original


def test_falha_quando_saldo_nao_fecha(texto_bradesco):
    adulterado = texto_bradesco.replace("3,07 96,93", "3,08 96,93")
    with pytest.raises(ErroExtrato, match="saldo não fecha") as exc:
        parse_texto(adulterado)
    assert "TITULAR" not in str(exc.value)


def test_falha_quando_total_nao_bate(texto_bradesco):
    adulterado = texto_bradesco.replace(
        "Total 5.000,01 3.131,89 1.968,12", "Total 5.000,01 3.131,90 1.968,12"
    )
    with pytest.raises(ErroExtrato, match="total de débitos"):
        parse_texto(adulterado)
    adulterado = texto_bradesco.replace(
        "Total 0,00 5,54 1.962,58", "Total 0,00 5,54 1.962,59"
    )
    with pytest.raises(ErroExtrato, match="saldo final"):
        parse_texto(adulterado)


def test_pdf_ponta_a_ponta(texto_bradesco):
    pdf = make_pdf(texto_bradesco.splitlines())
    assert detectar_tipo("extrato.pdf", pdf)[0] == "extrato_bradesco"
    assert "Extrato de:" in extrair_texto(pdf)
    r = parse_pdf(pdf)
    assert [t.id for t in r.transacoes] == [t.id for t in parse_texto(texto_bradesco)]


def test_pdf_nao_bradesco_e_ignorado():
    pdf = make_pdf(["Documento qualquer sem nada de banco"])
    assert detectar_tipo("x.pdf", pdf)[0] == "ignorado"
    assert detectar_tipo("y.pdf", b"nao e pdf")[0] == "ignorado"
