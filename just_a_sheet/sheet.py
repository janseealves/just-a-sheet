"""Leitura e escrita na planilha Google Sheets (gspread).

O importador só LÊ Categorias, Regras e Config e só faz APPEND em Lançamentos
(A:K) e Importações (A:K). Nunca escreve em L:M (fórmulas da planilha).
"""

import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import gspread
from gspread.utils import InsertDataOption, ValueInputOption, ValueRenderOption

from just_a_sheet.models import (
    Categoria,
    ConfigPlanilha,
    LancamentoExistente,
    Regra,
)

ESCOPOS = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]
ABA_LANCAMENTOS = "Lançamentos"
ABA_IMPORTACOES = "Importações"
ABA_CATEGORIAS = "Categorias"
ABA_REGRAS = "Regras"
ABA_CONFIG = "Config"

_MESES = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}  # fmt: skip
_EPOCH_SHEETS = date(1899, 12, 30)


def serial_para_data(serial: float) -> date:
    return _EPOCH_SHEETS + timedelta(days=int(serial))


def para_data(valor: Any) -> date | None:
    """Converte serial do Sheets, ISO, dd/mm/aaaa, mm/aaaa ou `set./2026`."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int | float):
        return serial_para_data(valor)
    texto = unicodedata.normalize("NFKD", str(valor)).strip().lower()
    if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", texto):
        return date(int(m[1]), int(m[2]), int(m[3]))
    if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", texto):
        return date(int(m[3]), int(m[2]), int(m[1]))
    if m := re.fullmatch(r"(\d{1,2})/(\d{4})", texto):
        return date(int(m[2]), int(m[1]), 1)
    m = re.fullmatch(r"([a-z]{3})[a-z]*\.?\s*(?:/|de)?\s*(\d{4})", texto)
    if m and m[1] in _MESES:
        return date(int(m[2]), _MESES[m[1]], 1)
    raise ValueError(f"data não reconhecida na planilha: {valor!r}")


def para_decimal(valor: Any) -> Decimal:
    if valor in (None, ""):
        return Decimal(0)
    if isinstance(valor, int | float):
        return Decimal(str(valor))
    return Decimal(str(valor).replace(".", "").replace(",", "."))


def _celula(linha: list, i: int) -> Any:
    return linha[i] if i < len(linha) else ""


def _texto(linha: list, i: int) -> str:
    return str(_celula(linha, i)).strip()


def _booleano(valor: Any) -> bool:
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().upper() in {"TRUE", "VERDADEIRO", "1", "SIM"}


def parse_config(linhas: list[list]) -> ConfigPlanilha:
    pares = {_texto(linha, 0): _celula(linha, 1) for linha in linhas if linha}
    try:
        inicio = para_data(pares["inicio_dados"])
        if inicio is None:
            raise KeyError("inicio_dados")
        return ConfigPlanilha(
            dia_fechamento_nubank=int(pares["dia_fechamento_nubank"]),
            dia_vencimento_nubank=int(pares["dia_vencimento_nubank"]),
            inicio_dados=inicio.replace(day=1),
        )
    except KeyError as exc:
        raise ValueError(f"parâmetro ausente na aba Config: {exc.args[0]}") from exc


class PlanilhaGspread:
    def __init__(self, spreadsheet_id: str, credenciais: str):
        cliente = gspread.service_account(filename=credenciais, scopes=ESCOPOS)
        self._sh = cliente.open_by_key(spreadsheet_id)

    def _ler(self, aba: str, intervalo: str) -> list[list]:
        return self._sh.worksheet(aba).get(
            intervalo, value_render_option=ValueRenderOption.unformatted
        )

    def ler_config(self) -> ConfigPlanilha:
        return parse_config(self._ler(ABA_CONFIG, "A2:B"))

    def ler_categorias(self) -> list[Categoria]:
        return [
            Categoria(
                nome=_texto(r, 0),
                tipo=_texto(r, 1),
                grupo=_texto(r, 2),
                orcamento_mensal=_texto(r, 3),
                dica=_texto(r, 4),
            )
            for r in self._ler(ABA_CATEGORIAS, "A2:E")
            if _texto(r, 0)
        ]

    def ler_regras(self) -> list[Regra]:
        return [
            Regra(
                padrao=_texto(r, 0),
                categoria=_texto(r, 1),
                descricao_limpa=_texto(r, 2),
                origem=_texto(r, 3),
            )
            for r in self._ler(ABA_REGRAS, "A2:D")
            if _texto(r, 0)
        ]

    def ler_importacoes(self) -> set[tuple[str, str]]:
        """Pares (drive_file_id, md5) já processados."""
        return {
            (_texto(r, 1), _texto(r, 2))
            for r in self._ler(ABA_IMPORTACOES, "A2:K")
            if _texto(r, 1)
        }

    def ler_lancamentos(self) -> list[LancamentoExistente]:
        resultado = []
        for r in self._ler(ABA_LANCAMENTOS, "A2:K"):
            id_ = _texto(r, 10)
            if not id_:
                continue
            resultado.append(
                LancamentoExistente(
                    id=id_,
                    mes_ref=para_data(_celula(r, 1)),
                    valor=para_decimal(_celula(r, 3)),
                    categoria=_texto(r, 4),
                    origem=_texto(r, 5),
                    revisado=_booleano(_celula(r, 7)),
                    descricao_original=_texto(r, 9),
                )
            )
        return resultado

    def _append(self, aba: str, linhas: list[list]) -> None:
        if not linhas:
            return
        self._sh.worksheet(aba).append_rows(
            linhas,
            value_input_option=ValueInputOption.user_entered,
            insert_data_option=InsertDataOption.overwrite,
            table_range="A:K",
        )

    def append_lancamentos(self, linhas: list[list]) -> None:
        self._append(ABA_LANCAMENTOS, linhas)

    def append_importacao(self, linha: list) -> None:
        self._append(ABA_IMPORTACOES, [linha])
