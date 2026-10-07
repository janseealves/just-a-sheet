from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from just_a_sheet.ids import make_id_nubank
from just_a_sheet.models import (
    ArquivoDrive,
    Categoria,
    ConfigPlanilha,
    ItemClassificado,
    LancamentoExistente,
    Regra,
    ResultadoLLM,
)
from just_a_sheet.pipeline import Pipeline
from tests.conftest import make_pdf

CATEGORIAS = [
    Categoria(nome=n, tipo="despesa")
    for n in (
        "Alimentação", "Compras", "Impostos", "Moradia", "Pagamento de fatura", "Outros",
    )
]  # fmt: skip
REGRAS = [
    Regra(padrao="Padaria Exemplo", categoria="Alimentação", descricao_limpa="Padaria"),
    Regra(padrao="Pagamento recebido", categoria="Pagamento de fatura",
          descricao_limpa="Pagamento da fatura"),
    Regra(padrao="IOF", categoria="Impostos", descricao_limpa="IOF"),
    Regra(padrao="Mercado Online", categoria="Alimentação",
          descricao_limpa="Mercado online"),
]  # fmt: skip
AGORA = datetime(2026, 2, 20, 10, 30, tzinfo=ZoneInfo("America/Sao_Paulo"))


class FakePlanilha:
    def __init__(self, inicio=date(2026, 1, 1), existentes=None, processados=None):
        self.inicio = inicio
        self.existentes = existentes or []
        self.processados = processados or set()
        self.lancamentos: list[list] = []
        self.importacoes: list[list] = []

    def ler_config(self):
        return ConfigPlanilha(
            dia_fechamento_nubank=9, dia_vencimento_nubank=16, inicio_dados=self.inicio
        )

    def ler_categorias(self):
        return CATEGORIAS

    def ler_regras(self):
        return REGRAS

    def ler_importacoes(self):
        return self.processados

    def ler_lancamentos(self):
        return self.existentes

    def append_lancamentos(self, linhas):
        self.lancamentos += linhas

    def append_importacao(self, linha):
        self.importacoes.append(linha)


class FakeDrive:
    def __init__(self, arquivos: dict[str, bytes]):
        self.arquivos = arquivos
        self.baixados: list[str] = []

    def listar(self):
        return [
            ArquivoDrive(id=f"id-{n}", nome=n, md5=f"md5-{n}") for n in self.arquivos
        ]

    def baixar(self, file_id):
        self.baixados.append(file_id)
        return self.arquivos[file_id.removeprefix("id-")]


class FakeLLM:
    def __init__(self, falhas=0):
        self.chamadas = []
        self.falhas = falhas

    def classificar(self, itens, categorias, exemplos):
        self.chamadas.append((itens, exemplos))
        return ResultadoLLM(
            itens=[
                ItemClassificado(
                    indice=i.indice, categoria="Compras",
                    descricao_limpa="Compra x", confianca=0.8,
                )
                for i in itens
            ],
            falhas=self.falhas,
        )  # fmt: skip


class LLMQuebrado:
    def classificar(self, itens, categorias, exemplos):
        raise RuntimeError("sem rede")


def _pipe(planilha, arquivos, llm=None):
    drive = FakeDrive(arquivos)
    return Pipeline(planilha, drive, llm or FakeLLM(), agora=lambda: AGORA), drive


def test_fluxo_nubank(csv_nubank):
    planilha = FakePlanilha()
    llm = FakeLLM()
    pipe, _ = _pipe(planilha, {"nubank_2026-02-16.csv": csv_nubank}, llm)
    [res] = pipe.ciclo()

    assert (res.status, res.erro) == ("ok", "")
    assert (res.linhas_lidas, res.linhas_novas) == (7, 7)
    assert res.via_regras == 5  # 2 padarias, IOF, estorno, pagamento recebido
    assert res.via_llm == 2  # Loja Gringa Sub e a de roupas
    assert len(llm.chamadas[0][0]) == 2
    # só A:K
    assert {len(linha) for linha in planilha.lancamentos} == {11}
    por_desc = {linha[9]: linha for linha in planilha.lancamentos}
    estorno = por_desc['Estorno de "Mercado Online" (Mercado)']
    assert estorno[2] == "Estorno Mercado online"  # herdou a categoria de X
    assert estorno[3] == 45.10 and estorno[4] == "Alimentação"
    assert estorno[7] is True and estorno[8] == 1.0
    roupa = por_desc["Loja de Roupas - Parcela 2/6"]
    assert roupa[6] == "'2/6" and roupa[7] is False and roupa[8] == 0.8
    assert roupa[1] == "2026-02-01" and roupa[0] == "2026-01-01"
    assert roupa[5] == "nubank"
    # Importações
    assert planilha.importacoes == [
        [
            "nubank_2026-02-16.csv", "id-nubank_2026-02-16.csv",
            "md5-nubank_2026-02-16.csv", "fatura_nubank", "2026-02-20 10:30",
            7, 7, 5, 2, "ok", "",
        ]
    ]  # fmt: skip


