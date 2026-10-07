from datetime import date
from decimal import Decimal

from just_a_sheet.ids import make_id_nubank
from just_a_sheet.models import Regra
from just_a_sheet.parsers import detectar_tipo
from just_a_sheet.parsers.nubank import parse_fatura
from just_a_sheet.rules import aplicar_regras


def _por_titulo(arquivo):
    return {t.descricao_original: t for t in arquivo.transacoes}


def test_sinais_e_total(csv_nubank):
    r = parse_fatura(csv_nubank, "fatura.csv", 9)
    t = _por_titulo(r)
    assert t["Padaria Exemplo"].valor == Decimal("-12.50")  # compra = saída
    assert t["Pagamento recebido"].valor == Decimal("3215.95")  # crédito = entrada
    assert t['Estorno de "Mercado Online" (Mercado)'].valor == Decimal("32.54")
    # total exclui "Pagamento recebido"; 12,50*2 + 4 + 114,55 - 32,54 + 69
    assert r.total_fatura == Decimal("180.01")


def test_parcela_iof_e_pagamento(csv_nubank):
    t = _por_titulo(parse_fatura(csv_nubank, "fatura.csv", 9))
    roupa = t["Loja de Roupas - Parcela 4/10"]
    assert roupa.parcela == "4/10"
    assert roupa.descricao == "Loja de Roupas"
    assert roupa.data == date(2026, 1, 1)  # data mantida como vem
    iof = t['IOF de "Loja Gringa Sub"']
    assert iof.valor == Decimal("-4.00")
    assert iof.estorno_de is None
    assert "Pagamento recebido" in t


def test_ocorrencia_de_linhas_identicas(csv_nubank):
    r = parse_fatura(csv_nubank, "fatura.csv", 9)
    padarias = [t for t in r.transacoes if t.descricao_original == "Padaria Exemplo"]
    assert [t.id for t in padarias] == [
        make_id_nubank(date(2026, 1, 12), "Padaria Exemplo", Decimal("-12.50"), 1),
        make_id_nubank(date(2026, 1, 12), "Padaria Exemplo", Decimal("-12.50"), 2),
    ]
    assert len({t.id for t in r.transacoes}) == len(r.transacoes)


def test_mes_ref_pelo_nome_do_arquivo(csv_nubank):
    for nome in ("nubank_fatura_2026-09-16.csv", "Nubank_2026-09-16.csv"):
        r = parse_fatura(csv_nubank, nome, 9)
        assert {t.mes_ref for t in r.transacoes} == {date(2026, 9, 1)}


def test_mes_ref_inferido(csv_nubank):
    # maior compra = 12/01; fechamento dia 9 -> 12 >= 9 -> vence em fevereiro
    r = parse_fatura(csv_nubank, "fatura.csv", 9)
    assert {t.mes_ref for t in r.transacoes} == {date(2026, 2, 1)}
    # fechamento dia 20 -> 12 < 20 -> vence no mesmo mês
    r = parse_fatura(csv_nubank, "fatura.csv", 20)
    assert {t.mes_ref for t in r.transacoes} == {date(2026, 1, 1)}


def test_mes_ref_inferido_virada_de_ano():
    csv = b'date,title,amount\n2026-12-15,Loja,"10,00"\n'
    r = parse_fatura(csv, "x.csv", 9)
    assert r.transacoes[0].mes_ref == date(2027, 1, 1)


def test_estorno_herda_categoria_de_x(csv_nubank):
    r = parse_fatura(csv_nubank, "fatura.csv", 9)
    estorno = _por_titulo(r)['Estorno de "Mercado Online" (Mercado)']
    assert estorno.estorno_de == "Mercado Online"
    assert estorno.descricao == "Estorno Mercado Online"
    regras = [
        Regra(padrao="Mercado Online", categoria="Mercado", descricao_limpa="Mercado")
    ]
    assert aplicar_regras(estorno, regras, {"Mercado"})
    assert estorno.categoria == "Mercado"
    assert estorno.descricao == "Estorno Mercado"
    assert estorno.valor > 0


def test_detectar_tipo_csv(csv_nubank):
    assert detectar_tipo("a.csv", csv_nubank)[0] == "fatura_nubank"
    tipo, motivo = detectar_tipo("b.csv", b"Extrato de: Ag: 0;Conta: 0\nx;y\n")
    assert tipo == "ignorado"
    assert "envie o PDF" in motivo
    tipo, motivo = detectar_tipo("c.csv", b"a,b,c\n")
    assert (tipo, motivo) == ("ignorado", "formato não reconhecido")
    assert detectar_tipo("d.txt", b"x")[0] == "ignorado"
