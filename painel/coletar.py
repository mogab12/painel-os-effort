"""Executa uma coleta e grava no banco.

Uso:
    python -m painel.coletar              # coleta no Effort (usa as variáveis do .env)
    python -m painel.coletar --visivel    # abre o navegador na tela, para depurar
    python -m painel.coletar --demo       # gera dados fictícios, para testar o painel sem o Effort
    python -m painel.coletar --demo-dias 15   # idem, com 15 dias de histórico (para ver a evolução do backlog)
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import traceback

from . import config
from .banco import Banco

log = logging.getLogger("painel")


def executar(origem: str = "manual", demo: bool = False, visivel: bool = False) -> int:
    banco = Banco(config.BANCO)
    coleta_id = banco.iniciar_coleta("demo" if demo else origem)
    if coleta_id is None:
        log.info("Já existe uma coleta em andamento; nada a fazer.")
        return 0
    try:
        if demo:
            from .demo import gerar
            dados = gerar()
        else:
            from .coletor import Coletor, ConfigColetor
            if not (config.EFFORT_USUARIO and config.EFFORT_SENHA):
                raise RuntimeError("Defina EFFORT_USUARIO e EFFORT_SENHA no .env.")
            dados = Coletor(ConfigColetor(
                url_base=config.EFFORT_URL, usuario=config.EFFORT_USUARIO, senha=config.EFFORT_SENHA,
                headless=config.HEADLESS and not visivel, liberar_acesso=config.EFFORT_LIBERAR_ACESSO,
                empresa=config.EFFORT_EMPRESA,
            )).coletar()
        if dados.empty:
            raise RuntimeError("A coleta não trouxe nenhuma OS; nada foi gravado.")
        banco.concluir_coleta(coleta_id, dados)
        log.info("Coleta %s concluída: %s OS.", coleta_id, len(dados))
        return 0
    except Exception as e:  # noqa: BLE001 - qualquer falha vira registro no banco
        log.error("Coleta %s falhou: %s", coleta_id, e)
        banco.falhar_coleta(coleta_id, f"{e}\n\n{traceback.format_exc()}")
        return 1
    except KeyboardInterrupt:  # Ctrl+C: libera a trava para a próxima coleta poder rodar
        log.warning("Coleta %s interrompida.", coleta_id)
        banco.falhar_coleta(coleta_id, "Coleta interrompida pelo usuário (Ctrl+C).")
        return 130


def semear_historico(dias: int) -> None:
    """Grava uma coleta fictícia por dia, dos últimos `dias` dias, com o backlog variando de leve."""
    import random
    banco = Banco(config.BANCO)
    manha = dt.datetime.now().replace(hour=7, minute=0, second=0, microsecond=0)
    n = 330
    for atras in range(dias - 1, -1, -1):
        n += random.Random(atras).randint(-14, 8)
        coleta_id = banco.iniciar_coleta("demo")
        if coleta_id is None:
            log.info("Já existe uma coleta em andamento; nada a fazer.")
            return
        from .demo import gerar
        banco.concluir_coleta(coleta_id, gerar(n, semente=atras))
        banco.datar_coleta(coleta_id, manha - dt.timedelta(days=atras))
    log.info("Histórico fictício criado: %s dias.", dias)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="gera dados fictícios em vez de acessar o Effort")
    ap.add_argument("--demo-dias", type=int, metavar="N", help="como --demo, mas cria N dias de histórico")
    ap.add_argument("--visivel", action="store_true", help="mostra o navegador durante a coleta")
    ap.add_argument("--origem", default="manual", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.demo_dias:
        semear_historico(a.demo_dias)
        sys.exit(0)
    sys.exit(executar(origem=a.origem, demo=a.demo, visivel=a.visivel))


if __name__ == "__main__":
    main()
