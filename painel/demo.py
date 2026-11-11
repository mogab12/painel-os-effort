from __future__ import annotations

import datetime as dt
import random

import pandas as pd

PENDENCIAS = [
    ("", 25), ("(MEH) FECHAR OS", 5), ("(MEH) AGUARDANDO ANALISE DO EQUIPAMENTO", 60),
    ("(MEH) AGUARDANDO EXECUÇÃO DO SERVIÇO TERCEIROS", 75), ("(MEH) AGUARDANDO SETOR COMPRAS", 50),
    ("(MEH) AGUARDANDO CERTIFICADO", 13), ("(MEH) AGUARDANDO RETORNO DO EQUIPAMENTO", 16),
    ("(MEH) AGUARDANDO PEÇA", 11), ("(MEH) AGUARDANDO EMPENHO", 7), ("(MEH) AGUARDANDO ORÇAMENTO", 6),
    ("(MEH) AGUARDANDO SETOR FINANCEIRO", 6), ("(MEH) SOLICITAÇAO DE TRANSPORTE", 5),
    ("(MEH) AGUARDANDO APROVAÇÃO DE ORÇAMENTO", 3), ("(MEH) AGUARDANDO ADIANTAMENTO", 3),
]
EQUIPAMENTOS = ["INCUBADORA", "BOMBA DE INFUSAO", "VENTILADOR", "MICROSCOPIO", "AUTOCLAVE", "CAMA",
                "MONITOR MULTIPARAMETRICO", "GASTROSCOPIO", "FOTOTERAPIA", "APARELHO DE ANESTESIA"]
SETORES = ["UNIDADE NEONATAL", "CENTRO CIRURGICO", "LABORATORIO CLINICO", "UTI ADULTO", "ENDOSCOPIA",
           "ANATOMIA PATOLOGICA", "RADIOLOGIA", "UTI PEDIATRICA", "CENTRAL DE MATERIAL E ESTERILIZAÇAO"]
TECNICOS = ["TECNICO A", "TECNICO B", "TECNICO C", "TECNICO D", "TECNICO E"]


def gerar(n: int = 300, semente: int | None = None) -> pd.DataFrame:
    rnd = random.Random(semente)
    hoje = dt.date.today()
    pend, pesos = zip(*PENDENCIAS)
    linhas = []
    for i in range(n):
        p = rnd.choices(pend, pesos)[0]
        eq = rnd.choice(EQUIPAMENTOS)
        dias = int(rnd.expovariate(1 / 60))
        situacao = "A" if (p == "" and rnd.random() < 0.8) else "P"
        # prazo entre 5 e 60 dias
        pend_abertura = pend_prazo = ""
        if situacao == "P" and p:
            inicio = dt.datetime.combine(hoje, dt.time(rnd.randint(7, 17), rnd.choice([0, 15, 30, 45]))) \
                - dt.timedelta(days=rnd.randint(0, dias))
            pend_abertura = f"{inicio:%d/%m/%Y %H:%M}"
            pend_prazo = f"{inicio + dt.timedelta(days=rnd.randint(5, 60)):%d/%m/%Y %H:%M}"
        linhas.append({
            "os": f"2026{90000 + i:05d}",
            "codigo": f"{eq[:4]}-{rnd.randint(1, 40):04d}",
            "equipamento": eq, "modelo": f"{eq} - MODELO X", "setor": rnd.choice(SETORES), "plano": "",
            "tipo": rnd.choice(["CORRETIVA", "CORRETIVA", "PREVENTIVA"]),
            "situacao": situacao,
            "abertura": (hoje - dt.timedelta(days=dias)).strftime("%d/%m/%Y"),
            "ultima_pendencia": p, "responsavel": rnd.choice(TECNICOS),
            "pendencia_abertura": pend_abertura, "pendencia_prazo": pend_prazo,
        })
    return pd.DataFrame(linhas)
