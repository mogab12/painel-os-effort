# streamlit run app.py
from __future__ import annotations

import datetime as dt
import subprocess
import sys

import altair as alt
import pandas as pd
import streamlit as st

from painel import config
from painel.aba_historico import aba_historico
from painel.banco import Banco
from painel.estilo import css, faixa, legenda_grafico, paleta
from painel.regras import Regras, classificar, resumo

st.set_page_config(page_title="Fila de OS | MEH", page_icon=":material/stethoscope:", layout="wide")
P = paleta()
st.markdown(css(), unsafe_allow_html=True)

QUEM = ["MEH", "Fornecedor / terceiros", "Suprimentos / financeiro", "Não classificado"]
COR_QUEM = P["quem"]
FAC = ["Rápida", "Trabalho da MEH", "Depende de outros"]
COR_FAC = P["fac"]
IDADES = ["0–30 dias", "31–90 dias", "91–180 dias", "Mais de 180 dias"]
COR_IDADE = P["idade"]
PEQUENAS = {"de", "da", "do", "das", "dos", "e"}

COLUNAS_TABELA = {
    "facilidade": "Facilidade", "os": "OS", "codigo": "Código", "equipamento": "Equipamento",
    "setor": "Setor", "tipo": "Tipo", "ultima_pendencia": "Última pendência", "prazo_txt": "Prazo",
    "acao_sugerida": "Ação sugerida", "responsavel": "Responsável",
}


def nome_bonito(n: str) -> str:
    if not n or n != n.upper():
        return n
    return " ".join(p.lower() if p.lower() in PEQUENAS and i else p.capitalize() for i, p in enumerate(n.lower().split()))


def nomes_curtos(nomes) -> dict:
    # primeiro nome, ou primeiro + último se repetir
    primeiro = {n: n.split()[0] if n.split() else n for n in nomes}
    repetidos = {p for p in primeiro.values() if list(primeiro.values()).count(p) > 1}
    return {n: (f"{p} {n.split()[-1]}" if p in repetidos and len(n.split()) > 1 else p) for n, p in primeiro.items()}


def pendencia_bonita(p: str) -> str:
    import re
    p = re.sub(r"^\s*\([^)]*\)\s*", "", p or "")
    return p.capitalize() if p else "(sem pendência)"


if config.SENHA_PAINEL:
    if not st.session_state.get("ok"):
        st.title("Fila de OS da MEH")
        s = st.text_input("Senha", type="password", autocomplete="current-password")
        if s and s == config.SENHA_PAINEL:
            st.session_state.ok = True
            st.rerun()
        elif s:
            st.error("Senha incorreta. Confira e tente de novo.")
        st.stop()

banco = Banco(config.BANCO)
regras = Regras.carregar(config.REGRAS)


@st.cache_data(show_spinner=False)
def carregar(coleta_id: int, regras_mtime: float, hoje: dt.date) -> pd.DataFrame:
    df = classificar(banco.os_da_coleta(coleta_id), regras, hoje)
    df["responsavel"] = df["responsavel"].map(nome_bonito)
    df["ultima_pendencia_txt"] = df["ultima_pendencia"].map(pendencia_bonita)
    return df


