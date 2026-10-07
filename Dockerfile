# == Base ==
# Estágio comum: interpretador, uv e as dependências em cache.
FROM python:3.13-slim AS base
# Instala o uv (gerenciador de pacotes/venv). Copiamos os binários da imagem oficial.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
# PYTHONDONTWRITEBYTECODE: não gera arquivos .pyc.
# PYTHONUNBUFFERED: não bufferiza stdout/stderr — evita perder logs se o app cair.
# TZ: fuso usado nos timestamps de Importações (pode ser sobrescrito pelo .env).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=America/Sao_Paulo
WORKDIR /app
# Copiamos só os manifests primeiro: enquanto eles não mudam, o Docker reaproveita
# a camada de dependências em cache e não reinstala tudo a cada build.
COPY pyproject.toml uv.lock ./

# == Production ==
# Imagem de produção: SEM o grupo `dev`, código imutável dentro da imagem,
# rodando como usuário non-root.
FROM base AS production
# --no-dev: ignora o grupo de dev (ruff/pytest não vão pra produção).
# O projeto em si ainda não é instalado aqui (melhor cache de camadas).
RUN uv sync --frozen --no-install-project --no-dev
# Copia o código e finaliza a instalação (o projeto em si, com o comando just-a-sheet).
COPY README.md ./
COPY just_a_sheet ./just_a_sheet
RUN uv sync --frozen --no-dev
# Cria um usuário sem privilégios e entrega a ele o código e o venv.
# Rodar como root em produção é uma superfície de ataque desnecessária.
RUN useradd --create-home appuser \
    && chown -R appuser:appuser /app
USER appuser
# CMD padrão — loop infinito de importação.
CMD ["uv", "run", "just-a-sheet", "run"]
