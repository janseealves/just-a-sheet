"""Utilitários de teste. Todos os dados aqui são FICTÍCIOS."""

from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

ALTURA_PAGINA = 842

# (x, top, texto, tamanho) de cada trecho de texto de uma página
Trecho = tuple[float, float, str, float]


@pytest.fixture
def csv_nubank() -> bytes:
    return (FIXTURES / "nubank_fatura.csv").read_bytes()


def _montar_pdf(paginas: list[list[Trecho]], largura: int = 595) -> bytes:
    """PDF mínimo (Helvetica) com cada trecho de texto na posição pedida."""
    # objetos: 1 catálogo, 2 páginas, 3 fonte; depois (página, conteúdo) por página
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(len(paginas)))
    objetos: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(paginas)} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>",
    ]
    for i, trechos in enumerate(paginas):
        ops = []
        for x, top, texto, tamanho in trechos:
            seguro = texto.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            y = ALTURA_PAGINA - top - tamanho
            ops.append(
                f"BT /F1 {tamanho} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm ({seguro}) Tj ET"
            )
        stream = "\n".join(ops).encode("cp1252")
        pagina = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {largura} {ALTURA_PAGINA}] "
            f"/Contents {5 + 2 * i} 0 R /Resources << /Font << /F1 3 0 R >> >> >>"
        )
        objetos.append(pagina.encode())
        objetos.append(
            b"<< /Length "
            + str(len(stream)).encode()
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )
    saida = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, corpo in enumerate(objetos, start=1):
        offsets.append(len(saida))
        saida += f"{i} 0 obj\n".encode() + corpo + b"\nendobj\n"
    xref = len(saida)
    saida += f"xref\n0 {len(objetos) + 1}\n".encode()
    saida += b"0000000000 65535 f \n"
    for off in offsets:
        saida += f"{off:010d} 00000 n \n".encode()
    saida += (
        f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(saida)


def make_pdf(linhas: list[str]) -> bytes:
    """PDF de uma página, uma linha de texto por linha (sem colunas)."""
    return _montar_pdf([[(20, 20 + 8 * i, linha, 6) for i, linha in enumerate(linhas)]])


# --- extrato Bradesco fictício, com o layout do "Bradesco Celular" -------------

TAMANHO = 8
X_DATA, X_HISTORICO, X_DOCTO = 46, 110, 303
X_CREDITO, X_DEBITO, X_SALDO = 427, 490, 550  # borda direita de cada coluna
PASSO = 30  # distância vertical entre lançamentos
# deslocamento da linha de valores em relação à 1ª linha do histórico, quando o
# lançamento tem duas linhas (a 2ª linha fica 9 pt abaixo da 1ª)
DESLOCAMENTO_VALORES = {"centro": 4.5, "baixo": 9.0, "topo": 0.0}


def _largura(texto: str) -> float:
    """Largura (pt) de um número em Helvetica 8 pt."""
    return (
        sum({".": 0.278, ",": 0.278, "-": 0.333}.get(c, 0.556) for c in texto) * TAMANHO
    )


def extrato_pagina(
    itens: list[tuple[Any, ...]],
    alinhamento: str = "centro",
    secao2: bool = False,
    folha: str = "1/1",
) -> list[Trecho]:
    """Monta os trechos de uma página do extrato.

    `itens`:
      ("cod", data, saldo)
      ("lanc", data|None, [linhas do histórico], docto, "c"|"d", valor, saldo)
      ("total", creditos, debitos, saldo)
      ("solto", texto)  # texto sem linha de valores (ex.: rodapé)
    `alinhamento`: onde ficam os valores nos lançamentos de duas linhas
    ("centro" entre as linhas, "baixo" na 2ª linha ou "topo" na 1ª).
    """
    t: list[Trecho] = [
        (200, 55, "Bradesco Celular", 10),
        (200, 80, "Data: 01/02/2026 - 10:00", TAMANHO),
        (
            46,
            123,
            "Extrato de: Agencia: 0000 | Conta: 0000000-0 | Titular: TITULAR FICTICIO",
            TAMANHO,
        ),
        (518, 123, f"Folha: {folha}", TAMANHO),
        (46, 155, "Data", TAMANHO),
        (111, 155, "Histórico", TAMANHO),
        (305, 155, "Docto.", TAMANHO),
        (385, 155, "Crédito (R$)", TAMANHO),
        (452, 155, "Débito (R$)", TAMANHO),
        (520, 155, "Saldo (R$)", TAMANHO),
    ]
    if secao2:
        t.append((191, 123, "Últimos Lancamentos", TAMANHO))

    def numero(texto: str, borda: float, top: float) -> Trecho:
        return (borda - _largura(texto), top, texto, TAMANHO)

    y = 191.0
    for item in itens:
        tipo = item[0]
        if tipo == "cod":
            _, data, saldo = item
            t += [
                (X_DATA, y, data, TAMANHO),
                (X_HISTORICO, y, "COD. LANC. 0", TAMANHO),
                numero(saldo, X_SALDO, y),
            ]
        elif tipo == "lanc":
            _, data, linhas, docto, cd, valor, saldo = item
            dy = DESLOCAMENTO_VALORES[alinhamento] if len(linhas) == 2 else 0.0
            for i, linha in enumerate(linhas):
                t.append((X_HISTORICO, y + 9 * i, linha, TAMANHO))
            if data:
                t.append((X_DATA, y + dy, data, TAMANHO))
            t.append((X_DOCTO, y + dy, docto, TAMANHO))
            t.append(numero(valor, X_CREDITO if cd == "c" else X_DEBITO, y + dy))
            t.append(numero(saldo, X_SALDO, y + dy))
        elif tipo == "total":
            _, cred, deb, saldo = item
            t += [
                (X_DATA, y, "Total", TAMANHO),
                numero(cred, X_CREDITO, y),
                numero(deb, X_DEBITO, y),
                numero(saldo, X_SALDO, y),
            ]
        elif tipo == "solto":
            t.append((X_HISTORICO, y, item[1], TAMANHO))
        else:  # pragma: no cover
            raise ValueError(tipo)
        y += PASSO
    return t


def make_extrato_pdf(paginas: list[dict[str, Any]]) -> bytes:
    """PDF de extrato; cada página é `{"itens": [...], **opções de extrato_pagina}`."""
    return _montar_pdf([extrato_pagina(**p) for p in paginas])
