"""Utilitários de teste. Todos os dados aqui são FICTÍCIOS."""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def texto_bradesco() -> str:
    return (FIXTURES / "bradesco_extrato.txt").read_text(encoding="utf-8")


@pytest.fixture
def csv_nubank() -> bytes:
    return (FIXTURES / "nubank_fatura.csv").read_bytes()


def make_pdf(linhas: list[str]) -> bytes:
    """Gera um PDF mínimo (uma linha de texto por linha) para testar o pdfplumber."""
    ops = []
    y = 780
    for linha in linhas:
        seguro = linha.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"BT /F1 6 Tf 1 0 0 1 20 {y} Tm ({seguro}) Tj ET")
        y -= 8
    stream = "\n".join(ops).encode("cp1252")
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 800 800] "
        b"/Contents 5 0 R /Resources << /Font << /F1 4 0 R >> >> >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>",
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    ]
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
