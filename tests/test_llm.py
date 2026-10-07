import json
from types import SimpleNamespace

import openai

try:  # openai 3.x usa httpx2; versões antigas usam httpx
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

from just_a_sheet.llm import ClassificadorLLM
from just_a_sheet.models import Categoria, ItemLLM

CATEGORIAS = [
    Categoria(nome="Alimentação", tipo="despesa", dica="restaurantes, padarias"),
    Categoria(nome="Compras", tipo="despesa"),
    Categoria(nome="Outros", tipo="despesa"),
]
REQ = httpx.Request("POST", "http://llm.invalid/v1/chat/completions")


def _itens(n: int) -> list[ItemLLM]:
    return [
        ItemLLM(indice=i, descricao_original=f"LOJA {i}", valor=-1.0, origem="nubank")
        for i in range(n)
    ]


def _resposta(itens: list[dict]) -> SimpleNamespace:
    msg = SimpleNamespace(content=json.dumps({"itens": itens}))
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


class FakeClient:
    """Cada passo é uma resposta pronta ou uma exceção a ser levantada."""

    def __init__(self, passos):
        self.passos = list(passos)
        self.chamadas: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.chamadas.append(kwargs)
        passo = self.passos.pop(0) if len(self.passos) > 1 else self.passos[0]
        if isinstance(passo, Exception):
            raise passo
        return passo


def _llm(client, dormiu=None):
    return ClassificadorLLM(
        client, "modelo-x", sleep=(dormiu if dormiu is not None else []).append
    )


def test_resposta_valida():
    client = FakeClient(
        [
            _resposta(
                [
                    {"indice": 0, "categoria": "Compras", "descricao_limpa": "Loja 0",
                     "confianca": 0.9},
                    {"indice": 1, "categoria": "Alimentação", "descricao_limpa": "Padaria",
                     "confianca": 1.7},
                ]
            )
        ]
    )  # fmt: skip
    r = _llm(client).classificar(_itens(2), CATEGORIAS, [("PADARIA X", "Alimentação")])
    assert r.falhas == 0
    assert [(i.indice, i.categoria, i.descricao_limpa) for i in r.itens] == [
        (0, "Compras", "Loja 0"),
        (1, "Alimentação", "Padaria"),
    ]
    assert r.itens[1].confianca == 1.0  # clamp em 0..1
    chamada = client.chamadas[0]
    assert chamada["response_format"]["type"] == "json_schema"
    enum = chamada["response_format"]["json_schema"]["schema"]["properties"]["itens"][
        "items"
    ]["properties"]["categoria"]["enum"]
    assert enum == ["Alimentação", "Compras", "Outros"]
    prompt = chamada["messages"][0]["content"]
    assert "padarias" in prompt and "PADARIA X -> Alimentação" in prompt
    # só os 4 campos permitidos vão para o provedor
    enviado = json.loads(chamada["messages"][1]["content"].split("\n", 1)[1])
    assert set(enviado[0]) == {"indice", "descricao_original", "valor", "origem"}


def test_categoria_invalida_ou_ausente_vira_outros():
    client = FakeClient(
        [
            _resposta(
                [
                    {"indice": 0, "categoria": "Inventada", "descricao_limpa": "x",
                     "confianca": 0.9},
                    {"indice": 2, "categoria": None, "confianca": 0.8},
                ]
            )
        ]
    )  # fmt: skip
    r = _llm(client).classificar(_itens(3), CATEGORIAS)
    assert [(i.categoria, i.confianca) for i in r.itens] == [("Outros", 0.0)] * 3
    assert r.falhas == 0


def test_falha_de_rede_vira_outros_com_retentativas():
    dormiu: list[float] = []
    client = FakeClient([openai.APIConnectionError(request=REQ)])
    r = _llm(client, dormiu).classificar(_itens(3), CATEGORIAS)
    assert r.falhas == 3
    assert [(i.categoria, i.confianca) for i in r.itens] == [("Outros", 0.0)] * 3
    assert len(client.chamadas) == 3  # 1 + 2 novas tentativas
    assert dormiu == [2, 4]  # backoff


def test_json_schema_rejeitado_cai_para_json_object():
    rejeicao = openai.BadRequestError(
        "sem suporte", response=httpx.Response(400, request=REQ), body=None
    )
    ok = _resposta(
        [
            {
                "indice": 0,
                "categoria": "Compras",
                "descricao_limpa": "L",
                "confianca": 0.5,
            }
        ]
    )
    client = FakeClient([rejeicao, ok])
    r = _llm(client).classificar(_itens(1), CATEGORIAS)
    assert [c["response_format"]["type"] for c in client.chamadas] == [
        "json_schema",
        "json_object",
    ]
    assert "Responda SOMENTE com JSON" in client.chamadas[1]["messages"][0]["content"]
    assert r.itens[0].categoria == "Compras"


def test_json_quebrado_conta_como_falha():
    msg = SimpleNamespace(content="isto não é json")
    client = FakeClient([SimpleNamespace(choices=[SimpleNamespace(message=msg)])])
    r = _llm(client).classificar(_itens(2), CATEGORIAS)
    assert r.falhas == 2


def test_lotes_de_50():
    ok = _resposta([])
    client = FakeClient([ok])
    r = _llm(client).classificar(_itens(120), CATEGORIAS)
    assert len(client.chamadas) == 3
    assert len(r.itens) == 120
