from datetime import date
from decimal import Decimal

from just_a_sheet.models import Regra, Transacao
from just_a_sheet.rules import aplicar_regras, casa, normalizar


def _t(desc: str, origem: str = "nubank") -> Transacao:
    return Transacao(
        data=date(2026, 1, 1),
        mes_ref=date(2026, 1, 1),
        descricao=desc,
        valor=Decimal("-1"),
        origem=origem,
        descricao_original=desc,
        id="x",
    )


def test_normalizacao():
    assert normalizar("Padaria AÇÚCAR Ão") == "padaria acucar ao"


def test_inicio_de_palavra():
    assert casa("Raia", "DROGA RAIA1755 SP")  # Raia1755 casa
    assert casa("Raia", "Drogaria Raia")
    assert not casa("Raia", "Cantinapraia")
    assert casa("açúcar", "ACUCAR REFINADO")  # acentos e caixa


def test_origem_da_regra():
    regras = [
        Regra(padrao="Pix", categoria="A", descricao_limpa="a", origem="bradesco")
    ]
    assert not aplicar_regras(_t("Pix teste", "nubank"), regras, {"A"})
    assert aplicar_regras(_t("Pix teste", "bradesco"), regras, {"A"})


def test_primeira_regra_vence_e_resultado():
    regras = [
        Regra(padrao="Loja", categoria="Compras", descricao_limpa="Loja"),
        Regra(padrao="Loja Gringa", categoria="Viagem", descricao_limpa="Gringa"),
    ]
    t = _t("Loja Gringa Sub")
    assert aplicar_regras(t, regras, {"Compras", "Viagem"})
    assert (t.categoria, t.descricao) == ("Compras", "Loja")
    assert t.revisado is True
    assert t.confianca == 1.0


def test_categoria_inexistente_e_ignorada():
    regras = [Regra(padrao="Loja", categoria="Fantasma", descricao_limpa="x")]
    t = _t("Loja")
    assert not aplicar_regras(t, regras, {"Compras"})
    assert t.categoria == ""
