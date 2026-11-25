"""Compara duas fotografias de um cadastro por chave: o que entrou, o que saiu e cada campo que mudou."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass
class Mudancas:
    entrou: pd.DataFrame   # linhas novas (colunas do cadastro)
    saiu: pd.DataFrame     # linhas que sumiram
    mudou: pd.DataFrame    # uma linha por campo alterado: chave, campo, antes, depois

    @property
    def vazio(self) -> bool:
        return self.entrou.empty and self.saiu.empty and self.mudou.empty

    def resumo(self) -> dict:
        return {"entrou": len(self.entrou), "saiu": len(self.saiu),
                "mudou": self.mudou["chave"].nunique() if not self.mudou.empty else 0}


def _normalizar(df: pd.DataFrame, chave: str) -> pd.DataFrame:
    # vazio, NaN e espaços contam como o mesmo valor
    df = df.fillna("").astype(str).apply(lambda s: s.str.strip())
    df = df[df[chave] != ""]
    return df.drop_duplicates(subset=chave, keep="first").set_index(chave)


def comparar(antes: pd.DataFrame, depois: pd.DataFrame, chave: str) -> Mudancas:
    a, d = _normalizar(antes, chave), _normalizar(depois, chave)
    entrou = d.loc[d.index.difference(a.index, sort=False)].reset_index()
    saiu = a.loc[a.index.difference(d.index, sort=False)].reset_index()

    comuns = a.index.intersection(d.index, sort=False)
    campos = [c for c in d.columns if c in a.columns]
    linhas = []
    for campo in campos:
        va, vd = a.loc[comuns, campo], d.loc[comuns, campo]
        for k in comuns[(va != vd).to_numpy()]:
            linhas.append({"chave": k, "campo": campo, "antes": va[k], "depois": vd[k]})
    mudou = pd.DataFrame(linhas, columns=["chave", "campo", "antes", "depois"])
    if not mudou.empty:
        mudou = mudou.sort_values(["chave", "campo"]).reset_index(drop=True)
    return Mudancas(entrou.sort_values(chave).reset_index(drop=True), saiu.sort_values(chave).reset_index(drop=True), mudou)


def gravar_log(caminho: Path, mudancas: pd.DataFrame, referencia: str):
    """mudancas.xlsx de um mês: um resumo por cadastro e uma aba por cadastro com tipo, chave, campo, antes, depois."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    cols = ["tipo", "chave", "campo", "antes", "depois"]
    with pd.ExcelWriter(caminho, engine="openpyxl") as x:
        if mudancas.empty:
            pd.DataFrame({"referência": [referencia], "mudanças": [0]}).to_excel(x, sheet_name="Resumo", index=False)
            return
        resumo = (mudancas.drop_duplicates(["cadastro", "chave", "tipo"]).groupby(["cadastro", "tipo"]).size()
                  .unstack(fill_value=0).reindex(columns=["entrou", "saiu", "mudou", "voltou"], fill_value=0).reset_index())
        resumo.insert(0, "referência", referencia)
        resumo.to_excel(x, sheet_name="Resumo", index=False)
        for cadastro, df in mudancas.groupby("cadastro"):
            df[cols].to_excel(x, sheet_name=str(cadastro)[:31], index=False)
