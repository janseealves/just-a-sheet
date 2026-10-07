from datetime import date
from decimal import Decimal

from just_a_sheet.models import LancamentoExistente, Transacao
from just_a_sheet.reconcile import conciliar, totais_da_planilha


def _deb(valor: str, origem: str = "bradesco") -> Transacao:
    return Transacao(
        data=date(2026, 2, 10),
        mes_ref=date(2026, 2, 1),
        descricao="PAGTO ELETRON COBRANCA NUBANK",
        valor=Decimal(valor),
        origem=origem,
        descricao_original="PAGTO ELETRON COBRANCA NUBANK",
        id=valor,
    )


def test_tolerancia_de_um_real():
    totais = [Decimal("500.00")]
    dentro = [_deb("-500.99"), _deb("-499.00"), _deb("-500.00")]
    fora = [_deb("-501.01"), _deb("-498.99")]
    assert conciliar(dentro + fora, totais) == 3
    assert all(t.categoria == "Pagamento de fatura" for t in dentro)
    assert all(t.categoria == "" for t in fora)
    t = dentro[0]
    assert t.descricao == "Pagamento da fatura Nubank"
    assert t.revisado and t.confianca == 1.0


def test_so_debito_bradesco_sem_categoria():
    totais = [Decimal("500.00")]
    credito = _deb("500.00")
    nubank = _deb("-500.00", origem="nubank")
    ja_classificado = _deb("-500.00")
    ja_classificado.categoria = "Moradia"
    assert conciliar([credito, nubank, ja_classificado], totais) == 0


def test_totais_da_planilha_por_mes_sem_pagamentos():
    def lanc(valor, cat="Compras", mes=date(2026, 2, 1), origem="nubank"):
        return LancamentoExistente(
            id=str(valor) + cat, mes_ref=mes, valor=Decimal(valor),
            categoria=cat, origem=origem,
        )  # fmt: skip

    existentes = [
        lanc("-100"),
        lanc("-50.50"),
        lanc("3000", cat="Pagamento de fatura"),  # excluído
        lanc("-70", mes=date(2026, 3, 1)),
        lanc("-999", origem="bradesco"),  # não é Nubank
    ]
    assert sorted(totais_da_planilha(existentes)) == [
        Decimal("70"),
        Decimal("150.50"),
    ]
