"""Parser do extrato Bradesco (PDF, layout "Bradesco Celular").

O texto corrido do PDF (`extract_text`) não serve para casar a descrição com o valor:
cada lançamento tem até duas linhas na coluna Histórico e as colunas Docto./Crédito/
Débito/Saldo ficam centralizadas na vertical entre elas, então os números caem ora na
linha de cima, ora na de baixo. Por isso o parser trabalha com a posição das palavras:
acha as "linhas de valores" (docto + valor + saldo) e entrega cada fragmento de texto
(e cada data) ao lançamento cuja linha de valores está mais próxima na vertical.

Cuidado de privacidade: o cabeçalho/rodapé traz nome do titular, agência e conta.
Essa região é descartada e NUNCA entra em logs nem em mensagens de erro.
"""

import io
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from just_a_sheet.ids import make_id_bradesco
from just_a_sheet.models import ArquivoParseado, TipoArquivo, Transacao
from just_a_sheet.money import parse_valor_ptbr

log = logging.getLogger(__name__)

TOLERANCIA = Decimal("0.005")
# Palavras com `top` até esta distância (pt) pertencem à mesma linha visual.
TOLERANCIA_LINHA = 2.0
# Um fragmento de texto/data só é atribuído a uma linha de valores se o centro dele
# estiver a até esta distância (pt) do centro da linha (no layout real: ~4,5 pt).
DISTANCIA_MAXIMA = 14.0

_RE_DATA = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
_RE_VALOR = re.compile(r"^-?[\d.]+,\d{2}$")
_RE_INTEIRO = re.compile(r"^\d+$")
_RE_DM_FINAL = re.compile(r"\s+\d{2}/\d{2}$")
_RE_MARCADOR = re.compile(r"\b(DES|REM):")
_HISTORICOS_CONHECIDOS = (
    "PAGTO ELETRON COBRANCA",
    "PIX QR CODE DINAMICO",
    "PIX QR CODE ESTATICO",
    "PIX RECEBIDO",
    "PIX ENVIADO",
    "APLICACAO CDB",
    "RENTAB.INVEST FACILCRED*",
    "TRANSFERENCIA PIX",
)
_PREFIXO_COD_LANC = "COD. LANC."


class ErroExtrato(ValueError):
    """Falha de integridade do extrato (saldo ou total não fecha)."""


def extrair_texto(conteudo: bytes) -> str:
    """Texto corrido do PDF; usado só para reconhecer o tipo do arquivo."""
    import pdfplumber

    paginas: list[str] = []
    with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
        for pagina in pdf.pages:
            paginas.append(pagina.extract_text() or "")
    return "\n".join(paginas)


def separar_historico(texto: str) -> tuple[str, str]:
    """Separa `texto` em (historico, contraparte)."""
    achou = _RE_MARCADOR.search(texto)
    if achou:
        return texto[: achou.start()].strip(), texto[achou.start() :].strip()
    for conhecido in _HISTORICOS_CONHECIDOS:
        if texto.startswith(conhecido):
            return conhecido, texto[len(conhecido) :].strip()
    return texto.strip(), ""


# --------------------------------------------------------------- geometria


def _norm(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in sem_acento if not unicodedata.combining(c)).lower()


def _centro(palavras: list[dict[str, Any]]) -> float:
    topo = min(p["top"] for p in palavras)
    base = max(p["bottom"] for p in palavras)
    return (topo + base) / 2


