"""Relatórios da fotografia mensal: contratos e qualidade do cadastro. Limites em alertas.yaml."""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from pathlib import Path

import pandas as pd
import yaml

PADRAO = {"contratos": {"a_vencer_dias": [30, 60, 90], "situacoes_sem_contrato": ["COMODATO", "LOCADO"]},
          "qualidade": {"criticidades_exigem_plano": ["ALTA", "MEDIA"]}}


def carregar(caminho: str | Path) -> dict:
    p = Path(caminho)
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    return {k: {**v, **(cfg.get(k) or {})} for k, v in PADRAO.items()}


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", str(t)).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]+", " ", t.upper()).strip()


def _valor(t: str) -> float:
    try:
        return float(str(t).replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0


def _ativos(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["ate"].isna()] if "ate" in df else df


def _sem_sufixo(numero: str) -> str:
    return re.sub(r" \(\d+\)$", "", str(numero)).strip()


def relatorio_contratos(contratos: pd.DataFrame, equipamentos: pd.DataFrame, cfg: dict, hoje: dt.date,
                        contratos_equip: pd.DataFrame | None = None) -> pd.DataFrame:
    """Uma linha por problema: chave, descrição, problema. contratos_equip (contrato x equipamento) permite
    apontar contrato vigente sem equipamento vinculado; sem ele, essa verificação não é feita."""
    c = _ativos(contratos).copy()
    lim = sorted(cfg["contratos"]["a_vencer_dias"])
    fim = pd.to_datetime(c["vigencia_fim"], format="%d/%m/%Y", errors="coerce")
    dias = (fim - pd.Timestamp(hoje)).dt.days
    saida = []
    rotulo = lambda r: f"{r['fornecedor']} - {r['descricao']}".strip(" -")
    encerrado = c["situacao"].str.upper().str.startswith("ENCERRADO")
    com_equip = set() if contratos_equip is None else set(_ativos(contratos_equip)["numero"].map(_sem_sufixo))
    for (_, r), d, enc in zip(c.iterrows(), dias, encerrado):
        if pd.notna(d) and d < 0 and not enc:
            saida.append((r["numero"], rotulo(r), f"Vencido há {-int(d)} dias ({r['vigencia_fim']})"))
        elif pd.notna(d) and d >= 0:
            faixa = next((n for n in lim if d <= n), None)
            if faixa is not None:
                saida.append((r["numero"], rotulo(r), f"Vence em {int(d)} dias ({r['vigencia_fim']}): faixa de {faixa} dias"))
        if not enc and _valor(r["valor"]) == 0:
            saida.append((r["numero"], rotulo(r), "Valor zerado"))
        if contratos_equip is not None and not enc and (pd.isna(d) or d >= 0) and \
                _sem_sufixo(r["numero"]) not in com_equip:
            saida.append((r["numero"], rotulo(r), "Contrato vigente sem equipamentos vinculados"))
    base = c["numero"].str.replace(r" \(\d+\)$", "", regex=True)
    for num in sorted(base[base.duplicated(keep=False)].unique()):
        saida.append((num, "", f"Número repetido em {int((base == num).sum())} contratos"))
    sit = [s.upper() for s in cfg["contratos"]["situacoes_sem_contrato"]]
    e = _ativos(equipamentos)
    sem = e[(e["inativo"] != "SIM") & e["situacao"].str.upper().isin(sit) & (e["tem_contrato"] == "NÃO")]
    for _, r in sem.iterrows():
        saida.append((r["tag"], f"{r['descricao']} ({r['setor']})", f"Equipamento {r['situacao']} sem contrato"))
    return pd.DataFrame(saida, columns=["chave", "descricao", "problema"])


def relatorio_qualidade(equipamentos: pd.DataFrame, planos: pd.DataFrame, setores: pd.DataFrame,
                        fornecedores: pd.DataFrame, contratos: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Uma linha por problema: cadastro, chave, descrição, problema."""
    saida = []
    e = _ativos(equipamentos)
    e = e[e["inativo"] != "SIM"]
    for _, r in e.iterrows():
        desc = f"{r['descricao']} ({r['setor']})"
        if r["tag"].startswith("(sem TAG)"):
            saida.append(("Equipamentos", r["tag"], desc, "Equipamento sem TAG"))
        faltam = [n for n, col in (("criticidade", "criticidade"), ("prioridade", "prioridade")) if not r[col]]
        if faltam:
            saida.append(("Equipamentos", r["tag"], desc, "Sem " + " e sem ".join(faltam)))
        if r["criticidade"] in cfg["qualidade"]["criticidades_exigem_plano"] and r["tem_plano"] == "NÃO":
            saida.append(("Equipamentos", r["tag"], desc, f"Criticidade {r['criticidade']} sem plano de manutenção"))
    p = _ativos(planos)
    for _, r in p[(p["ativo"] == "SIM") & (p["periodicidade"] == "")].iterrows():
        saida.append(("Planos", r["id"], r["nome"], "Plano ativo sem periodicidade"))
    s = _ativos(setores)
    for _, r in s.iterrows():
        nome = f"{r['sigla']} - {r['nome']}".strip(" -")
        if not r["sigla"]:
            saida.append(("Setores", r["id"], nome, "Setor sem sigla"))
        if r["nome"].endswith("."):
            saida.append(("Setores", r["id"], nome, "Nome do setor termina com ponto"))
    # fornecedor com contrato (ativo) sem CNPJ ou telefone; o contrato só traz o nome, então o cruzamento é pelo nome
    f = _ativos(fornecedores)
    por_nome = {_norm(n): r for n, (_, r) in zip(f["razao_social"], f.iterrows())}
    vistos = set()
    for nome in _ativos(contratos)["fornecedor"]:
        r = por_nome.get(_norm(nome))
        if r is None or r["codigo"] in vistos:
            continue
        vistos.add(r["codigo"])
        faltam = [n for n, col in (("CNPJ", "cnpj"), ("telefone", "telefone")) if not r[col]]
        if faltam:
            saida.append(("Fornecedores", r["codigo"], r["razao_social"], "Fornecedor com contrato sem " + " e sem ".join(faltam)))
    return pd.DataFrame(saida, columns=["cadastro", "chave", "descricao", "problema"])
