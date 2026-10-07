"""Conciliação do pagamento da fatura do Nubank com o débito no Bradesco."""

from collections.abc import Iterable
from decimal import Decimal

from just_a_sheet.models import LancamentoExistente, Transacao

CATEGORIA_PAGAMENTO = "Pagamento de fatura"
DESCRICAO_PAGAMENTO = "Pagamento da fatura Nubank"
TOLERANCIA = Decimal("1.00")


def totais_da_planilha(existentes: Iterable[LancamentoExistente]) -> list[Decimal]:
    """Total (soma de −valor) das linhas Nubank por mes_ref, sem os pagamentos."""
    por_mes: dict[object, Decimal] = {}
    for lanc in existentes:
        if lanc.origem != "nubank" or lanc.categoria == CATEGORIA_PAGAMENTO:
            continue
        por_mes[lanc.mes_ref] = por_mes.get(lanc.mes_ref, Decimal(0)) - lanc.valor
    return list(por_mes.values())


def conciliar(transacoes: list[Transacao], totais: Iterable[Decimal]) -> int:
    """Marca débitos do Bradesco sem categoria que batem (±R$ 1,00) com uma fatura.

    Retorna quantos lançamentos foram conciliados.
    """
    totais = [t for t in totais if t > 0]
    conciliados = 0
    for t in transacoes:
        if t.categoria or t.origem != "bradesco" or t.valor >= 0:
            continue
        if any(abs(abs(t.valor) - total) <= TOLERANCIA for total in totais):
            t.categoria = CATEGORIA_PAGAMENTO
            t.descricao = DESCRICAO_PAGAMENTO
            t.revisado = True
            t.confianca = 1.0
            t.via = "conciliacao"
            conciliados += 1
    return conciliados