def _agrupar_linhas(palavras: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Agrupa palavras na mesma linha visual (`top` próximo), cada uma por x0."""
    linhas: list[list[dict[str, Any]]] = []
    for palavra in sorted(palavras, key=lambda p: (p["top"], p["x0"])):
        if linhas and palavra["top"] - linhas[-1][0]["top"] <= TOLERANCIA_LINHA:
            linhas[-1].append(palavra)
        else:
            linhas.append([palavra])
    return [sorted(linha, key=lambda p: p["x0"]) for linha in linhas]


@dataclass
class _Colunas:
    base_cabecalho: float  # só o que está abaixo disto é corpo da tabela
    x_historico: float  # início da coluna Histórico
    x_docto: float  # início da coluna Docto. (fim do Histórico)
    x_valores: float  # início das colunas Crédito/Débito/Saldo (fim do Docto.)


def _achar_colunas(palavras: list[dict[str, Any]]) -> _Colunas | None:
    """Descobre as colunas pelos cabeçalhos; None se a página não tem a tabela."""
    por_texto: dict[str, dict[str, Any]] = {}
    for p in palavras:
        por_texto.setdefault(_norm(p["text"]), p)
    historico = por_texto.get("historico")
    docto = por_texto.get("docto.")
    credito = por_texto.get("credito")
    if not (historico and docto and credito):
        return None
    if abs(historico["top"] - docto["top"]) > TOLERANCIA_LINHA:
        return None
    return _Colunas(
        base_cabecalho=max(historico["bottom"], docto["bottom"], credito["bottom"]),
        x_historico=historico["x0"] - 6,
        x_docto=docto["x0"] - 8,
        x_valores=credito["x0"] - 10,
    )


@dataclass
class _Registro:
    """Uma linha de valores (docto + valor + saldo), COD. LANC. ou Total."""

    centro: float
    docto: str | None = None
    valores: list[Decimal] = field(default_factory=list)
    eh_total: bool = False
    data: date | None = None
    fragmentos: list[tuple[float, str]] = field(default_factory=list)

    @property
    def texto(self) -> str:
        return " ".join(t for _, t in sorted(self.fragmentos))


def _registro_de_valores(
    linha: list[dict[str, Any]], colunas: _Colunas, totais: list[dict[str, Any]]
) -> _Registro:
    reg = _Registro(centro=_centro(linha))
    valores: list[tuple[float, Decimal]] = []
    for p in linha:
        if _RE_VALOR.match(p["text"]):
            valores.append((p["x1"], parse_valor_ptbr(p["text"])))
        elif _RE_INTEIRO.match(p["text"]) and p["x1"] < colunas.x_valores:
            reg.docto = p["text"]
    reg.valores = [v for _, v in sorted(valores, key=lambda xv: xv[0])]
    reg.eh_total = any(abs(_centro([t]) - reg.centro) <= 3 for t in totais)
    return reg


def _registros_da_pagina(
    palavras: list[dict[str, Any]], colunas: _Colunas
) -> list[_Registro]:
    corpo = [p for p in palavras if p["top"] >= colunas.base_cabecalho - 1]
    numericas: list[dict[str, Any]] = []
    historico: list[dict[str, Any]] = []
    datas: list[dict[str, Any]] = []
    totais: list[dict[str, Any]] = []
    for p in corpo:
        if p["x0"] < colunas.x_historico:
            if _RE_DATA.match(p["text"]):
                datas.append(p)
            elif _norm(p["text"]) == "total":
                totais.append(p)
        elif p["x0"] < colunas.x_docto:
            historico.append(p)
        else:
            numericas.append(p)

    registros = [
        _registro_de_valores(linha, colunas, totais)
        for linha in _agrupar_linhas(numericas)
    ]
    if not registros:
        return registros

    def mais_proximo(centro: float) -> _Registro | None:
        reg = min(registros, key=lambda r: abs(r.centro - centro))
        return reg if abs(reg.centro - centro) <= DISTANCIA_MAXIMA else None

    for d in datas:
        reg = mais_proximo(_centro([d]))
        if reg is not None and reg.data is None and not reg.eh_total:
            dia, mes, ano = d["text"].split("/")
            reg.data = date(int(ano), int(mes), int(dia))
    for linha in _agrupar_linhas(historico):
        reg = mais_proximo(_centro(linha))
        if reg is None:
            log.warning("fragmento de texto sem linha de valores; ignorado")
        elif not reg.eh_total:
            reg.fragmentos.append(
                (min(p["top"] for p in linha), " ".join(p["text"] for p in linha))
            )
    return registros


# --------------------------------------------------------- integridade


def _montar_transacoes(registros: list[_Registro]) -> list[Transacao]:
    transacoes: list[Transacao] = []
    data_atual: date | None = None
    saldo_anterior: Decimal | None = None
    creditos = Decimal(0)
    debitos = Decimal(0)
    n_secao = 0

    for reg in registros:
        if reg.eh_total:
            n_secao += 1
            if len(reg.valores) != 3:
                raise ErroExtrato(f"linha de total da seção {n_secao} ilegível")
            tot_cred, tot_deb, tot_saldo = reg.valores
            if abs(tot_cred - creditos) > TOLERANCIA:
                raise ErroExtrato(
                    f"total de créditos da seção {n_secao} não confere "
                    f"(extrato {tot_cred}, lido {creditos})"
                )
            if abs(tot_deb - debitos) > TOLERANCIA:
                raise ErroExtrato(
                    f"total de débitos da seção {n_secao} não confere "
                    f"(extrato {tot_deb}, lido {debitos})"
                )
            if saldo_anterior is None or abs(tot_saldo - saldo_anterior) > TOLERANCIA:
                raise ErroExtrato(f"saldo final da seção {n_secao} não confere")
            creditos = debitos = Decimal(0)
            continue

        if reg.data is not None:
            data_atual = reg.data

        if reg.texto.startswith(_PREFIXO_COD_LANC):
            if not reg.valores:
                raise ErroExtrato("saldo inicial (COD. LANC.) ilegível")
            saldo_anterior = reg.valores[-1]
            continue

        if len(reg.valores) != 2 or reg.docto is None:
            raise ErroExtrato("linha de lançamento ilegível (docto, valor e saldo)")
        if data_atual is None:
            raise ErroExtrato("lançamento antes de qualquer data no extrato")
        if saldo_anterior is None:
            raise ErroExtrato("lançamento antes do saldo inicial (COD. LANC.)")

        docto = int(reg.docto)
        valor, saldo = reg.valores
        delta = saldo - saldo_anterior
        if abs(delta - valor) <= TOLERANCIA:
            credito = True
        elif abs(delta + valor) <= TOLERANCIA:
            credito = False
        else:
            raise ErroExtrato(
                f"saldo não fecha no docto {docto} de {data_atual.isoformat()} "
                f"(valor {valor}, variação do saldo {delta}); "
                "importação do arquivo cancelada"
            )
        if credito:
            creditos += valor
        else:
            debitos += valor
            valor = -valor
        saldo_anterior = saldo

        historico, contraparte = separar_historico(_RE_DM_FINAL.sub("", reg.texto))
        descricao_original = re.sub(r"\s+", " ", f"{historico} {contraparte}").strip()
        transacoes.append(
            Transacao(
                data=data_atual,
                mes_ref=data_atual.replace(day=1),
                descricao=descricao_original,
                valor=valor,
                origem="bradesco",
                descricao_original=descricao_original,
                id=make_id_bradesco(data_atual, docto, valor),
            )
        )
    return transacoes


def parse_palavras(paginas: list[list[dict[str, Any]]]) -> list[Transacao]:
    """Lê as palavras de cada página (dicts do `pdfplumber.extract_words()`)."""
    registros: list[_Registro] = []
    achou_tabela = False
    for palavras in paginas:
        colunas = _achar_colunas(palavras)
        if colunas is None:
            continue
        achou_tabela = True
        registros.extend(_registros_da_pagina(palavras, colunas))
    if not achou_tabela:
        raise ErroExtrato("tabela de lançamentos não encontrada no PDF")
    return _montar_transacoes(registros)


def parse_pdf(conteudo: bytes) -> ArquivoParseado:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
        paginas = [pagina.extract_words() for pagina in pdf.pages]
    transacoes = parse_palavras(paginas)
    return ArquivoParseado(tipo=TipoArquivo.EXTRATO_BRADESCO, transacoes=transacoes)
