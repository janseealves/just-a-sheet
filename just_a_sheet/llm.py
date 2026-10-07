"""Classificação por LLM (API compatível com OpenAI) do que as regras não resolveram."""

import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any

import openai
from pydantic import BaseModel, ValidationError

from just_a_sheet.models import (
    Categoria,
    ItemClassificado,
    ItemLLM,
    ResultadoLLM,
)

log = logging.getLogger(__name__)

TAMANHO_LOTE = 50
TIMEOUT_SEGUNDOS = 120
NOVAS_TENTATIVAS = 2
CATEGORIA_PADRAO = "Outros"
MAX_EXEMPLOS = 30

_ERROS_TEMPORARIOS = (
    openai.APIConnectionError,  # inclui APITimeoutError
    openai.RateLimitError,
    openai.InternalServerError,
)


class LLMIndisponivel(Exception):
    """O provedor falhou (rede, limite, timeout ou resposta inutilizável)."""


class _Saida(BaseModel):
    itens: list[dict[str, Any]]


class _Item(BaseModel):
    indice: int
    categoria: str | None = None
    descricao_limpa: str = ""
    confianca: float = 0.0


def montar_prompt(
    itens: list[ItemLLM],
    categorias: list[Categoria],
    exemplos: list[tuple[str, str]],
    descrever_schema: bool,
) -> list[dict[str, str]]:
    linhas_cat = "\n".join(
        f"- {c.nome} (tipo: {c.tipo or 'n/d'})" + (f": {c.dica}" if c.dica else "")
        for c in categorias
    )
    partes = [
        "Você classifica lançamentos financeiros pessoais (Brasil) em categorias.",
        "Valor positivo = entrada; negativo = saída.",
        f"Categorias permitidas (use exatamente um nome):\n{linhas_cat}",
    ]
    if exemplos:
        ex = "\n".join(f"- {d} -> {c}" for d, c in exemplos[:MAX_EXEMPLOS])
        partes.append(f"Exemplos já revisados por uma pessoa:\n{ex}")
    partes.append(
        "Para cada item devolva: indice (o mesmo recebido), categoria (nome exato da "
        "lista), descricao_limpa (curta e legível, sem códigos) e confianca (0 a 1)."
    )
    if descrever_schema:
        partes.append(
            'Responda SOMENTE com JSON no formato {"itens": [{"indice": 0, '
            '"categoria": "...", "descricao_limpa": "...", "confianca": 0.9}]}.'
        )
    usuario = json.dumps([i.model_dump() for i in itens], ensure_ascii=False)
    return [
        {"role": "system", "content": "\n\n".join(partes)},
        {"role": "user", "content": f"Classifique estes itens:\n{usuario}"},
    ]


_RE_CERCA = re.compile(r"^\s*```[\w-]*\s*\n(.*?)\n?```\s*$", re.DOTALL)


def extrair_json(bruto: str) -> str:
    """Tira a cerca markdown (```json ... ```) que alguns modelos põem na resposta."""
    achou = _RE_CERCA.match(bruto)
    return (achou[1] if achou else bruto).strip()


class ClassificadorLLM:
    def __init__(
        self,
        client: Any,
        model: str,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.client = client
        self.model = model
        self._sleep = sleep

    @classmethod
    def criar(cls, base_url: str, api_key: str, model: str) -> "ClassificadorLLM":
        client = openai.OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=TIMEOUT_SEGUNDOS,
            max_retries=0,  # as tentativas são controladas aqui
        )
        return cls(client, model)

    # -- chamada ao provedor -------------------------------------------------

    def _criar(self, mensagens: list[dict[str, str]], response_format: dict) -> str:
        resposta = self.client.chat.completions.create(
            model=self.model,
            messages=mensagens,
            response_format=response_format,
            temperature=0,
        )
        return resposta.choices[0].message.content or ""

    def _chamar(
        self,
        itens: list[ItemLLM],
        categorias: list[Categoria],
        exemplos: list[tuple[str, str]],
    ) -> str:
        # Só json_object: alguns provedores (ex.: Ollama Cloud) aceitam json_schema
        # com HTTP 200 mas ignoram o schema. O formato vai descrito no prompt.
        return self._criar(
            montar_prompt(itens, categorias, exemplos, descrever_schema=True),
            {"type": "json_object"},
        )

    def _chamar_com_retentativas(self, *args: Any) -> str:
        for tentativa in range(NOVAS_TENTATIVAS + 1):
            try:
                return self._chamar(*args)
            except _ERROS_TEMPORARIOS as exc:
                if tentativa == NOVAS_TENTATIVAS:
                    raise LLMIndisponivel(type(exc).__name__) from exc
                self._sleep(2 ** (tentativa + 1))  # backoff: 2 s, 4 s
            except openai.APIError as exc:
                raise LLMIndisponivel(type(exc).__name__) from exc
        raise LLMIndisponivel("sem resposta")  # inalcançável

    # -- API pública ---------------------------------------------------------

    def classificar(
        self,
        itens: list[ItemLLM],
        categorias: list[Categoria],
        exemplos: list[tuple[str, str]] | None = None,
    ) -> ResultadoLLM:
        exemplos = exemplos or []
        nomes = {c.nome for c in categorias}
        resultado = ResultadoLLM()
        for inicio in range(0, len(itens), TAMANHO_LOTE):
            lote = itens[inicio : inicio + TAMANHO_LOTE]
            try:
                bruto = self._chamar_com_retentativas(lote, categorias, exemplos)
                saida = _Saida.model_validate_json(extrair_json(bruto))
            except (LLMIndisponivel, ValidationError) as exc:
                log.warning("LLM indisponível para um lote (%s)", type(exc).__name__)
                resultado.falhas += len(lote)
                resultado.itens.extend(
                    ItemClassificado(indice=i.indice, categoria=CATEGORIA_PADRAO)
                    for i in lote
                )
                continue
            resultado.itens.extend(_validar_lote(lote, saida, nomes))
        return resultado


def _validar_lote(
    lote: list[ItemLLM], saida: _Saida, nomes: set[str]
) -> list[ItemClassificado]:
    """Valida item a item; ausente/inválido/categoria fora da lista -> Outros/0."""
    por_indice: dict[int, _Item] = {}
    for bruto in saida.itens:
        try:
            item = _Item.model_validate(bruto)
        except ValidationError:
            continue
        por_indice[item.indice] = item

    resultado = []
    for original in lote:
        item = por_indice.get(original.indice)
        if item is None or item.categoria not in nomes:
            resultado.append(
                ItemClassificado(indice=original.indice, categoria=CATEGORIA_PADRAO)
            )
            continue
        resultado.append(
            ItemClassificado(
                indice=original.indice,
                categoria=item.categoria,
                descricao_limpa=item.descricao_limpa.strip(),
                confianca=min(1.0, max(0.0, item.confianca)),
            )
        )
    return resultado
