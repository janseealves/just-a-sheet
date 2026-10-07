"""Parser do extrato Bradesco (PDF, layout "Bradesco Celular").

Cuidado de privacidade: as linhas de cabeçalho/rodapé trazem nome do titular, agência
e conta. Elas são descartadas e NUNCA entram em logs nem em mensagens de erro.
"""

import io
import re
from datetime import date
from decimal import Decimal

from just_a_sheet.ids import make_id_bradesco
from just_a_sheet.models import ArquivoParseado, TipoArquivo, Transacao
from just_a_sheet.money import parse_valor_ptbr

TOLERANCIA = Decimal("0.005")
_NUM = r"-?[\d.]+,\d{2}"
_RE_DATA = re.compile(r"^(\d{2})/(\d{2})/(\d{4})\s*(.*)$")
_RE_CAUDA = re.compile(
    r"(?:^|\s)(?:(\d{2}/\d{2})\s+)?(\d+)\s+([\d.]+,\d{2})\s+(" + _NUM + r")$"
)
_RE_COD_LANC = re.compile(r"^COD\. LANC\..*?(" + _NUM + r")$")
_RE_TOTAL = re.compile(r"^Total\s+([\d.]+,\d{2})\s+([\d.]+,\d{2})\s+(" + _NUM + r")$")
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
_PREFIXOS_CABECALHO = ("Bradesco Celular", "Data:", "Extrato de:", "Data Histórico")


class ErroExtrato(ValueError):
    """Falha de integridade do extrato (saldo ou total não fecha)."""


def extrair_texto(conteudo: bytes) -> str:
    import pdfplumber

    paginas: list[str] = []
    with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
        for pagina in pdf.pages:
            paginas.append(pagina.extract_text() or "")
    return "\n".join(paginas)


def _eh_cabecalho(linha: str) -> bool:
    return linha.startswith(_PREFIXOS_CABECALHO) or "Folha:" in linha


def separar_historico(texto: str) -> tuple[str, str]:
    """Separa `texto` em (historico, contraparte)."""
    achou = _RE_MARCADOR.search(texto)
    if achou:
        return texto[: achou.start()].strip(), texto[achou.start() :].strip()
    for conhecido in _HISTORICOS_CONHECIDOS:
        if texto.startswith(conhecido):
            return conhecido, texto[len(conhecido) :].strip()
    return texto.strip(), ""


def parse_texto(texto: str) -> list[Transacao]:
    transacoes: list[Transacao] = []
    data_atual: date | None = None
    buffer: list[str] = []
    saldo_anterior: Decimal | None = None
    creditos = Decimal(0)
    debitos = Decimal(0)
    n_secao = 0

    for bruta in texto.splitlines():
        linha = bruta.strip()
        if not linha or _eh_cabecalho(linha):
            continue
        if linha.startswith("Últimos Lan"):
            buffer.clear()
            continue

        total = _RE_TOTAL.match(linha)
        if total:
            n_secao += 1
            tot_cred = parse_valor_ptbr(total[1])
            tot_deb = parse_valor_ptbr(total[2])
            tot_saldo = parse_valor_ptbr(total[3])
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
            buffer.clear()
            continue

        data_linha = _RE_DATA.match(linha)
        if data_linha:
            data_atual = date(
                int(data_linha[3]), int(data_linha[2]), int(data_linha[1])
            )
            resto = data_linha[4].strip()
            buffer.clear()
        else:
            resto = linha

        cod = _RE_COD_LANC.match(resto)
        if cod:
            saldo_anterior = parse_valor_ptbr(cod[1])
            buffer.clear()
            continue

        cauda = _RE_CAUDA.search(resto)
        if not cauda:
            if resto:
                buffer.append(resto)
            continue

        if data_atual is None:
            raise ErroExtrato("lançamento antes de qualquer data no extrato")
        if saldo_anterior is None:
            raise ErroExtrato("lançamento antes do saldo inicial (COD. LANC.)")

        docto = int(cauda[2])
        valor = parse_valor_ptbr(cauda[3])
        saldo = parse_valor_ptbr(cauda[4])
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

        historico, contraparte = separar_historico(
            " ".join([*buffer, resto[: cauda.start()]]).strip()
        )
        descricao_original = re.sub(r"\s+", " ", f"{historico} {contraparte}").strip()
        buffer.clear()

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


def parse_pdf(conteudo: bytes) -> ArquivoParseado:
    transacoes = parse_texto(extrair_texto(conteudo))
    return ArquivoParseado(tipo=TipoArquivo.EXTRATO_BRADESCO, transacoes=transacoes)
