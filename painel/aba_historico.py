"""Aba "Histórico" do painel: consulta do registro mensal dos cadastros do Effort (painel/snapshot.py)."""
from __future__ import annotations

import datetime as dt
import io
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from . import config
from .alertas import carregar as carregar_alertas, relatorio_contratos, relatorio_qualidade
from .banco import COLUNAS_SNAPSHOT, Banco

NOMES = {"equipamentos": "Equipamentos", "contratos": "Contratos", "fornecedores": "Fornecedores",
         "fabricantes": "Fabricantes", "modelos": "Modelos", "setores": "Setores", "planos": "Planos",
         "procedimentos": "Procedimentos", "motivos_pendencia": "Motivos de pendência",
         "planos_equipamentos": "Planos x equipamentos", "contratos_equipamentos": "Contratos x equipamentos",
         "os_fechadas": "OS fechadas"}
TIPOS = {"entrou": "Entrou", "saiu": "Saiu do Effort", "mudou": "Mudou", "voltou": "Voltou"}
ATIVIDADES = {"pendenciaconcluida": "Pendência", "pendencia": "Pendência", "ocorrenciaservico": "Ocorrência (serviço)",
              "ocorrenciasolucionada": "Ocorrência solucionada", "ocorrencia": "Ocorrência", "servico": "Serviço",
              "servicoexterno": "Serviço externo", "peca": "Peça", "anexo": "Anexo", "foto": "Foto"}


@st.cache_data(show_spinner=False)
def _ler(caminho: str, snapshot_id: int):
    """Tudo o que a aba usa, relido só quando há registro novo."""
    b = Banco(caminho)
    cad = {e: b.cadastro(e, incluir_removidos=True) for e in COLUNAS_SNAPSHOT}
    return cad, b.mudancas(), b.tabela("os_detalhe"), b.tabela("os_atividades")


@st.cache_data(show_spinner=False, max_entries=2)
def _ler_arquivo(caminho: str, mtime: float) -> bytes:
    return Path(caminho).read_bytes()