def disparar_coleta():
    subprocess.Popen([sys.executable, "-m", "painel.coletar", "--origem", "manual"],
                     cwd=str(config.RAIZ), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


ultima = banco.ultima_coleta("ok")
recente = banco.ultima_coleta(None)
andamento = banco.effort_ocupado()

topo, botao = st.columns([5, 1])
with topo:
    st.title("Fila de OS · Manutenção de Equipamentos Hospitalares")
    if ultima:
        quando = dt.datetime.fromisoformat(ultima["fim"] or ultima["inicio"])
        st.caption(f"Atualizado em {quando:%d/%m/%Y, %H:%M}")
with botao:
    st.write("")
    if andamento:
        rotulo = "Registro mensal…" if isinstance(andamento, dict) and andamento.get("tipo") == "snapshots" else "Coletando…"
        st.button(rotulo, disabled=True, use_container_width=True)
    elif st.button("Atualizar agora", type="primary", use_container_width=True,
                   help="Busca os dados no Effort. Leva alguns minutos."):
        disparar_coleta()
        st.toast("Coleta iniciada.")
        andamento = True

if andamento:
    @st.fragment(run_every=15)
    def _vigiar():
        if not banco.effort_ocupado():
            st.rerun(scope="app")
    _vigiar()

if recente and recente["status"] == "erro" and (not ultima or recente["id"] > ultima["id"]):
    with st.expander("Última coleta falhou; mostrando a anterior"):
        st.code(recente["erro"] or "", language=None)

if not ultima:
    st.warning("Sem dados ainda. Clique em Atualizar agora.")
    st.stop()

df = carregar(ultima["id"], config.REGRAS.stat().st_mtime, dt.date.today())
r = resumo(df, regras)
curto = nomes_curtos(df["responsavel"].unique())

desconhecidas = sorted(df.loc[df["regra_desconhecida"], "ultima_pendencia"].unique())
if desconhecidas:
    st.warning("Pendências sem regra em `regras.yaml`: " + "; ".join(desconhecidas) + ".")


FONTE = "'Source Sans 3', 'Segoe UI', sans-serif"


def estilizar(grafico):
    return (grafico.configure_view(stroke=None)
            .configure_axis(domain=False, ticks=False, gridColor=P["grade"], gridOpacity=0.25,
                            labelColor=P["texto_graf"], titleColor=P["texto_graf"], labelFont=FONTE,
                            titleFont=FONTE)
            .configure_legend(labelColor=P["texto_graf"], labelFont=FONTE, symbolType="square"))


def barras(dados: pd.DataFrame, eixo: str, cor: str, dominio: list, cores: list, titulo_eixo="",
           ordenar=None, limite=None, altura_barra=30):
    g = dados.groupby([eixo, cor]).size().reset_index(name="OS")
    tot = g.groupby(eixo)["OS"].sum().sort_values(ascending=False)
    ordem = ordenar or list(tot.index)
    if limite:
        ordem = ordem[:limite]
        g = g[g[eixo].isin(ordem)]
    presentes = [(d, c) for d, c in zip(dominio, cores) if d in set(g[cor])]
    st.markdown(legenda_grafico([d for d, _ in presentes], [c for _, c in presentes]), unsafe_allow_html=True)
    eixo_y = alt.Axis(labelLimit=320, labelOverlap=False, labelPadding=8)
    base = alt.Chart(g).mark_bar(cornerRadius=1).encode(
        y=alt.Y(f"{eixo}:N", sort=ordem, title=None, axis=eixo_y),
        x=alt.X("sum(OS):Q", title=None, axis=alt.Axis(tickMinStep=1, format="d")),
        color=alt.Color(f"{cor}:N", scale=alt.Scale(domain=dominio, range=cores), legend=None),
        order=alt.Order(f"{cor}_ordem:Q"),
        tooltip=[alt.Tooltip(f"{eixo}:N", title=titulo_eixo or eixo), alt.Tooltip(f"{cor}:N", title=""), "OS:Q"],
    ).transform_calculate(**{f"{cor}_ordem": f"indexof({dominio}, datum['{cor}'])"})
    totais = alt.Chart(tot.loc[ordem].reset_index(name="total")).mark_text(
        align="left", dx=5, color=P["texto_graf"], fontWeight=600).encode(
        y=alt.Y(f"{eixo}:N", sort=ordem), x="total:Q", text="total:Q")
    st.altair_chart(estilizar((base + totais).properties(height=alt.Step(altura_barra))),
                    use_container_width=True)


def tabela(dados: pd.DataFrame, chave: str, mostrar_resp=True):
    cols = [c for c in COLUNAS_TABELA if mostrar_resp or c != "responsavel"]
    vis = dados.assign(ultima_pendencia=dados["ultima_pendencia_txt"],
                       responsavel=dados["responsavel"].map(curto))[cols].rename(columns=COLUNAS_TABELA)
    st.dataframe(vis, hide_index=True, use_container_width=True, height=min(640, 38 + 35 * len(vis)),
                 column_config={
                     "Prazo": st.column_config.TextColumn(width="medium",
                                                          help="Prazo de conclusão da pendência em aberto que vence primeiro"),
                     "Ação sugerida": st.column_config.TextColumn(width="large"),
                 })
    st.download_button("Baixar CSV", dados.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"os_{chave}_{dt.date.today():%Y-%m-%d}.csv", mime="text/csv", key=f"csv-{chave}")


def filtrar(dados: pd.DataFrame, escolha: str) -> pd.DataFrame:
    return {
        "Prioritárias": dados[dados["prioritaria"]],
        "Rápidas": dados[dados["facilidade"] == "Rápida"],
        "Trabalho da MEH": dados[dados["facilidade"] == "Trabalho da MEH"],
        "Dependem de outros": dados[dados["facilidade"] == "Depende de outros"],
        "Possível duplicidade": dados[dados["duplicada"]],
        "Expiradas": dados[dados["expirada"]],
    }.get(escolha, dados)


por_resp = df.groupby("responsavel").size().sort_values(ascending=False)
abas = st.tabs(["Visão geral"] + [f"{curto[n]} ({q})" for n, q in por_resp.items()] + ["Histórico"])

with abas[0]:
    contagem = {q: int((df["depende_de"] == q).sum()) for q in QUEM}
    st.markdown(faixa(contagem, dict(zip(QUEM, COR_QUEM)),
                      "OS abertas"), unsafe_allow_html=True)
    m = st.columns(6)
    m[0].metric("OS abertas", r["total"])
    m[1].metric("Dependem da MEH", r["ec"])
    m[2].metric("Dependem de outros", r["externo"])
    m[3].metric("Rápidas", r["rapidas"], help="Fechar OS, registrar pendência ou cobrar certificado")
    m[4].metric("Pendências expiradas", r["expiradas"], help="Pendências em aberto que já passaram do prazo de conclusão")
    m[5].metric(f"Mais de {regras.limiares['antiga_dias']} dias", r["antigas"])

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Quem toma ação")
        barras(df, "depende_de", "faixa_idade", IDADES, COR_IDADE, ordenar=[q for q in QUEM if q in set(df["depende_de"])])
    with c2:
        st.subheader("OS por responsável")
        barras(df.assign(responsavel=df["responsavel"].map(curto)), "responsavel", "facilidade", FAC, COR_FAC)

    st.subheader("Última pendência")
    barras(df, "ultima_pendencia_txt", "depende_de", QUEM, COR_QUEM)

    c3, c4 = st.columns(2)
    with c3:
        st.subheader("Setores com mais OS")
        barras(df, "setor", "depende_de", QUEM, COR_QUEM, limite=12)
    with c4:
        st.subheader("Backlog")
        hist = banco.historico()
        if hist.empty or hist["dia"].nunique() < 2:
            st.caption("Aparece após coletas em dois dias.")
        else:
            ev = hist.groupby("dia").size().reset_index(name="total")
            ev["dia"] = pd.to_datetime(ev["dia"])
            folga = max(3, (ev["total"].max() - ev["total"].min()) * 0.25)
            linha = alt.Chart(ev).encode(
                x=alt.X("dia:T", title=None, axis=alt.Axis(format="%d/%m", labelAngle=0, tickCount=min(len(ev), 7))),
                y=alt.Y("total:Q", title=None, scale=alt.Scale(domain=[ev["total"].min() - folga, ev["total"].max() + folga], nice=True),
                        axis=alt.Axis(tickCount=5, format="d")),
                tooltip=[alt.Tooltip("dia:T", title="Dia", format="%d/%m/%Y"), alt.Tooltip("total:Q", title="OS abertas")])
            pontos = linha.mark_line(color=P["destaque"], strokeWidth=2.5) + linha.mark_point(
                color=P["destaque"], filled=True, size=55)
            rotulos = alt.Chart(ev.iloc[[0, -1]]).mark_text(dy=-12, color=P["texto_graf"], fontWeight=600).encode(
                x="dia:T", y="total:Q", text="total:Q")
            st.altair_chart(estilizar((pontos + rotulos).properties(height=300)), use_container_width=True)

    st.subheader("Fila de ação")
    esc = st.radio("Mostrar", ["Prioritárias", "Expiradas", "Rápidas", "Trabalho da MEH", "Dependem de outros",
                               "Possível duplicidade", "Todas"], horizontal=True, key="f-geral", label_visibility="collapsed")
    busca = st.text_input("Buscar", placeholder="Buscar OS, equipamento ou setor…", key="b-geral",
                          label_visibility="collapsed", autocomplete="off")
    sel = filtrar(df, esc)
    if busca:
        sel = sel[sel.astype(str).apply(lambda l: busca.lower() in " ".join(l).lower(), axis=1)]
    tabela(sel, "geral")

with abas[-1]:
    aba_historico(banco, config.RAIZ / "alertas.yaml")

for aba, (nome, qtd) in zip(abas[1:-1], por_resp.items()):
    with aba:
        d = df[df["responsavel"] == nome]
        st.caption(nome)
        m = st.columns(5)
        m[0].metric("OS", qtd)
        m[1].metric("Rápidas", int((d["facilidade"] == "Rápida").sum()))
        m[2].metric("Trabalho da MEH", int((d["facilidade"] == "Trabalho da MEH").sum()))
        m[3].metric("Dependem de outros", int((d["facilidade"] == "Depende de outros").sum()))
        m[4].metric("Pendências expiradas", int(d["expirada"].sum()))
        esc = st.radio("Mostrar", ["Todas", "Expiradas", "Rápidas", "Trabalho da MEH", "Dependem de outros",
                                   "Possível duplicidade"],
                       horizontal=True, key=f"f-{nome}", label_visibility="collapsed")
        tabela(filtrar(d, esc), "-".join(nome.lower().split()), mostrar_resp=False)
