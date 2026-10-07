# just-a-sheet — especificação do importador

Importador que lê a fatura do cartão (Nubank) e o extrato da conta (Bradesco) de uma
pasta do Google Drive, classifica cada transação e grava numa planilha Google Sheets
que já existe. Roda como um container Docker num servidor ARM64, num loop simples.

**Repositório público.** Nada de dados reais aqui: nem extratos, nem nomes de pessoas,
nem agência/conta/CNPJ, nem IDs da planilha/pasta, nem chaves. Todos os exemplos e
fixtures deste repositório são fictícios. IDs e segredos chegam por variáveis de ambiente.

---

## 1. Fluxo

```
a cada POLL_SECONDS:
  arquivos = Drive.listar(DRIVE_FOLDER_ID)
  ja_processados = planilha.Importações (drive_file_id, md5)
  para cada arquivo novo ou com md5 diferente:
      tipo = detectar(arquivo)               # fatura_nubank | extrato_bradesco | ignorado
      se ignorado: registrar em Importações (status "ignorado", erro = motivo); seguir
      transacoes = parser[tipo](bytes, nome)
      descartar transacoes com mes_ref < inicio_dados (Config) e ids já na planilha
      classificar: Regras -> conciliação do pagamento da fatura -> LLM (o que sobrou)
      gravar em Lançamentos (append)
      se status "ok", não é --dry-run e ARCHIVE_FOLDER_ID está definida: arquivar o arquivo (§1.1)
      registrar em Importações
  erros de um arquivo não derrubam o loop: vão para Importações (status "erro")
```

### 1.1 Arquivamento

- Só arquivos `ok` (nunca `ignorado`/`erro`), fora do `--dry-run`, e com `ARCHIVE_FOLDER_ID` definida.
- Mês `AAAA-MM` = `mes_ref` mais frequente entre as transações parseadas do arquivo (todas,
  antes do filtro de `inicio_dados` e do dedup; na fatura Nubank é o `mes_ref` da fatura).
  Arquivo sem transações não é arquivado.
- A subpasta é buscada por nome dentro de `ARCHIVE_FOLDER_ID`; se não existir, tenta criar
  (a conta de serviço não tem cota no Drive, então a criação pode falhar).
- Move com `files.update(addParents=<subpasta>, removeParents=DRIVE_FOLDER_ID)`, sem renomear.
- O arquivamento acontece antes de gravar a linha de Importações, para a nota entrar nela.
- Falha de arquivamento não muda o status (continua `ok`): warning no log e, no campo `erro`
  de Importações, `não foi possível arquivar (<TipoDoErro>); o arquivo ficou em input`.
- `--dry-run` imprime `arquivaria em files/AAAA-MM` por arquivo, sem chamar a API.

## 2. Contrato com a planilha (já existe; não criar nem alterar estrutura)

Abas e colunas (linha 1 = cabeçalho). O importador **só lê** Categorias, Regras e Config,
e **só faz append** em Lançamentos e Importações.

### Lançamentos — o importador escreve **apenas A:K**

| col | campo | tipo / formato de escrita |
|---|---|---|
| A | data | data da transação, string ISO `YYYY-MM-DD` |
| B | mes_ref | primeiro dia do mês de referência, ISO `YYYY-MM-01` |
| C | descricao | texto curto e legível |
| D | valor | número; **entrada positiva, saída negativa** |
| E | categoria | exatamente um nome da aba Categorias |
| F | origem | `nubank` ou `bradesco` |
| G | parcela | `"'3/10"` (com apóstrofo, senão o Sheets vira data) ou `""` |
| H | revisado | booleano |
| I | confianca | número 0..1 |
| J | descricao_original | texto original do banco (normalizado, ver §5) |
| K | id | 12 hex, ver §4 |

L (`tipo`) e M (`grupo`) são fórmulas `ARRAYFORMULA` no cabeçalho — **nunca escrever em L:M**.

Escrita: `values.append` em `'Lançamentos'!A:K` com `valueInputOption=USER_ENTERED`,
`insertDataOption=OVERWRITE`. A planilha está em locale pt_BR: datas vão como string
ISO (o Sheets converte), números vão como número JSON (nunca string com vírgula),
booleanos como booleano JSON.