def test_arquivo_ja_processado_e_pulado(csv_nubank):
    nome = "nubank_2026-02-16.csv"
    planilha = FakePlanilha(processados={(f"id-{nome}", f"md5-{nome}")})
    pipe, drive = _pipe(planilha, {nome: csv_nubank})
    assert pipe.ciclo() == []
    assert drive.baixados == []
    assert planilha.lancamentos == [] and planilha.importacoes == []


def test_md5_diferente_reprocessa_sem_duplicar(csv_nubank):
    nome = "nubank_2026-02-16.csv"
    planilha = FakePlanilha(processados={(f"id-{nome}", "md5-antigo")})
    pipe, _ = _pipe(planilha, {nome: csv_nubank})
    [r1] = pipe.ciclo()
    assert r1.linhas_novas == 7
    # segundo ciclo: planilha "viu" os lançamentos -> nada é regravado
    planilha.existentes = [
        LancamentoExistente(id=linha[10]) for linha in planilha.lancamentos
    ]
    planilha.lancamentos.clear()
    [r2] = pipe.ciclo()
    assert (r2.linhas_novas, r2.ja_existentes) == (0, 7)
    assert planilha.lancamentos == []


def test_ids_existentes_nao_sao_regravados(csv_nubank):
    existente = make_id_nubank(
        date(2026, 1, 12), "Padaria Exemplo", Decimal("-12.50"), 1
    )
    planilha = FakePlanilha(existentes=[LancamentoExistente(id=existente)])
    pipe, _ = _pipe(planilha, {"nubank_2026-02-16.csv": csv_nubank})
    [res] = pipe.ciclo()
    assert (res.linhas_novas, res.ja_existentes) == (6, 1)
    assert existente not in {linha[10] for linha in planilha.lancamentos}


def test_mes_ref_antes_do_inicio_dados_e_descartado(csv_nubank):
    planilha = FakePlanilha(inicio=date(2026, 3, 1))  # fatura é de fev/2026
    pipe, _ = _pipe(planilha, {"nubank_2026-02-16.csv": csv_nubank})
    [res] = pipe.ciclo()
    assert res.linhas_lidas == 7 and res.linhas_novas == 0
    assert planilha.lancamentos == []
    assert planilha.importacoes[0][9] == "ok"


def test_csv_bradesco_vira_ignorado():
    planilha = FakePlanilha()
    pipe, _ = _pipe(planilha, {"extrato.csv": b"Extrato de: Ag: 0;Conta: 0\n1;2\n"})
    [res] = pipe.ciclo()
    assert res.status == "ignorado"
    assert planilha.lancamentos == []
    assert planilha.importacoes[0][9] == "ignorado"
    assert "envie o PDF" in planilha.importacoes[0][10]


def test_dry_run_nao_escreve(csv_nubank):
    planilha = FakePlanilha()
    llm = FakeLLM()
    pipe, _ = _pipe(planilha, {"nubank_2026-02-16.csv": csv_nubank}, llm)
    [res] = pipe.ciclo(dry_run=True)
    assert res.linhas_novas == 7 and len(res.novas) == 7
    assert llm.chamadas  # o LLM roda no dry-run
    assert planilha.lancamentos == [] and planilha.importacoes == []


