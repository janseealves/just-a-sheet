"""Linha de comando: run | once | parse."""

import argparse
import logging
import sys
import time
from pathlib import Path

from just_a_sheet.models import ArquivoParseado, ResultadoImportacao, TipoArquivo
from just_a_sheet.parsers import detectar_tipo, parse_arquivo

log = logging.getLogger("just_a_sheet")


def _construir_pipeline():
    from just_a_sheet.config import Settings
    from just_a_sheet.drive import DriveGoogle
    from just_a_sheet.llm import ClassificadorLLM
    from just_a_sheet.pipeline import Pipeline
    from just_a_sheet.sheet import PlanilhaGspread

    s = Settings()  # type: ignore[call-arg]  # vem do ambiente
    pipeline = Pipeline(
        planilha=PlanilhaGspread(s.spreadsheet_id, s.google_application_credentials),
        drive=DriveGoogle(
            s.drive_folder_id,
            s.google_application_credentials,
            s.archive_folder_id,
        ),
        llm=ClassificadorLLM.criar(s.llm_base_url, s.llm_api_key, s.llm_model),
        arquivar=bool(s.archive_folder_id),
    )
    return pipeline, s


def _resumo(r: ResultadoImportacao) -> str:
    return (
        f"{r.arquivo}: status={r.status} tipo={r.tipo_arquivo.value if r.tipo_arquivo else '-'} "
        f"lidas={r.linhas_lidas} novas={r.linhas_novas} regras={r.via_regras} "
        f"llm={r.via_llm}" + (f" erro={r.erro}" if r.erro else "")
    )


def cmd_run(_args: argparse.Namespace) -> int:
    pipeline, settings = _construir_pipeline()
    log.info("iniciando loop (a cada %s s)", settings.poll_seconds)
    while True:
        try:
            resultados = pipeline.ciclo()
            log.info("ciclo concluído: %d arquivo(s) processado(s)", len(resultados))
            for r in resultados:
                log.info(_resumo(r))
        except Exception:
            log.exception("falha no ciclo; tentando de novo no próximo")
        time.sleep(settings.poll_seconds)


def cmd_once(args: argparse.Namespace) -> int:
    pipeline, _ = _construir_pipeline()
    resultados = pipeline.ciclo(dry_run=args.dry_run)
    if not resultados:
        print("Nenhum arquivo novo.")
    for r in resultados:
        print(_resumo(r))
        if args.dry_run:
            print(f"  já existentes na planilha: {r.ja_existentes}")
            print(f"  seriam novas: {r.linhas_novas}")
            if r.arquivaria_em:
                print(f"  arquivaria em {r.arquivaria_em}")
            for t in r.novas:
                print(
                    f"  {t.data} {t.valor:>10} {t.categoria:<24} "
                    f"conf={t.confianca:.2f} rev={t.revisado} | {t.descricao}"
                )
    return 1 if any(r.status == "erro" for r in resultados) else 0


def _imprimir_parse(parseado: ArquivoParseado) -> None:
    for t in parseado.transacoes:
        parcela = f" [{t.parcela}]" if t.parcela else ""
        print(f"{t.data} {t.mes_ref:%Y-%m} {t.valor:>12} {t.id} {t.descricao}{parcela}")
    entradas = sum((t.valor for t in parseado.transacoes if t.valor > 0), 0)
    saidas = sum((t.valor for t in parseado.transacoes if t.valor < 0), 0)
    print(f"\n{len(parseado.transacoes)} transações")
    print(f"entradas: {entradas}  saídas: {saidas}")
    if parseado.total_fatura is not None:
        print(f"total da fatura: {parseado.total_fatura}  mes_ref: {parseado.mes_ref}")


def cmd_parse(args: argparse.Namespace) -> int:
    caminho = Path(args.arquivo)
    conteudo = caminho.read_bytes()
    tipo, motivo = detectar_tipo(caminho.name, conteudo)
    if tipo == TipoArquivo.IGNORADO:
        print(f"arquivo ignorado: {motivo}", file=sys.stderr)
        return 2
    try:
        parseado = parse_arquivo(tipo, caminho.name, conteudo, args.fechamento)
    except ValueError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1
    print(f"tipo: {tipo.value}")
    _imprimir_parse(parseado)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="just-a-sheet")
    sub = parser.add_subparsers(dest="comando", required=True)

    sub.add_parser("run", help="loop infinito de importação").set_defaults(func=cmd_run)

    once = sub.add_parser("once", help="executa um ciclo")
    once.add_argument("--dry-run", action="store_true", help="não escreve nada")
    once.set_defaults(func=cmd_once)

    parse = sub.add_parser("parse", help="só o parser, em arquivo local")
    parse.add_argument("arquivo")
    parse.add_argument("--fechamento", type=int, default=9)
    parse.set_defaults(func=cmd_parse)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
