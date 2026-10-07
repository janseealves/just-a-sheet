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
        "-4.10",
        "PIX QR CODE DINAMICO DES: PADARIA EXEMPLO LTDA",
        1000001,
    ),
    (date(2026, 1, 5), "5000.00", "PIX RECEBIDO REM: EMPRESA EXEMPLO SA", 1000002),
    (date(2026, 1, 5), "-61.30", "PIX QR CODE DINAMICO DES: MERCADO EXEMPLO", 1000003),
    (date(2026, 1, 6), "0.01", "RENTAB.INVEST FACILCRED*", 3),
    (
        date(2026, 1, 6),
        "-2000.00",
        "PAGTO ELETRON COBRANCA IMOBILIARIA EXEMPLO ADM",
        11,
    ),
    (date(2026, 1, 6), "-1000.00", "APLICACAO CDB", 1000004),
    (date(2026, 1, 6), "-43.49", "PIX ENVIADO DES: FULANO DE TAL", 1000005),
    (date(2026, 1, 31), "-27.40", "PAGTO ELETRON COBRANCA SEGURADORA EXEMPLO", 12),
    (
        date(2026, 2, 3),
        "-6.20",
        "PIX QR CODE DINAMICO DES: PADARIA EXEMPLO LTDA",
        1000006,
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
    adulterado = texto_bradesco.replace("4,10 95,90", "4,11 95,90")
    with pytest.raises(ErroExtrato, match="saldo não fecha") as exc:
        parse_texto(adulterado)
    assert "TITULAR" not in str(exc.value)


def test_falha_quando_total_nao_bate(texto_bradesco):
    adulterado = texto_bradesco.replace(
        "Total 5.000,01 3.136,29 1.963,72", "Total 5.000,01 3.136,30 1.963,72"
    )
    with pytest.raises(ErroExtrato, match="total de débitos"):
        parse_texto(adulterado)
    adulterado = texto_bradesco.replace(
        "Total 0,00 6,20 1.957,52", "Total 0,00 6,20 1.957,53"
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
