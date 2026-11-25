"""Funções comuns para mostrar OS e atividades (zip de pastas e aba Histórico).

Tipos de atividade do Effort (ícone ico_<tipo>.png na tela de consulta da OS):
  ocorrenciasolucionada  defeito relatado, com a causa
  ocorrenciaservico      serviço realizado (o que foi feito), com a causa
  pendenciaconcluida     pendência encerrada: aberta em `data`, fechada em `fechada_em`
  pendencia              pendência ainda em aberto
  servico                mão de obra: técnico em `descricao`, horas em `quantidade`
  peca                   material: código e descrição, quantidade, valor unitário e total
  servicoexterno         empresa em `descricao`, valor
  autor / anexo / foto   assinaturas e anexos (os arquivos não são baixados)
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

ATIVIDADES = {"pendenciaconcluida": "Pendência", "pendencia": "Pendência", "ocorrenciaservico": "Serviço realizado",
              "ocorrenciasolucionada": "Defeito", "ocorrencia": "Ocorrência", "servico": "Mão de obra",
              "servicoexterno": "Serviço externo", "peca": "Material (peça)", "anexo": "Anexo", "foto": "Foto",
              "autor": "Assinatura", "procedimento": "Procedimento", "procedimentoajuste": "Procedimento"}


def reais(s: pd.Series) -> pd.Series:
    """'1.234,56' -> 1234.56; vazio -> NaN."""
    return pd.to_numeric(s.fillna("").astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
                         errors="coerce")


def formatar_reais(v) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if pd.notna(v) else ""


def data_hora(s: pd.Series) -> pd.Series:
    """'dd/mm/aaaa hh:mm' ou 'dd/mm/aaaa' -> datetime."""
    s = s.fillna("").astype(str).str.strip()
    return pd.to_datetime(s, format="%d/%m/%Y %H:%M", errors="coerce").fillna(
        pd.to_datetime(s.str[:10], format="%d/%m/%Y", errors="coerce"))


def sem_prefixo(texto) -> str:
    """'(MEH) AGUARDANDO PEÇA' -> 'AGUARDANDO PEÇA'."""
    texto = str(texto or "")
    return texto[6:] if texto.startswith("(MEH) ") else texto


def pendencias(a: pd.DataFrame, agora: dt.datetime | None = None) -> pd.DataFrame:
    """Pendências de uma ou mais OS, em ordem de abertura, com os dias em aberto (até hoje, se não fechou)."""
    p = a[a["tipo"].astype(str).str.startswith("pendencia")].copy()
    p["inicio"], p["fim"] = data_hora(p["data"]), data_hora(p["fechada_em"])
    p["em_aberto"] = p["fim"].isna()
    ate = p["fim"].fillna(pd.Timestamp(agora or dt.datetime.now()))
    p["dias"] = ((ate - p["inicio"]).dt.total_seconds() / 86400).round(0)
    p["pendencia"] = p["descricao"].map(sem_prefixo)
    return p.sort_values(["os", "inicio"])
