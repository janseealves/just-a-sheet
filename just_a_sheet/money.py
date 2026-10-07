"""Conversão de valores monetários no formato pt-BR."""

from decimal import Decimal, InvalidOperation


def parse_valor_ptbr(texto: str) -> Decimal:
    """Converte `"12,50"`, `"- 2.870,40"` ou `"1.000,00"` em Decimal.

    Remove espaços e o `.` de milhar, troca `,` por `.`. O sinal `-` (possivelmente
    separado por espaço) é preservado.
    """
    limpo = "".join(texto.split()).replace(".", "").replace(",", ".")
    try:
        return Decimal(limpo)
    except InvalidOperation as exc:
        raise ValueError(f"valor monetário inválido: {texto!r}") from exc
