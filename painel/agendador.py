# python -m painel.agendador
from __future__ import annotations

import logging
import time

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from . import config
from .banco import Banco
from .coletar import executar
from .snapshot import executar as fotografar

log = logging.getLogger("painel.agendador")


def _fotografia_agendada():
    # se uma coleta de OS (ex.: manual) estiver usando o Effort, espera em vez de perder o mês
    for _ in range(24):
        if not Banco(config.BANCO).effort_ocupado():
            return fotografar(origem="agendada")
        log.info("Effort em uso por outra coleta; registro mensal tenta de novo em 10 minutos.")
        time.sleep(600)
    log.error("Registro mensal não rodou: o Effort ficou ocupado por 4 horas.")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    agenda = BlockingScheduler(timezone=config.FUSO)
    agenda.add_job(lambda: executar(origem="agendada"), CronTrigger.from_crontab(config.AGENDA_CRON, timezone=config.FUSO),
                   id="coleta", max_instances=1, coalesce=True, misfire_grace_time=3600)
    agenda.add_job(_fotografia_agendada, CronTrigger.from_crontab(config.SNAPSHOT_CRON, timezone=config.FUSO),
                   id="fotografia", max_instances=1, coalesce=True, misfire_grace_time=6 * 3600)
    log.info("Agendador iniciado: coletas '%s', registro mensal '%s' (%s).", config.AGENDA_CRON, config.SNAPSHOT_CRON, config.FUSO)
    agenda.start()


if __name__ == "__main__":
    main()
