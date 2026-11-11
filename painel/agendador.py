# python -m painel.agendador
from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from . import config
from .coletar import executar

log = logging.getLogger("painel.agendador")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    agenda = BlockingScheduler(timezone=config.FUSO)
    agenda.add_job(lambda: executar(origem="agendada"), CronTrigger.from_crontab(config.AGENDA_CRON, timezone=config.FUSO),
                   id="coleta", max_instances=1, coalesce=True, misfire_grace_time=3600)
    log.info("Agendador iniciado: '%s' (%s).", config.AGENDA_CRON, config.FUSO)
    agenda.start()


if __name__ == "__main__":
    main()