def _pdf_bradesco(valor_debito="139,90", saldo="860,10") -> bytes:
    return make_pdf(
        [
            "Bradesco Celular",
            "Extrato de: Agencia: 0000 | Conta: 0000000-0",
            "Data Histórico Docto. Crédito (R$) Débito (R$) Saldo (R$)",
            "31/01/2026 COD. LANC. 0 0,00 1.000,00",
            "05/02/2026 PAGTO ELETRON COBRANCA",
            f"NUBANK 0000099 {valor_debito} {saldo}",
            "06/02/2026 PIX ENVIADO DES: FULANO DE TAL 06/02 1000009 20,10 840,00",
            "Total 0,00 160,00 840,00",
        ]
    )


def test_conciliacao_no_mesmo_lote_independe_da_ordem(csv_nubank):
    planilha = FakePlanilha()
    # "a_" (Bradesco) é processado antes de "b_" (Nubank): o total vem do lote.
    pipe, _ = _pipe(
        planilha,
        {"a_extrato.pdf": _pdf_bradesco(), "b_nubank_2026-02-16.csv": csv_nubank},
    )
    res_a, res_b = pipe.ciclo()
    assert (res_a.status, res_a.erro) == ("ok", "")
    pagamento = next(
        n for n in res_a.novas if n.origem == "bradesco" and n.valor < -100
    )
    assert pagamento.categoria == "Pagamento de fatura"
    assert pagamento.descricao == "Pagamento da fatura Nubank"
    assert pagamento.revisado is True
    pix = next(n for n in res_a.novas if "FULANO" in n.descricao_original)
    assert pix.via == "llm" and pix.revisado is False
    assert res_a.via_regras == 1 and res_a.via_llm == 1
    assert res_b.status == "ok"
    assert {len(linha) for linha in planilha.lancamentos} == {11}


def test_erro_de_um_arquivo_nao_derruba_os_outros(csv_nubank):
    planilha = FakePlanilha()
    # saldo não fecha (139,90 debitado, mas saldo cai 100,00)
    quebrado = _pdf_bradesco(saldo="900,00")
    pipe, _ = _pipe(
        planilha,
        {"a_extrato.pdf": quebrado, "b_nubank_2026-02-16.csv": csv_nubank},
    )
    res_a, res_b = pipe.ciclo()
    assert res_a.status == "erro" and "saldo não fecha" in res_a.erro
    assert "Agencia" not in res_a.erro
    assert res_b.status == "ok" and res_b.linhas_novas == 7
    assert [i[9] for i in planilha.importacoes] == ["erro", "ok"]
    assert all(linha[5] == "nubank" for linha in planilha.lancamentos)


def test_llm_indisponivel_grava_outros_e_registra_erro(csv_nubank):
    planilha = FakePlanilha()
    pipe, _ = _pipe(planilha, {"nubank_2026-02-16.csv": csv_nubank}, LLMQuebrado())
    [res] = pipe.ciclo()
    assert res.status == "ok"
    assert res.erro == "LLM indisponível; 2 lançamentos ficaram em Outros"
    roupa = next(r for r in planilha.lancamentos if r[9].startswith("Loja de Roupas"))
    assert (roupa[4], roupa[7], roupa[8]) == ("Outros", False, 0.0)
    assert planilha.importacoes[0][10] == res.erro


def test_exemplos_few_shot_vem_dos_revisados(csv_nubank):
    existentes = [
        LancamentoExistente(
            id=f"e{i}",
            descricao_original=f"D{i}",
            categoria="Compras",
            revisado=i % 2 == 0,
        )
        for i in range(100)
    ]
    llm = FakeLLM()
    pipe, _ = _pipe(
        FakePlanilha(existentes=existentes), {"nubank_2026-02-16.csv": csv_nubank}, llm
    )
    pipe.ciclo(dry_run=True)
    exemplos = llm.chamadas[0][1]
    assert len(exemplos) == 30
    assert exemplos[-1] == ("D98", "Compras")


def test_arquivo_sem_md5_processado_se_id_ja_registrado():
    class DriveSemMd5(FakeDrive):
        def listar(self):
            return [ArquivoDrive(id="id-nativo", nome="Planilha.gsheet", md5="")]

    drive = DriveSemMd5({})
    # id já registrado (com qualquer md5): pulado, nem é baixado
    planilha = FakePlanilha(processados={("id-nativo", "qualquer")})
    assert Pipeline(planilha, drive, FakeLLM(), agora=lambda: AGORA).ciclo() == []
    assert drive.baixados == [] and planilha.importacoes == []
