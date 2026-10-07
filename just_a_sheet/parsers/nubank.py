"""Parser da fatura Nubank (CSV)."""

import csv
import io
import re
from collections import Counter
from datetime import date
from decimal import Decimal

from just_a_sheet.ids import make_id_nubank
from just_a_sheet.models import ArquivoParseado, TipoArquivo, Transacao
from just_a_sheet.money import parse_valor_ptbr

PAGAMENTO_RECEBIDO = "Pagamento recebido"
_RE_PARCELA = re.compile(r"\s+-\s+Parcela\s+(\d+)/(\d+)$")
_RE_ESTORNO = re.compile(r'^Estorno de "(.+)" \(.*\)$')
_RE_DATA_NO_NOME = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def decodificar(conteudo: bytes) -> str:
    try:
        return conteudo.decode("utf-8-sig")
    except UnicodeDecodeError:
        return conteudo.decode("latin-1")


def _primeiro_dia_mes(d: date, somar_meses: int = 0) -> date:
    indice = d.year * 12 + (d.month - 1) + somar_meses
    return date(indice // 12, indice % 12 + 1, 1)


def _mes_ref(nome: str, datas_compra: list[date], dia_fechamento: int) -> date:
    """Mês do vencimento: pelo nome do arquivo ou inferido pelo fechamento."""
    achado = _RE_DATA_NO_NOME.search(nome)
    if achado:
        try:
            return date(int(achado[1]), int(achado[2]), 1)
        except ValueError:
            pass  # data inválida no nome: cai para a inferência
    if not datas_compra:
        raise ValueError(
            "não foi possível definir o mês de referência da fatura "
            "(sem compras e sem data de vencimento no nome do arquivo)"
        )
    d = max(datas_compra)
    return _primeiro_dia_mes(d, 0 if d.day < dia_fechamento else 1)


def parse_fatura(conteudo: bytes, nome: str, dia_fechamento: int) -> ArquivoParseado:
    leitor = csv.reader(io.StringIO(decodificar(conteudo)))
    linhas = list(leitor)
    brutas: list[tuple[date, str, Decimal]] = []  # (data, title, valor c/ sinal)
    for numero, linha in enumerate(linhas[1:], start=2):
        if not linha or not any(c.strip() for c in linha):
            continue
        if len(linha) < 3:
            raise ValueError(f"linha {numero} do CSV com menos de 3 colunas")
        try:
            data = date.fromisoformat(linha[0].strip())
            valor = -parse_valor_ptbr(linha[2])
        except ValueError as exc:
            raise ValueError(f"linha {numero} do CSV inválida: {exc}") from exc
        brutas.append((data, linha[1], valor))

    datas_compra = [d for d, t, _ in brutas if t != PAGAMENTO_RECEBIDO]
    mes_ref = _mes_ref(nome, datas_compra, dia_fechamento)
    total = sum((-v for d, t, v in brutas if t != PAGAMENTO_RECEBIDO), Decimal(0))

    ocorrencias: Counter[tuple[date, str, Decimal]] = Counter()
    transacoes: list[Transacao] = []
    for data, title, valor in brutas:
        ocorrencias[(data, title, valor)] += 1
        n = ocorrencias[(data, title, valor)]

        descricao = title
        parcela = ""
        achou = _RE_PARCELA.search(title)
        if achou:
            parcela = f"{achou[1]}/{achou[2]}"
            descricao = title[: achou.start()]
        estorno_de = None
        achou_estorno = _RE_ESTORNO.match(title)
        if achou_estorno:
            estorno_de = achou_estorno[1]
            descricao = f"Estorno {estorno_de}"

        transacoes.append(
            Transacao(
                data=data,
                mes_ref=mes_ref,
                descricao=descricao,
                valor=valor,
                origem="nubank",
                parcela=parcela,
                descricao_original=title,
                id=make_id_nubank(data, title, valor, n),
                estorno_de=estorno_de,
            )
        )
    return ArquivoParseado(
        tipo=TipoArquivo.FATURA_NUBANK,
        transacoes=transacoes,
        total_fatura=total,
        mes_ref=mes_ref,
    )
