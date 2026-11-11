from __future__ import annotations

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def _carregar_env():
    arq = RAIZ / ".env"
    if not arq.exists():
        return
    for linha in arq.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        k, v = linha.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_carregar_env()


def env(nome: str, padrao: str | None = None) -> str | None:
    return os.environ.get(nome, padrao)


def env_bool(nome: str, padrao: bool) -> bool:
    v = os.environ.get(nome)
    return padrao if v is None else v.strip().lower() in ("1", "true", "sim", "yes", "on")


EFFORT_URL = env("EFFORT_URL", "https://huusp.globalthings.net").rstrip("/")
EFFORT_USUARIO = env("EFFORT_USUARIO", "")
EFFORT_SENHA = env("EFFORT_SENHA", "")
EFFORT_LIBERAR_ACESSO = env_bool("EFFORT_LIBERAR_ACESSO", True)
EFFORT_EMPRESA = env("EFFORT_EMPRESA", "Manutenção de Equipamentos")
HEADLESS = env_bool("HEADLESS", True)

BANCO = Path(env("BANCO", str(RAIZ / "dados" / "painel.db")))
REGRAS = Path(env("REGRAS", str(RAIZ / "regras.yaml")))

# cron, horário de Brasília
AGENDA_CRON = env("AGENDA_CRON", "0 7,13 * * 1-5")
FUSO = env("FUSO", "America/Sao_Paulo")

# vazio = sem senha
SENHA_PAINEL = env("SENHA_PAINEL", "")