### Importações (append em A:K)

`arquivo | drive_file_id | md5 | tipo_arquivo | processado_em | linhas_lidas | linhas_novas | via_regras | via_llm | status | erro`

- `processado_em`: `YYYY-MM-DD HH:MM` no fuso `America/Sao_Paulo`.
- `status`: `ok`, `erro` ou `ignorado`. `erro`: mensagem curta em português (vazio se ok).
- Um arquivo conta como já processado se existir linha com o mesmo `drive_file_id` e o
  mesmo `md5` (status qualquer). md5 vazio na planilha = processar de novo (o dedup por id
  evita duplicatas).

### Categorias (ler A2:E)

`categoria | tipo (despesa/receita/transferencia) | grupo | orcamento_mensal | dica_para_classificacao`

### Regras (ler A2:D)

`padrao | categoria | descricao_limpa | origem` (origem vazia = qualquer banco). Ordem importa.

### Config (ler A2:B)

Pares `parametro | valor`: `dia_fechamento_nubank` (int), `dia_vencimento_nubank` (int),
`inicio_dados` (data; vem formatada como mês, ler com `valueRenderOption=UNFORMATTED_VALUE`
e converter o serial do Sheets, ou ler `FORMATTED` e tratar `set./2026`).
A coluna E da Config é fórmula da planilha — ignorar.

## 3. Detecção do tipo de arquivo

| condição | tipo |
|---|---|
| `.csv` cuja primeira linha é `date,title,amount` | `fatura_nubank` |
| `.pdf` cujo texto contém `Bradesco` e `Extrato de:` | `extrato_bradesco` |
| `.csv` do Bradesco (primeira linha começa com `Extrato de:` e usa `;`) | `ignorado`: "CSV do Bradesco não traz o nome de quem pagou/recebeu; envie o PDF" |
| qualquer outro | `ignorado`: "formato não reconhecido" |

## 4. Identificador (dedup) — tem que bater exatamente

`id = sha1(chave.encode("utf-8")).hexdigest()[:12]`

- **Nubank:** `chave = "nubank|{data_iso}|{title}|{valor:.2f}|{n}"`
  - `title` = coluna `title` do CSV exatamente como vem do `csv` module (sem aspas externas).
  - `valor` = valor **já com o sinal da planilha** (compra → negativo).
  - `n` = ocorrência (1, 2, …) da mesma tupla `(data, title, valor)` dentro do arquivo.
- **Bradesco:** `chave = "bradesco|{data_iso}|{int(docto)}|{valor:.2f}"`
  - `docto` sem zeros à esquerda (`0000011` → `11`). `valor` com sinal.

Vetores de teste (fictícios, obrigatórios nos testes):

| chave | id |
|---|---|
| `nubank\|2026-01-05\|Padaria Exemplo\|-12.50\|1` | `b937d3338efd` |
| `nubank\|2026-01-05\|Padaria Exemplo\|-12.50\|2` | `0e46baa057ce` |
| `bradesco\|2026-01-07\|1234567\|-45.90` | `84af0b36a645` |
| `bradesco\|2026-01-07\|1000007\|1500.00` | `6f418734ac92` |

## 5. Parsers

### 5.1 Fatura Nubank (CSV)

Formato (UTF-8, vírgula, valores pt-BR entre aspas):

```
date,title,amount
2026-01-12,Padaria Exemplo,"12,50"
2026-01-10,"IOF de ""Loja Gringa Sub""","3,50"
2026-01-10,Loja Gringa Sub,"98,20"
2026-01-05,"Estorno de ""Mercado Online"" (Mercado)","- 45,10"
2026-01-03,Pagamento recebido,"- 2.870,40"
2026-01-01,Loja de Roupas - Parcela 2/6,"58,00"
```

- `amount`: remover espaços, `.` de milhar, `,` → `.`; pode ter `- ` na frente.
  **Valor na planilha = −amount** (compra positiva no CSV vira saída negativa).
