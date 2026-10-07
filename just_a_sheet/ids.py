"""Identificadores determinísticos (12 hex) usados para deduplicar lançamentos."""

import hashlib
from datetime import date
from decimal import Decimal


def _valor_2casas(valor: Decimal | float | str) -> str:
    return f"{Decimal(str(valor)).quantize(Decimal('0.01')):.2f}"


def _sha12(chave: str) -> str:
    return hashlib.sha1(chave.encode("utf-8")).hexdigest()[:12]


def make_id_nubank(data: date, title: str, valor: Decimal | float | str, n: int) -> str:
    """`valor` já com o sinal da planilha; `n` = ocorrência da tupla no arquivo."""
    return _sha12(f"nubank|{data.isoformat()}|{title}|{_valor_2casas(valor)}|{n}")


def make_id_bradesco(data: date, docto: int | str, valor: Decimal | float | str) -> str:
    """`docto` sem zeros à esquerda; `valor` com sinal."""
    return _sha12(f"bradesco|{data.isoformat()}|{int(docto)}|{_valor_2casas(valor)}")
