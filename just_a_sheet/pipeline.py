"""Orquestra um ciclo de importação (Drive -> parse -> classificação -> planilha)."""

import logging
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

from just_a_sheet.llm import CATEGORIA_PADRAO, MAX_EXEMPLOS
from just_a_sheet.models import (
    ArquivoDrive,
    ArquivoParseado,
    Categoria,
    ConfigPlanilha,
    ItemLLM,
    LancamentoExistente,
    Regra,
    ResultadoImportacao,
    ResultadoLLM,
    TipoArquivo,
    Transacao,
)
from just_a_sheet.parsers import detectar_tipo, parse_arquivo
from just_a_sheet.reconcile import conciliar, totais_da_planilha
from just_a_sheet.rules import aplicar_regras

log = logging.getLogger(__name__)


class Planilha(Protocol):
    def ler_config(self) -> ConfigPlanilha: ...
    def ler_categorias(self) -> list[Categoria]: ...
    def ler_regras(self) -> list[Regra]: ...
    def ler_importacoes(self) -> set[tuple[str, str]]: ...
    def ler_lancamentos(self) -> list[LancamentoExistente]: ...
    def append_lancamentos(self, linhas: list[list]) -> None: ...
    def append_importacao(self, linha: list) -> None: ...


class Drive(Protocol):
    def listar(self) -> list[ArquivoDrive]: ...
    def baixar(self, file_id: str) -> bytes: ...


class LLM(Protocol):
    def classificar(
        self,
        itens: list[ItemLLM],
        categorias: list[Categoria],
        exemplos: list[tuple[str, str]],
    ) -> ResultadoLLM: ...


def _agora_padrao() -> datetime:
    return datetime.now(ZoneInfo("America/Sao_Paulo"))


