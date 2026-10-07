"""Classificação determinística pelas regras da planilha."""

import re
import unicodedata

from just_a_sheet.models import Regra, Transacao


def normalizar(texto: str) -> str:
    """NFKD, sem acentos, minúsculas."""
    decomposto = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c))
    return sem_acento.lower()


def casa(padrao: str, texto: str) -> bool:
    """O padrão só casa a partir do início de uma palavra."""
    return (
        re.search(r"(^|[^a-z0-9])" + re.escape(normalizar(padrao)), normalizar(texto))
        is not None
    )


def primeira_regra(regras: list[Regra], texto: str, origem: str) -> Regra | None:
    """Primeira regra (na ordem da planilha) que casa; origem vazia = qualquer."""
    for regra in regras:
        if regra.origem.strip() and normalizar(regra.origem) != normalizar(origem):
            continue
        if regra.padrao.strip() and casa(regra.padrao, texto):
            return regra
    return None


def aplicar_regras(
    transacao: Transacao, regras: list[Regra], categorias_validas: set[str]
) -> bool:
    """Aplica a primeira regra que casar. Retorna True se classificou.

    Estorno do Nubank: casa pelo `X` de `Estorno de "X" (Y)` e a descrição vira
    `"Estorno " + descricao_limpa`. Regra que aponta para categoria inexistente na
    planilha é ignorada (o item segue para o LLM).
    """
    texto = transacao.estorno_de or transacao.descricao_original
    regra = primeira_regra(regras, texto, transacao.origem)
    if regra is None:
        return False
    if categorias_validas and regra.categoria not in categorias_validas:
        return False
    transacao.categoria = regra.categoria
    transacao.descricao = (
        f"Estorno {regra.descricao_limpa}"
        if transacao.estorno_de
        else regra.descricao_limpa
    )
    transacao.revisado = True
    transacao.confianca = 1.0
    transacao.via = "regra"
    return True