- Parcela: sufixo regex `\s+-\s+Parcela\s+(\d+)/(\d+)$` → coluna G `'n/m'`. Parcelas vêm com
  a data de início do ciclo; manter a data como vem.
- `Pagamento recebido` → é o crédito do pagamento da fatura (Regra resolve).
- `IOF de "X"` → linha própria (Regra resolve).
- Estorno `Estorno de "X" (Y)`: classificar como se fosse `X` (mesma categoria da compra);
  `descricao = "Estorno " + descricao_de_X`; valor positivo.
- `descricao_original` = `title` completo.
- **mes_ref** = mês do **vencimento** da fatura:
  1. se o nome do arquivo tem `YYYY-MM-DD` (ex.: `nubank_fatura_2026-09-16.csv`,
     `Nubank_2026-09-16.csv`) → esse é o vencimento;
  2. senão inferir: `d` = maior data de compra (ignorando `Pagamento recebido`);
     se `d.day < dia_fechamento_nubank` → vencimento no mês de `d`, senão no mês seguinte.
- Total da fatura (para conciliação) = soma de −valor de todas as linhas exceto
  `Pagamento recebido`.

### 5.2 Extrato Bradesco (PDF, layout "Bradesco Celular")

**Usar a posição das palavras, não o texto corrido.** Cada lançamento tem até duas linhas
de texto na coluna Histórico (ex.: `PIX QR CODE DINAMICO` e, embaixo, `DES: NOME 06/10`) e
as colunas Docto./Crédito/Débito/Saldo ficam **centralizadas na vertical** entre elas.
`page.extract_text()` põe então os números ora com a linha de cima, ora com a de baixo
(dependendo da página) e o nome de cada Pix cairia no lançamento seguinte. Por isso o
parser usa `page.extract_words()` (`x0`, `x1`, `top`, `bottom`), página a página.

Linhas de cabeçalho/rodapé: tudo acima da linha de cabeçalho da tabela
(`Data Histórico Docto. Crédito (R$) Débito (R$) Saldo (R$)`) é descartado (título,
`Extrato de:`, `Data:`, `Folha:`, `Últimos Lancamentos`). **Nunca** guardar nem logar o
conteúdo dessas linhas (têm nome do titular, agência e conta).

Algoritmo:
1. **Colunas** pelos cabeçalhos: Histórico (`x0` de "Histórico"), Docto. (`x0` de
   "Docto.") e valores (`x0` de "Crédito"). Palavras à esquerda do Histórico = coluna
   Data; entre Histórico e Docto. = texto; à direita = docto e valores.
2. **Linhas de valores:** as palavras da zona numérica são agrupadas por `top`. Em cada
   grupo, o inteiro à esquerda das colunas de valores é o `docto` e os números
   `-?[\d.]+,\d{2}` ordenados por `x1` são `[valor, saldo]` (ou `[crédito, débito, saldo]`
   na linha `Total`; `COD. LANC.` usa o último). A linha com a palavra `Total` na coluna
   Data é a linha de total.
3. **Atribuição:** cada linha de texto da coluna Histórico e cada data da coluna Data vai
   para a linha de valores cujo centro vertical (`(top+bottom)/2`) está **mais próximo**
   do dela. Fragmentos a mais de 14 pt de qualquer linha são ignorados (aviso no log, sem
   o texto). A data vale até a próxima data; lançamentos sem data herdam a anterior.
4. Texto do lançamento = fragmentos em ordem vertical, unidos por espaço, sem o `dd/mm`
   final (data do Pix).

Exemplo **fictício** do texto que `extract_text` devolveria **para as páginas com os valores
centralizados** (note que a linha dos números fica entre as duas linhas do histórico):

```
Data Histórico Docto. Crédito (R$) Débito (R$) Saldo (R$)
31/12/2025 COD. LANC. 0 100,00
PIX QR CODE DINAMICO
02/01/2026 1000001 4,10 95,90
DES: PADARIA EXEMPLO LTDA 02/01
PIX RECEBIDO
05/01/2026 1000002 5.000,00 5.095,90
REM: EMPRESA EXEMPLO SA 05/01
Total 5.000,01 3.136,29 1.963,72
```

