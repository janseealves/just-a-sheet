# just-a-sheet

Importador que lê a fatura do cartão (Nubank, CSV) e o extrato da conta (Bradesco, PDF)
de uma pasta do Google Drive, classifica cada transação (regras da planilha, conciliação
do pagamento da fatura e, por fim, um LLM) e grava numa planilha Google Sheets que já
existe. Roda como container Docker num loop simples. A especificação completa está em
[`docs/SPEC.md`](docs/SPEC.md).

> Repositório público: nenhum dado real (extratos, contas, IDs) deve entrar aqui.

## Rodar localmente

```bash
uv sync
uv run pytest
uv run ruff check

# Só o parser, sem rede (útil para depurar o layout de um PDF/CSV local):
uv run just-a-sheet parse caminho/do/arquivo.pdf
uv run just-a-sheet parse fatura.csv --fechamento 9

# Um ciclo completo sem escrever nada (usa Drive, planilha e LLM reais):
uv run just-a-sheet once --dry-run
```

Arquivos reais para depuração ficam em `samples/` (ignorado pelo git).

## Publicar

Push na branch `prd` dispara `.github/workflows/deploy.yml`, que roda num runner
self-hosted ARM64, copia `/just-a-sheet/.env` e sobe o `docker-compose.yml`.
Simulação: em Actions → Deploy → Run workflow (`workflow_dispatch`) roda
`once --dry-run` no servidor, lendo Drive/planilha/LLM sem gravar nada e sem subir o serviço.
A conta de serviço do Google fica em `/just-a-sheet/service-account.json` no servidor.

## Variáveis de ambiente

| variável | obrigatória | padrão |
|---|---|---|
| `SPREADSHEET_ID` | sim | |
| `DRIVE_FOLDER_ID` | sim | |
| `GOOGLE_APPLICATION_CREDENTIALS` | sim | `/run/secrets/google-sa.json` |
| `LLM_BASE_URL` | não | `https://ollama.com/v1` |
| `LLM_API_KEY` | sim | |
| `LLM_MODEL` | não | `gpt-oss:20b` |
| `POLL_SECONDS` | não | `600` |
| `TZ` | não | `America/Sao_Paulo` |

Veja `.env.example`.
