"""Detecção do tipo de arquivo e despacho para o parser certo."""

from just_a_sheet.models import ArquivoParseado, TipoArquivo
from just_a_sheet.parsers import bradesco_pdf, nubank

MOTIVO_CSV_BRADESCO = (
    "CSV do Bradesco não traz o nome de quem pagou/recebeu; envie o PDF"
)
MOTIVO_NAO_RECONHECIDO = "formato não reconhecido"


def detectar_tipo(nome: str, conteudo: bytes) -> tuple[TipoArquivo, str]:
    """Retorna (tipo, motivo). `motivo` só é preenchido quando `ignorado`."""
    minusculo = nome.lower()
    if minusculo.endswith(".csv"):
        primeira = nubank.decodificar(conteudo).lstrip("﻿").splitlines()
        primeira_linha = primeira[0].strip() if primeira else ""
        if primeira_linha == "date,title,amount":
            return TipoArquivo.FATURA_NUBANK, ""
        if primeira_linha.startswith("Extrato de:") and ";" in primeira_linha:
            return TipoArquivo.IGNORADO, MOTIVO_CSV_BRADESCO
    elif minusculo.endswith(".pdf"):
        try:
            texto = bradesco_pdf.extrair_texto(conteudo)
        except Exception:
            return TipoArquivo.IGNORADO, "PDF ilegível"
        if "Bradesco" in texto and "Extrato de:" in texto:
            return TipoArquivo.EXTRATO_BRADESCO, ""
    return TipoArquivo.IGNORADO, MOTIVO_NAO_RECONHECIDO


def parse_arquivo(
    tipo: TipoArquivo, nome: str, conteudo: bytes, dia_fechamento: int
) -> ArquivoParseado:
    if tipo == TipoArquivo.FATURA_NUBANK:
        return nubank.parse_fatura(conteudo, nome, dia_fechamento)
    if tipo == TipoArquivo.EXTRATO_BRADESCO:
        return bradesco_pdf.parse_pdf(conteudo)
    raise ValueError(f"tipo de arquivo sem parser: {tipo}")
