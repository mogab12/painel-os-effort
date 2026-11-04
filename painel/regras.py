"""Classificação das OS a partir das regras em regras.yaml.

Entrada: DataFrame "bruto" vindo do coletor, com as colunas
    os, codigo, equipamento, setor, tipo, situacao, abertura, ultima_pendencia, responsavel
(situacao é "A" para aberta ou "P" para pendente; abertura é uma data) e, opcionalmente,
    pendencia_abertura, pendencia_prazo   ("dd/mm/aaaa hh:mm" da pendência em aberto que vence primeiro)

Saída: o mesmo DataFrame com as colunas calculadas
    dias_aberta, dias_pendencia, dias_para_vencer, expirada, prazo_txt, faixa_idade, depende_de,
    ordem_facilidade, facilidade, acao_sugerida, duplicada, qtd_os_equipamento, prioritaria,
    regra_desconhecida
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

COLUNAS_BRUTAS = [
    "os", "codigo", "equipamento", "setor", "tipo", "situacao",
    "abertura", "ultima_pendencia", "responsavel",
]


def normalizar(texto: str | None) -> str:
    """Maiúsculas, sem acentos, sem prefixo entre parênteses e com espaços simples."""
    if not texto:
        return ""
    t = unicodedata.normalize("NFD", str(texto))
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").upper()
    t = re.sub(r"^\s*\([^)]*\)\s*", "", t)  # "(MEH) AGUARDANDO..." -> "AGUARDANDO..."
    return re.sub(r"\s+", " ", t).strip()


@dataclass
class Regras:
    bruto: dict

    @classmethod
    def carregar(cls, caminho: str | Path = "regras.yaml") -> "Regras":
        with open(caminho, encoding="utf-8") as f:
            return cls(yaml.safe_load(f))

    @property
    def limiares(self) -> dict:
        return self.bruto["limiares"]

    def faixa(self, lista: str, chave: str, valor: int) -> str:
        for item in self.bruto[lista]:
            if valor <= item[chave]:
                return item["nome"]
        return self.bruto[lista][-1]["nome"]

    def regra_pendencia(self, pendencia: str) -> tuple[dict, bool]:
        """Devolve (regra, reconhecida)."""
        p = normalizar(pendencia)
        for regra in self.bruto["pendencias"]:
            if normalizar(regra["contem"]) in p:
                return regra, True
        return self.bruto["pendencia_desconhecida"], False


def texto_prazo(dias: float) -> str:
    """Dias até o prazo de conclusão (negativo = já passou) em texto para a tabela."""
    if pd.isna(dias):
        return "Sem prazo"
    d = int(dias)
    if d < 0:
        return f"Expirada há {-d} dia{'s' if d < -1 else ''}"
    if d == 0:
        return "Vence hoje"
    return f"Vence em {d} dia{'s' if d > 1 else ''}"


def _classificar_linha(linha: pd.Series, regras: Regras) -> dict:
    # idade da pendência atual (desde que ela foi aberta); sem essa data, usa a idade da OS
    dias = int(linha["dias_pendencia"])
    pend = (linha.get("ultima_pendencia") or "").strip()
    reconhecida = True

    if not pend:
        chave = "aberta" if linha["situacao"] == "A" else "pendente"
        regra = regras.bruto["sem_pendencia"][chave]
        depende, ordem, acao = regra["depende_de"], regra["ordem"], regra["acao"]
        if chave == "aberta" and normalizar(linha.get("tipo")) == "PEDIDO DE COMPRA":
            acao = regra.get("acao_pedido_compra", acao)
    else:
        regra, reconhecida = regras.regra_pendencia(pend)
        depende, ordem, acao = regra["depende_de"], regra["ordem"], regra["acao"]
        extra = regra.get("se_mais_de_dias")
        if extra and dias > extra["dias"]:
            ordem, acao = extra["ordem"], extra["acao"]
        elif depende != "MEH" and ordem >= 8 and dias > regras.limiares["escalar_apos_dias"]:
            ordem = 7
            acao = f"Cobrar retorno e escalar (parada há mais de {regras.limiares['escalar_apos_dias']} dias)"

    return {
        "depende_de": depende,
        "ordem_facilidade": ordem,
        "facilidade": regras.faixa("faixas_facilidade", "ate_ordem", ordem),
        "acao_sugerida": acao,
        "regra_desconhecida": not reconhecida,
    }


def classificar(bruto: pd.DataFrame, regras: Regras, hoje: dt.date | None = None) -> pd.DataFrame:
    faltando = [c for c in COLUNAS_BRUTAS if c not in bruto.columns]
    if faltando:
        raise ValueError(f"Colunas ausentes nos dados coletados: {faltando}")
    hoje = hoje or dt.date.today()
    df = bruto.copy()
    df["ultima_pendencia"] = df["ultima_pendencia"].fillna("")
    df["responsavel"] = df["responsavel"].fillna("").replace("", "Sem responsável")
    abertura = pd.to_datetime(df["abertura"], dayfirst=True).dt.date
    df["abertura"] = abertura
    df["dias_aberta"] = [(hoje - d).days for d in abertura]

    def _datas(col):
        txt = df[col].fillna("").astype(str).str.strip() if col in df else pd.Series("", index=df.index)
        return pd.to_datetime(txt.str[:10], format="%d/%m/%Y", errors="coerce")

    hoje_ts = pd.Timestamp(hoje)
    pend_abertura, prazo = _datas("pendencia_abertura"), _datas("pendencia_prazo")
    df["dias_pendencia"] = (hoje_ts - pend_abertura).dt.days.fillna(df["dias_aberta"]).astype(int)
    df["dias_para_vencer"] = (prazo - hoje_ts).dt.days          # NaN quando não há prazo
    df["expirada"] = df["dias_para_vencer"] < 0
    df["prazo_txt"] = df["dias_para_vencer"].map(texto_prazo)
    df["faixa_idade"] = [regras.faixa("faixas_idade", "ate_dias", d) for d in df["dias_aberta"]]

    calc = pd.DataFrame([_classificar_linha(l, regras) for _, l in df.iterrows()], index=df.index)
    df = pd.concat([df, calc], axis=1)

    genericos = set(regras.bruto.get("codigos_genericos", []))
    # duplicidade = mais de uma OS aberta no mesmo equipamento e do mesmo tipo (corretiva + preventiva não conta)
    qtd = df.groupby(["codigo", "tipo"])["os"].transform("count")
    df["qtd_os_equipamento"] = qtd
    df["duplicada"] = (qtd > 1) & ~df["codigo"].isin(genericos)
    df.loc[df["duplicada"], "acao_sugerida"] = (
        df.loc[df["duplicada"], "acao_sugerida"]
        + " · conferir duplicidade ("
        + df.loc[df["duplicada"], "qtd_os_equipamento"].astype(str)
        + " OS do mesmo tipo abertas no mesmo equipamento)"
    )

    lim = regras.limiares["analise_atrasada_dias"]
    df["prioritaria"] = (
        (df["ordem_facilidade"] <= 3)
        | ((df["depende_de"] == "MEH") & (df["dias_pendencia"] > lim))
        | df["duplicada"]
        | df["expirada"]
    )
    # dentro de cada facilidade: das mais atrasadas para as mais folgadas; sem prazo por último
    return df.sort_values(["ordem_facilidade", "dias_para_vencer", "dias_aberta"], ascending=[True, True, False],
                          na_position="last").reset_index(drop=True)


def resumo(df: pd.DataFrame, regras: Regras) -> dict:
    return {
        "total": len(df),
        "ec": int((df["depende_de"] == "MEH").sum()),
        "externo": int((df["depende_de"] != "MEH").sum()),
        "rapidas": int((df["facilidade"] == "Rápida").sum()),
        "antigas": int((df["dias_aberta"] > regras.limiares["antiga_dias"]).sum()),
        "expiradas": int(df["expirada"].sum()),
    }