Regras do parser:
- `COD. LANC. 0`: não é transação; só define o saldo inicial (o último número da linha).
- **Sinal pelo saldo:** `saldo_atual - saldo_anterior` deve ser `+valor` (crédito) ou
  `-valor` (débito), com tolerância de R$ 0,005. Se não for nenhum dos dois →
  **falha a importação do arquivo inteiro** com erro claro. Essa é a checagem de
  integridade: nunca gravar valor lido errado.
- Linha `Total créditos débitos saldo`: conferir soma dos créditos, soma dos débitos e saldo
  final da seção; divergência → falha.
- Há duas seções (o período filtrado e "Últimos Lancamentos"); ler as duas.
- `historico` = parte antes de `DES:`/`REM:` ou, nos boletos, as palavras conhecidas do
  histórico (`PAGTO ELETRON COBRANCA`); `contraparte` = o resto (`DES: …`, `REM: …` ou o nome
  do favorecido do boleto), sem a data `dd/mm` final.
- `descricao_original = re.sub(r"\s+", " ", f"{historico} {contraparte}").strip()`,
  ex.: `PIX QR CODE DINAMICO DES: PADARIA EXEMPLO LTDA`, `PAGTO ELETRON COBRANCA IMOBILIARIA EXEMPLO ADM`,
  `RENTAB.INVEST FACILCRED*`.
- `mes_ref` = primeiro dia do mês da data. `parcela` vazia.

## 6. Classificação

### 6.1 Regras (determinístico)

- Normalizar texto e padrão: NFKD → remover acentos → minúsculas.
- Casa se `re.search(r"(^|[^a-z0-9])" + re.escape(padrao_norm), descricao_original_norm)`
  (o padrão só casa a partir do início de uma palavra: `Raia` casa `Raia1755`, mas não
  `Cantinapraia`).
- Respeitar `origem` da regra (vazia = qualquer). **Primeira regra que casar vence.**
- Resultado: `categoria` e `descricao = descricao_limpa`; `revisado = True`; `confianca = 1.0`.
- Para estorno do Nubank, aplicar ao `X` de `Estorno de "X" (Y)`.

### 6.2 Conciliação do pagamento da fatura

Débito do Bradesco ainda sem categoria cujo `abs(valor)` difere em até R$ 1,00 do total
de alguma fatura Nubank (do lote atual ou das linhas Nubank já na planilha, total por
`mes_ref`, excluindo a categoria "Pagamento de fatura") → categoria `Pagamento de fatura`,
`descricao = "Pagamento da fatura Nubank"`, `revisado = True`, `confianca = 1.0`.

### 6.3 LLM (só o que sobrou)

- Cliente: SDK `openai` apontando para `LLM_BASE_URL` (API compatível com OpenAI; padrão
  `https://ollama.com/v1`), `LLM_API_KEY`, `LLM_MODEL` (padrão `gpt-oss:20b`).
- Uma chamada por lote de até 50 itens. Enviar **só** `indice`, `descricao_original`,
  `valor`, `origem`. Nunca dados de cabeçalho do extrato.
- Prompt em português com: lista de categorias (nome, tipo, dica) e até 30 exemplos
  recentes de linhas `revisado = TRUE` da planilha (`descricao_original → categoria`),
  como few-shot.
- Saída estruturada: **só** `response_format={"type": "json_object"}`, com o formato
  `{"itens": [{"indice": 0, "categoria": "...", "descricao_limpa": "...", "confianca": 0.9}]}`
  descrito no prompt. Não usar `json_schema`: o Ollama Cloud responde HTTP 200 e ignora o
  schema (veio só `"Mercado"`), o que derrubava o lote inteiro em `Outros`. Aceitar a
  resposta envolta em cercas markdown (três crases + `json`) e sempre validar com pydantic.
- A `descricao` gravada nunca fica vazia: sem `descricao_limpa` (LLM falhou ou omitiu), usa
  a `descricao_original`.