class Pipeline:
    def __init__(
        self,
        planilha: Planilha,
        drive: Drive,
        llm: LLM,
        agora: Callable[[], datetime] = _agora_padrao,
    ):
        self.planilha = planilha
        self.drive = drive
        self.llm = llm
        self.agora = agora

    # ------------------------------------------------------------------ ciclo

    def ciclo(self, dry_run: bool = False) -> list[ResultadoImportacao]:
        config = self.planilha.ler_config()
        categorias = self.planilha.ler_categorias()
        regras = self.planilha.ler_regras()
        processados = self.planilha.ler_importacoes()
        existentes = self.planilha.ler_lancamentos()

        ids_existentes = {lanc.id for lanc in existentes}
        exemplos = [
            (lanc.descricao_original, lanc.categoria)
            for lanc in existentes
            if lanc.revisado and lanc.descricao_original and lanc.categoria
        ][-MAX_EXEMPLOS:]

        pendentes = [
            a
            for a in sorted(self.drive.listar(), key=lambda a: a.nome)
            if not (a.md5 and (a.id, a.md5) in processados)
        ]

        resultados: list[ResultadoImportacao] = []
        parseados: list[tuple[ResultadoImportacao, ArquivoParseado]] = []

        # Fase 1: baixar, detectar e parsear (erros ficam isolados por arquivo).
        for arq in pendentes:
            res = ResultadoImportacao(
                arquivo=arq.nome, drive_file_id=arq.id, md5=arq.md5
            )
            try:
                conteudo = self.drive.baixar(arq.id)
                tipo, motivo = detectar_tipo(arq.nome, conteudo)
                res.tipo_arquivo = tipo
                if tipo == TipoArquivo.IGNORADO:
                    res.status = "ignorado"
                    res.erro = motivo
                    self._finalizar(res, dry_run)
                    resultados.append(res)
                    continue
                parseado = parse_arquivo(
                    tipo, arq.nome, conteudo, config.dia_fechamento_nubank
                )
                res.linhas_lidas = len(parseado.transacoes)
                parseados.append((res, parseado))
            except Exception as exc:
                self._marcar_erro(res, exc)
                self._finalizar(res, dry_run)
                resultados.append(res)

        # Fase 2: totais de fatura (lote atual + planilha) para a conciliação.
        totais: list[Decimal] = totais_da_planilha(existentes)
        totais += [p.total_fatura for _, p in parseados if p.total_fatura is not None]

        # Fase 3: filtrar, classificar e gravar cada arquivo.
        for res, parseado in parseados:
            try:
                self._processar(
                    res, parseado, config, categorias, regras, totais,
                    exemplos, ids_existentes, dry_run,
                )  # fmt: skip
            except Exception as exc:
                self._marcar_erro(res, exc)
                res.novas = []
                res.linhas_novas = 0
            self._finalizar(res, dry_run)
            resultados.append(res)
        return resultados

    # ---------------------------------------------------------------- partes

    def _processar(
        self,
        res: ResultadoImportacao,
        parseado: ArquivoParseado,
        config: ConfigPlanilha,
        categorias: list[Categoria],
        regras: list[Regra],
        totais: list[Decimal],
        exemplos: list[tuple[str, str]],
        ids_existentes: set[str],
        dry_run: bool,
    ) -> None:
        novas: list[Transacao] = []
        vistos: set[str] = set()
        for t in parseado.transacoes:
            if t.mes_ref < config.inicio_dados:
                continue
            if t.id in ids_existentes or t.id in vistos:
                res.ja_existentes += 1
                continue
            vistos.add(t.id)
            novas.append(t)

        nomes = {c.nome for c in categorias}
        for t in novas:
            aplicar_regras(t, regras, nomes)
        conciliar(novas, totais)

        sobra = [t for t in novas if not t.categoria]
        if sobra:
            self._classificar_llm(res, sobra, categorias, exemplos)

        res.novas = novas
        res.linhas_novas = len(novas)
        res.via_regras = sum(t.via in ("regra", "conciliacao") for t in novas)
        res.via_llm = len(sobra)

        if not dry_run and novas:
            self.planilha.append_lancamentos([t.para_linha() for t in novas])
        ids_existentes.update(t.id for t in novas)

    def _classificar_llm(
        self,
        res: ResultadoImportacao,
        sobra: list[Transacao],
        categorias: list[Categoria],
        exemplos: list[tuple[str, str]],
    ) -> None:
        itens = [
            ItemLLM(
                indice=i,
                descricao_original=t.descricao_original,
                valor=float(t.valor),
                origem=t.origem,
            )
            for i, t in enumerate(sobra)
        ]
        try:
            retorno = self.llm.classificar(itens, categorias, exemplos)
        except Exception as exc:
            log.warning("LLM indisponível (%s)", type(exc).__name__)
            retorno = ResultadoLLM(falhas=len(sobra))
        por_indice = {i.indice: i for i in retorno.itens}
        for i, t in enumerate(sobra):
            item = por_indice.get(i)
            t.via = "llm"
            t.revisado = False
            if item is None:
                t.categoria, t.confianca = CATEGORIA_PADRAO, 0.0
                continue
            t.categoria = item.categoria
            t.confianca = item.confianca
            if item.descricao_limpa:
                t.descricao = (
                    f"Estorno {item.descricao_limpa}"
                    if t.estorno_de
                    and not item.descricao_limpa.lower().startswith("estorno")
                    else item.descricao_limpa
                )
        if retorno.falhas:
            res.erro = (
                f"LLM indisponível; {retorno.falhas} lançamentos ficaram em "
                f"{CATEGORIA_PADRAO}"
            )

    # ----------------------------------------------------------------- saída

    @staticmethod
    def _marcar_erro(res: ResultadoImportacao, exc: Exception) -> None:
        # Mensagens dos parsers não carregam linhas de cabeçalho (dados pessoais).
        res.status = "erro"
        res.erro = (str(exc) or type(exc).__name__)[:300]
        log.error("erro ao processar %s: %s", res.arquivo, res.erro)

    def _finalizar(self, res: ResultadoImportacao, dry_run: bool) -> None:
        if dry_run:
            return
        try:
            self.planilha.append_importacao(
                [
                    res.arquivo,
                    res.drive_file_id,
                    res.md5,
                    res.tipo_arquivo.value if res.tipo_arquivo else "",
                    self.agora().strftime("%Y-%m-%d %H:%M"),
                    res.linhas_lidas,
                    res.linhas_novas,
                    res.via_regras,
                    res.via_llm,
                    res.status,
                    res.erro,
                ]
            )
        except Exception:
            log.exception("falha ao registrar %s em Importações", res.arquivo)
