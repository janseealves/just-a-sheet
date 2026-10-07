from datetime import date
from decimal import Decimal

import pytest

from just_a_sheet.ids import make_id_bradesco
from just_a_sheet.parsers import detectar_tipo
from just_a_sheet.parsers.bradesco_pdf import (
    ErroExtrato,
    extrair_texto,
    parse_palavras,
    parse_pdf,
)
from tests.conftest import make_extrato_pdf, make_pdf

# (data, valor, descricao_original, docto) — tudo fictício
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


def paginas_extrato(valor1="4,10", total_deb1="3.136,29", total_saldo2="1.957,52"):
    """3 páginas: valores no centro, na 2ª linha e na 1ª linha; 2 seções."""
    return [
        {  # valores centralizados entre as duas linhas do histórico
            "alinhamento": "centro",
            "folha": "1/3",
            "itens": [
                ("cod", "31/12/2025", "100,00"),
                (
                    "lanc",
                    "02/01/2026",
                    ["PIX QR CODE DINAMICO", "DES: PADARIA EXEMPLO LTDA 02/01"],
                    "1000001",
                    "d",
                    valor1,
                    "95,90",
                ),
                (
                    "lanc",
                    "05/01/2026",
                    ["PIX RECEBIDO", "REM: EMPRESA EXEMPLO SA 05/01"],
                    "1000002",
                    "c",
                    "5.000,00",
                    "5.095,90",
                ),
                (
                    "lanc",
                    None,
                    ["PIX QR CODE DINAMICO", "DES: MERCADO EXEMPLO 04/01"],
                    "1000003",
                    "d",
                    "61,30",
                    "5.034,60",
                ),
                (
                    "lanc",
                    "06/01/2026",
                    ["RENTAB.INVEST FACILCRED*"],
                    "0000003",
                    "c",
                    "0,01",
                    "5.034,61",
                ),
                (
                    "lanc",
                    None,
                    ["PAGTO ELETRON COBRANCA", "IMOBILIARIA EXEMPLO ADM"],
                    "0000011",
                    "d",
                    "2.000,00",
                    "3.034,61",
                ),
            ],
        },
        {  # valores alinhados à 2ª linha (o texto puro põe o nome na linha certa
            # aqui, mas na página anterior ele cai no lançamento seguinte)
            "alinhamento": "baixo",
            "folha": "2/3",
            "itens": [
                (
                    "lanc",
                    None,
                    ["APLICACAO CDB"],
                    "1000004",
                    "d",
                    "1.000,00",
                    "2.034,61",
                ),
                (
                    "lanc",
                    None,
                    ["PIX ENVIADO", "DES: FULANO DE TAL 06/01"],
                    "1000005",
                    "d",
                    "43,49",
                    "1.991,12",
                ),
                (
                    "lanc",
                    "31/01/2026",
                    ["PAGTO ELETRON COBRANCA", "SEGURADORA EXEMPLO"],
                    "0000012",
                    "d",
                    "27,40",
                    "1.963,72",
                ),
                ("total", "5.000,01", total_deb1, "1.963,72"),
            ],
        },
        {  # segunda seção; valores alinhados à 1ª linha
            "alinhamento": "topo",
            "folha": "3/3",
            "secao2": True,
            "itens": [
                ("cod", "31/01/2026", "1.963,72"),
                (
                    "lanc",
                    "03/02/2026",
                    ["PIX QR CODE DINAMICO", "DES: PADARIA EXEMPLO LTDA 03/02"],
                    "1000006",
                    "d",
                    "6,20",
                    "1.957,52",
                ),
                ("total", "0,00", "6,20", total_saldo2),
            ],
        },
    ]


def test_pdf_gera_transacoes_certas():
    pdf = make_extrato_pdf(paginas_extrato())
    transacoes = parse_pdf(pdf).transacoes  # lê as duas seções e as 3 páginas
    assert len(transacoes) == len(ESPERADAS)
    for t, (data, valor, desc, docto) in zip(transacoes, ESPERADAS, strict=True):
        assert t.data == data
        assert t.mes_ref == data.replace(day=1)
        assert t.valor == Decimal(valor)
        assert t.descricao_original == desc
        assert t.descricao == desc
        assert t.origem == "bradesco"
        assert t.parcela == ""
        assert t.id == make_id_bradesco(data, docto, Decimal(valor))


def test_texto_puro_descasaria_mas_a_posicao_acerta():
    pdf = make_extrato_pdf(paginas_extrato())
    # Com os valores centralizados, o texto corrido põe a linha dos números ENTRE as
    # duas linhas do histórico: o nome do Pix ficaria no lançamento seguinte.
    linhas = extrair_texto(pdf).splitlines()
    i = next(n for n, linha in enumerate(linhas) if "1000001" in linha)
    assert "PADARIA" not in linhas[i]
    assert linhas[i - 1] == "PIX QR CODE DINAMICO"
    assert linhas[i + 1].startswith("DES: PADARIA EXEMPLO LTDA")
    # Pela posição, cada fragmento vai para o lançamento de valores mais próximo.
    primeiro = parse_pdf(pdf).transacoes[0]
    assert (
        primeiro.descricao_original == "PIX QR CODE DINAMICO DES: PADARIA EXEMPLO LTDA"
    )


def test_cabecalho_nao_vaza():
    for t in parse_pdf(make_extrato_pdf(paginas_extrato())).transacoes:
        assert "TITULAR" not in t.descricao_original
        assert "Agencia" not in t.descricao_original


def test_falha_quando_saldo_nao_fecha():
    pdf = make_extrato_pdf(paginas_extrato(valor1="4,11"))
    with pytest.raises(ErroExtrato, match="saldo não fecha") as exc:
        parse_pdf(pdf)
    assert "TITULAR" not in str(exc.value)


def test_falha_quando_total_nao_bate():
    with pytest.raises(ErroExtrato, match="total de débitos"):
        parse_pdf(make_extrato_pdf(paginas_extrato(total_deb1="3.136,30")))
    with pytest.raises(ErroExtrato, match="saldo final"):
        parse_pdf(make_extrato_pdf(paginas_extrato(total_saldo2="1.957,53")))


def test_texto_solto_longe_das_linhas_de_valores_e_ignorado():
    paginas = paginas_extrato()
    paginas[1]["itens"] = [*paginas[1]["itens"], ("solto", "RODAPE QUALQUER")]
    paginas[1]["itens"].insert(-2, ("solto", "AVISO SOLTO"))  # entre lançamentos
    transacoes = parse_pdf(make_extrato_pdf(paginas)).transacoes
    assert [t.descricao_original for t in transacoes] == [e[2] for e in ESPERADAS]


def test_pdf_sem_tabela_falha():
    with pytest.raises(ErroExtrato, match="tabela de lançamentos"):
        parse_palavras([[{"text": "x", "x0": 0, "x1": 1, "top": 0, "bottom": 1}]])


def test_pdf_ponta_a_ponta():
    pdf = make_extrato_pdf(paginas_extrato())
    assert detectar_tipo("extrato.pdf", pdf)[0] == "extrato_bradesco"
    assert "Extrato de:" in extrair_texto(pdf)


def test_pdf_nao_bradesco_e_ignorado():
    pdf = make_pdf(["Documento qualquer sem nada de banco"])
    assert detectar_tipo("x.pdf", pdf)[0] == "ignorado"
    assert detectar_tipo("y.pdf", b"nao e pdf")[0] == "ignorado"