- Por item: `indice`, `categoria`, `descricao_limpa` (curta, legível), `confianca` (0..1).
- Item com categoria fora da lista ou ausente → `Outros`, `confianca = 0`.
- Resultado LLM: `revisado = False`.
- Falha de rede/limite/timeout (timeout 120 s, 2 novas tentativas com backoff) → gravar
  mesmo assim com `Outros`, `confianca = 0`, `revisado = False`, e `erro` na linha de
  Importações = "LLM indisponível; N lançamentos ficaram em Outros".

## 7. Configuração (variáveis de ambiente, `pydantic-settings`)

| variável | obrigatória | padrão |
|---|---|---|
| `SPREADSHEET_ID` | sim | |
| `DRIVE_FOLDER_ID` | sim | |
| `ARCHIVE_FOLDER_ID` | não | vazio = não arquiva |
| `GOOGLE_APPLICATION_CREDENTIALS` | sim | `/run/secrets/google-sa.json` |
| `LLM_BASE_URL` | não | `https://ollama.com/v1` |
| `LLM_API_KEY` | sim | |
| `LLM_MODEL` | não | `gpt-oss:20b` |
| `POLL_SECONDS` | não | `600` |
| `TZ` | não | `America/Sao_Paulo` |

Credencial Google: conta de serviço (JSON), escopos `drive` (precisa mover arquivos) e `spreadsheets`.
Bibliotecas: `google-api-python-client` + `google-auth` para o Drive, `gspread` para o Sheets.

## 8. CLI (`[project.scripts] just-a-sheet = "just_a_sheet.cli:main"`)

- `just-a-sheet run` — loop infinito (padrão do container). Log por ciclo em stdout.
- `just-a-sheet once [--dry-run]` — um ciclo. `--dry-run`: lê Drive e planilha, faz parse
  e classificação (inclusive LLM), **não escreve nada**; imprime por arquivo: tipo,
  linhas lidas, quantas já existem na planilha (por id), quantas seriam novas e a
  classificação de cada nova.
- `just-a-sheet parse ARQUIVO [--fechamento 9]` — só parser, arquivo local, sem rede;
  imprime a tabela de transações e os totais (útil para depurar layout de PDF).

Logs: nunca imprimir linhas de cabeçalho do PDF, agência, conta ou CNPJ.

## 9. Estrutura sugerida

```
just_a_sheet/
  __init__.py
  cli.py            # argparse: run | once | parse
  config.py         # Settings (pydantic-settings)
  models.py         # Transacao (pydantic), ResultadoImportacao
  ids.py            # make_id_nubank, make_id_bradesco
  money.py          # parse de valores pt-BR
  parsers/
    __init__.py     # detectar_tipo(nome, bytes)
    nubank.py
    bradesco_pdf.py # parse_pdf(bytes): pdfplumber extract_words -> parse_palavras(páginas)
  rules.py
  reconcile.py
  llm.py
  sheet.py          # leitura/escrita na planilha (gspread)
  drive.py          # listar/baixar (google-api-python-client)
  pipeline.py       # orquestra um ciclo; recebe sheet/drive/llm por injeção
tests/
  fixtures/         # SÓ dados fictícios
  test_ids.py test_money.py test_nubank.py test_bradesco.py
  test_rules.py test_reconcile.py test_llm.py test_pipeline.py
```

`pipeline` recebe interfaces (Protocol) de planilha, drive e LLM, para os testes usarem
fakes em memória.

## 10. Empacotamento e deploy (mesmo esquema do projeto irmão)

- Python 3.13, `uv` (com `uv.lock` versionado), `ruff` no grupo `dev` (mesma config:
  line-length 88, select E,F,I,UP,B,C4,SIM, ignore E501), `pytest` no grupo `dev`.
- `Dockerfile` multi-stage (`base` → `production`), `python:3.13-slim`, uv copiado de
  `ghcr.io/astral-sh/uv`, `uv sync --frozen --no-dev`, usuário non-root, `ENV TZ`,
  `CMD ["uv", "run", "just-a-sheet", "run"]`. Precisa funcionar em `linux/arm64`.
