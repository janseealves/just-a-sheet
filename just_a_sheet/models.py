"""Modelos de domínio compartilhados."""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field


class TipoArquivo(StrEnum):
    FATURA_NUBANK = "fatura_nubank"
    EXTRATO_BRADESCO = "extrato_bradesco"
    IGNORADO = "ignorado"


class Transacao(BaseModel):
    data: date
    mes_ref: date
    descricao: str
    valor: Decimal  # entrada positiva, saída negativa
    categoria: str = ""
    origem: str  # "nubank" | "bradesco"
    parcela: str = ""  # "3/10" (o apóstrofo é acrescentado só na escrita)
    revisado: bool = False
    confianca: float = 0.0
    descricao_original: str
    id: str
    # Estorno do Nubank: descrição de X em `Estorno de "X" (Y)`; a classificação
    # (regras/LLM) usa X para herdar a categoria da compra original.
    estorno_de: str | None = None
    # Como foi classificada: "regra" | "conciliacao" | "llm" | "" (ainda não).
    via: str = ""

    def para_linha(self) -> list:
        """Linha A:K de Lançamentos (L:M são fórmulas da planilha)."""
        return [
            self.data.isoformat(),
            self.mes_ref.isoformat(),
            self.descricao.strip() or self.descricao_original,
            float(self.valor),
            self.categoria,
            self.origem,
            f"'{self.parcela}" if self.parcela else "",
            self.revisado,
            self.confianca,
            self.descricao_original,
            self.id,
        ]


class ArquivoParseado(BaseModel):
    tipo: TipoArquivo
    transacoes: list[Transacao]
    # Fatura Nubank: soma de −valor de todas as linhas exceto "Pagamento recebido".
    total_fatura: Decimal | None = None
    mes_ref: date | None = None


class Categoria(BaseModel):
    nome: str
    tipo: str = ""
    grupo: str = ""
    orcamento_mensal: str = ""
    dica: str = ""


class Regra(BaseModel):
    padrao: str
    categoria: str
    descricao_limpa: str
    origem: str = ""


class ConfigPlanilha(BaseModel):
    dia_fechamento_nubank: int
    dia_vencimento_nubank: int
    inicio_dados: date


class LancamentoExistente(BaseModel):
    id: str
    mes_ref: date | None = None
    valor: Decimal = Decimal(0)
    categoria: str = ""
    origem: str = ""
    revisado: bool = False
    descricao_original: str = ""


class ArquivoDrive(BaseModel):
    id: str
    nome: str
    md5: str = ""


class ItemLLM(BaseModel):
    indice: int
    descricao_original: str
    valor: float
    origem: str


class ItemClassificado(BaseModel):
    indice: int
    categoria: str
    descricao_limpa: str = ""
    confianca: float = Field(default=0.0, ge=0.0, le=1.0)


class ResultadoLLM(BaseModel):
    itens: list[ItemClassificado] = Field(default_factory=list)
    # Itens que caíram em "Outros" por falha do provedor (rede/limite/timeout).
    falhas: int = 0


class ResultadoImportacao(BaseModel):
    """Resumo de um arquivo processado (vira linha em Importações)."""

    arquivo: str
    drive_file_id: str
    md5: str
    tipo_arquivo: TipoArquivo | None = None
    linhas_lidas: int = 0
    ja_existentes: int = 0
    linhas_novas: int = 0
    via_regras: int = 0
    via_llm: int = 0
    status: str = "ok"  # ok | erro | ignorado
    erro: str = ""
    novas: list[Transacao] = Field(default_factory=list)
    # dry-run: destino em que o arquivo seria arquivado (ex.: "files/2026-02")
    arquivaria_em: str = ""