def _xlsx(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()


def _reais(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.fillna("").str.replace(".", "", regex=False).str.replace(",", ".", regex=False), errors="coerce")


def _data(texto: str) -> pd.Series:
    return pd.to_datetime(texto, format="%d/%m/%Y", errors="coerce")


def aba_historico(banco: Banco, alertas_yaml: Path):
    snaps = banco.snapshots()
    ok = snaps[snaps["status"] == "ok"]
    if ok.empty:
        st.info("Ainda não há histórico dos cadastros. O registro roda sozinho no dia 1º de cada mês, às 2h, "
                "ou na hora com `python -m painel.snapshot --agora`.")
        return
    ultima = ok.iloc[0]
    cad, mudancas, detalhe, atividades = _ler(str(banco.caminho), int(ultima["id"]))
    totais = json.loads(ultima["totais"] or "{}")

    st.caption(f"Último registro: referência {ultima['referencia']}, feito em "
               f"{pd.to_datetime(ultima['fim']):%d/%m/%Y às %H:%M}. Os cadastros guardam também o que já saiu do Effort.")
    itens = list(NOMES.items())
    for linha in (itens[i:i + 4] for i in range(0, len(itens), 4)):
        for col, (e, nome) in zip(st.columns(4), linha):
            col.metric(nome, int((cad[e]["ate"].isna()).sum()))
    n_fech = int(cad["os_fechadas"]["ate"].isna().sum())
    if totais.get("os_com_detalhe", n_fech) < n_fech:
        st.caption(f"Detalhe das OS fechadas (responsável, custos, atividades): {totais['os_com_detalhe']} de {n_fech} lidas. "
                   "O restante é lido nos próximos registros.")

    # ------------------------------------------------------------------ zip de pastas
    st.subheader("Pastas por equipamento")
    st.caption("Um .zip com uma pasta por equipamento (TAG, descrição, modelo e patrimônio). Dentro, uma pasta por OS, "
               "aberta ou fechada, com a OS completa e as pendências. Abra o indice.html no navegador.")
    zips = sorted(config.SNAPSHOTS.glob("*/historico_equipamentos_*.zip"))
    c1, c2 = st.columns([2, 1])
    if zips:
        z = zips[-1]
        c1.download_button(f"Baixar pastas ({z.stat().st_size / 1e6:.0f} MB, {z.parent.name})", _ler_arquivo(str(z), z.stat().st_mtime),
                           file_name=z.name, mime="application/zip", key="h-zip", type="primary", icon=":material/folder_zip:")
    else:
        c1.caption("O zip é gerado no fim de cada registro mensal.")
    if c2.button("Gerar de novo com os dados atuais", key="h-zip-gerar"):
        from .pastas import gerar_zip
        with st.spinner("Montando as pastas (cerca de 10 segundos)…"):
            gerar_zip(banco, config.SNAPSHOTS / ultima["referencia"] / f"historico_equipamentos_{ultima['referencia']}.zip",
                      ultima["referencia"])
        st.rerun()

    # ------------------------------------------------------------------ equipamento
    st.subheader("Consultar equipamento")
    eq = cad["equipamentos"]
    descricao = dict(zip(eq["tag"], eq["descricao"]))
    tag = st.selectbox("Equipamento", sorted(descricao), index=None, placeholder="Digite a TAG, por exemplo BINF-0290",
                       format_func=lambda t: f"{t}  {descricao[t]}", key="h-tag",
                       label_visibility="collapsed")
    if tag:
        e = eq.set_index("tag").loc[tag]
        situacao = "Saiu do Effort em " + e["ate"] if pd.notna(e["ate"]) else ("Inativo" if e["inativo"] == "SIM" else "Ativo")
        st.markdown(f"**{tag}**, {e['descricao']} ({e['modelo']}, {e['fabricante']}). Setor: {e['setor']}. {situacao}.")
        campos = {"Situação": "situacao", "Criticidade": "criticidade", "Prioridade": "prioridade", "Plano": "tem_plano",
                  "Contrato": "tem_contrato", "Nº de série": "numero_serie", "Patrimônio": "patrimonio",
                  "Registrado desde": "desde"}
        st.dataframe(pd.DataFrame([{k: e[v] for k, v in campos.items()}]), hide_index=True, use_container_width=True)

        hist = mudancas[(mudancas["cadastro"] == "equipamentos") & (mudancas["chave"] == tag) & (mudancas["tipo"] != "entrou")]
        if not hist.empty:
            st.markdown("**Mudanças no cadastro**")
            st.dataframe(hist.assign(tipo=hist["tipo"].map(TIPOS))[["referencia", "tipo", "campo", "antes", "depois"]]
                         .rename(columns={"referencia": "Mês", "tipo": "O quê", "campo": "Campo", "antes": "Antes", "depois": "Depois"}),
                         hide_index=True, use_container_width=True)

        oss = cad["os_fechadas"][cad["os_fechadas"]["tag"] == tag].merge(detalhe, on="os", how="left", suffixes=("", "_d"))
        st.markdown(f"**OS fechadas: {len(oss)}**")
        if not oss.empty:
            oss = oss.assign(_f=_data(oss["fechamento"])).sort_values("_f", ascending=False)
            vis = oss[["os", "tipo", "abertura", "fechamento", "responsavel", "custo_total"]].rename(columns={
                "os": "OS", "tipo": "Tipo", "abertura": "Abertura", "fechamento": "Fechamento",
                "responsavel": "Responsável", "custo_total": "Custo (R$)"})
            st.dataframe(vis.fillna(""), hide_index=True, use_container_width=True)
            ids = set(oss["os"])
            gastos = atividades[atividades["os"].isin(ids) & (atividades["valor_total"].fillna("") != "")]
            if not gastos.empty:
                total = _reais(gastos["valor_total"]).sum()
                st.markdown(f"**Peças e serviços com preço: R$ {total:,.2f}**".replace(",", "X").replace(".", ",").replace("X", "."))
                st.dataframe(gastos.assign(tipo=gastos["tipo"].map(lambda t: ATIVIDADES.get(t, t)))
                             [["os", "data", "tipo", "descricao", "quantidade", "valor_unitario", "valor_total"]]
                             .rename(columns={"os": "OS", "data": "Data", "tipo": "Item", "descricao": "Descrição",
                                              "quantidade": "Qtd.", "valor_unitario": "Valor (R$)", "valor_total": "Total (R$)"}),
                             hide_index=True, use_container_width=True)
            escolhida = st.selectbox("Atividades da OS", list(oss["os"]), index=None, placeholder="Escolha uma OS para ver as atividades",
                                     key="h-os", label_visibility="collapsed")
            if escolhida:
                a = atividades[atividades["os"] == escolhida]
                if a.empty:
                    st.caption("O detalhe desta OS ainda não foi lido; ele entra num dos próximos registros.")
                else:
                    st.dataframe(a.assign(tipo=a["tipo"].map(lambda t: ATIVIDADES.get(t, t)))
                                 [["data", "tipo", "descricao", "fechada_em", "causa", "quantidade", "valor_total"]]
                                 .rename(columns={"data": "Data", "tipo": "Atividade", "descricao": "Descrição",
                                                  "fechada_em": "Pendência fechada em", "causa": "Causa",
                                                  "quantidade": "Qtd.", "valor_total": "Total (R$)"}),
                                 hide_index=True, use_container_width=True)

    # ------------------------------------------------------------------ mudanças do mês
    st.subheader("Mudanças por mês")
    meses = sorted(mudancas["referencia"].unique(), reverse=True)
    if not meses:
        st.caption("Nenhuma mudança registrada ainda.")
        meses = [ultima["referencia"]]
    c1, c2 = st.columns([1, 3])
    mes = c1.selectbox("Mês", meses, key="h-mes")
    doc = mudancas[mudancas["referencia"] == mes]
    if mes == min(meses):
        st.caption("No primeiro mês, todo o cadastro aparece como \"entrou\": é o início do registro.")
    resumo = (doc.drop_duplicates(["cadastro", "chave", "tipo"]).groupby(["cadastro", "tipo"]).size()
              .unstack(fill_value=0).reindex(columns=list(TIPOS), fill_value=0))
    resumo.index = [NOMES.get(i, i) for i in resumo.index]
    c2.dataframe(resumo.rename(columns=TIPOS), use_container_width=True)
    qual = st.radio("Cadastro", list(NOMES), format_func=NOMES.get, horizontal=True, key="h-cad-mud",
                    label_visibility="collapsed")
    d = doc[doc["cadastro"] == qual]
    st.dataframe(d.assign(tipo=d["tipo"].map(TIPOS))[["tipo", "chave", "campo", "antes", "depois"]]
                 .rename(columns={"tipo": "O quê", "chave": "Chave", "campo": "Campo", "antes": "Antes", "depois": "Depois"}),
                 hide_index=True, use_container_width=True, height=min(420, 38 + 35 * max(len(d), 1)))

    # ------------------------------------------------------------------ relatórios
    st.subheader("Relatórios")
    cfg = carregar_alertas(alertas_yaml)
    rel_c = relatorio_contratos(cad["contratos"], cad["equipamentos"], cfg, dt.date.today(),
                                cad["contratos_equipamentos"] if len(cad["contratos_equipamentos"]) else None)
    rel_q = relatorio_qualidade(cad["equipamentos"], cad["planos"], cad["setores"], cad["fornecedores"], cad["contratos"], cfg)
    st.caption("Limites em `alertas.yaml`. Contrato sem equipamentos vem do relatório \"Contratos com Fornecedores\" "
               "(analítico) do Effort.")
    t1, t2 = st.tabs([f"Contratos ({len(rel_c)})", f"Qualidade do cadastro ({len(rel_q)})"])
    with t1:
        st.dataframe(rel_c.rename(columns={"chave": "Contrato / TAG", "descricao": "Descrição", "problema": "Problema"}),
                     hide_index=True, use_container_width=True)
        st.download_button("Baixar relatório de contratos (.xlsx)", _xlsx(rel_c), file_name="relatorio_contratos.xlsx", key="h-dl-rc")
    with t2:
        resumo_q = rel_q.groupby(["cadastro", "problema"]).size().reset_index(name="quantidade")
        resumo_q["problema"] = resumo_q["problema"].str.replace(r"Fornecedor com contrato sem .*", "Fornecedor com contrato sem CNPJ ou telefone", regex=True)
        st.dataframe(resumo_q.groupby(["cadastro", "problema"], as_index=False)["quantidade"].sum()
                     .rename(columns={"cadastro": "Cadastro", "problema": "Problema", "quantidade": "Quantidade"}),
                     hide_index=True, use_container_width=True)
        st.dataframe(rel_q.rename(columns={"cadastro": "Cadastro", "chave": "Chave", "descricao": "Descrição", "problema": "Problema"}),
                     hide_index=True, use_container_width=True, height=360)
        st.download_button("Baixar relatório de qualidade (.xlsx)", _xlsx(rel_q), file_name="relatorio_qualidade.xlsx", key="h-dl-rq")

    # ------------------------------------------------------------------ cadastros completos
    st.subheader("Cadastros")
    qual = st.radio("Cadastro", list(NOMES), format_func=NOMES.get, horizontal=True, key="h-cad",
                    label_visibility="collapsed")
    incluir = st.toggle("Incluir o que já saiu do Effort", key="h-removidos")
    tab = cad[qual] if incluir else cad[qual][cad[qual]["ate"].isna()].drop(columns="ate")
    st.dataframe(tab, hide_index=True, use_container_width=True, height=420)
    c1, c2, c3 = st.columns(3)
    c1.download_button(f"Baixar {NOMES[qual].lower()} (.xlsx)", _xlsx(tab), file_name=f"{qual}.xlsx", key="h-dl-cad")
    c2.download_button("Baixar detalhe das OS (.xlsx)", _xlsx(detalhe), file_name="os_detalhe.xlsx", key="h-dl-det")
    c3.download_button("Baixar atividades das OS (.xlsx)", _xlsx(atividades), file_name="os_atividades.xlsx", key="h-dl-ativ")

    with st.expander("Registros mensais (log)"):
        st.dataframe(snaps[["referencia", "inicio", "fim", "status", "origem"]].rename(columns={
            "referencia": "Referência", "inicio": "Início", "fim": "Fim", "status": "Situação", "origem": "Origem"}),
            hide_index=True, use_container_width=True)