- `docker-compose.yml`: `name: just-a-sheet`; serviço `importer`; `build: {context: ., target: production}`;
  `env_file: .env`; volume `/just-a-sheet/service-account.json:/run/secrets/google-sa.json:ro`;
  `restart: unless-stopped`; logging json-file com `max-size: 10m`, `max-file: 3`.
- `.github/workflows/deploy.yml`: `on: push: branches: [prd]`; `concurrency: production`;
  `runs-on: [self-hosted, linux, ARM64, just-a-sheet]`; passos: checkout,
  `cp /just-a-sheet/.env .env`, `docker compose -f docker-compose.yml up -d --build --remove-orphans`,
  `docker image prune -f`.
- `deploy.yml` também tem o job `dry-run` (`workflow_dispatch`), que faz o build e roda
  `just-a-sheet once --dry-run` sem subir o serviço. **Logs de Actions de repositório
  público são públicos: nenhum workflow pode imprimir dados de transações.** O job
  redireciona toda a saída do comando para `/just-a-sheet/dry-run.log` (no servidor; o
  redirecionamento é feito pelo shell do runner, fora do container, que roda com o UID do
  dono da pasta) e o log mostra só "Simulação concluída/falhou" com o caminho do arquivo:
  ```
  docker compose -f docker-compose.yml run --rm importer just-a-sheet once --dry-run > /just-a-sheet/dry-run.log 2>&1 \
    && echo "Simulação concluída. Resultado em /just-a-sheet/dry-run.log no servidor." \
    || { echo "Simulação falhou. Detalhes em /just-a-sheet/dry-run.log no servidor."; exit 1; }
  ```
- `.github/workflows/ci.yml`: `on: [push, pull_request]`; **`runs-on: ubuntu-latest`**
  (nunca self-hosted em CI, o repositório é público); `uv sync`, `uv run ruff check`,
  `uv run pytest`.
- `.gitignore`: `.env`, `*.json` de credencial (`service-account*.json`, `*-sa.json`),
  `samples/`, `.venv`, caches. `.env.example` com as variáveis da §7 (valores vazios).
- `README.md` curto: o que é, como rodar local (`uv run just-a-sheet parse …`), como
  publicar (push em `prd`), variáveis de ambiente.

## 11. Critérios de aceite

1. `uv run pytest` passa; `uv run ruff check` limpo.
2. Testes cobrem, com fixtures fictícias:
   - vetores de id da §4;
   - parse de valores pt-BR (`"12,50"`, `"- 2.870,40"`, `"1.000,00"`);
   - Nubank: sinal, parcela, IOF, estorno (categoria herdada de X), pagamento recebido,
     `n` de ocorrência para linhas idênticas, mes_ref pelo nome do arquivo e inferido;
   - Bradesco: um PDF **fictício gerado nos testes** com o layout da §5.2 (duas linhas de
     histórico, números centralizados/na 1ª/na 2ª linha, lançamentos de uma linha, boleto com
     o nome embaixo, três páginas, duas seções) gera as transações certas (datas, sinais,
     valores, `descricao_original`, ids) e inclui um caso em que `extract_text` descasaria;
     **falha** quando um valor é alterado de forma que o saldo não fecha e quando o Total
     não bate;
   - Regras: normalização, início de palavra (`Raia` × `Cantinapraia`), origem, primeira vence;
   - conciliação ±R$ 1,00;
   - LLM: resposta válida, categoria inválida → Outros/0, falha → Outros/0 + erro;
   - arquivamento (fakes): move quando `ok`; não move em `ignorado`/`erro`/`--dry-run`;
     escolhe o mês mais frequente; falha ao mover mantém `ok` e anota em `erro`;
     `ARCHIVE_FOLDER_ID` vazio não arquiva;
   - pipeline com fakes: arquivo já processado é pulado; ids existentes não são regravados;
     `mes_ref < inicio_dados` é descartado; Bradesco CSV vira `ignorado`; escreve só A:K;
     `--dry-run` não escreve.
3. `docker build --platform linux/arm64 --target production .` funciona (se houver Docker
   disponível; senão, deixar claro no relato que não foi verificado).
4. `git grep` não encontra nenhum dado real (o repositório é público).
