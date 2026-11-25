"""Zip de pastas para consulta sem o app: uma pasta por equipamento, dentro dela uma pasta por OS (aberta ou
fechada) e, dentro de cada OS, as pendências. Tudo em páginas HTML que abrem no navegador, ligadas por links.

    historico_2026-11/
        LEIA-ME.txt
        indice.html                                   todos os equipamentos, com busca
        GASTR-0009 - GASTROSCOPIO - EG-590WR - PAT 12345/
            equipamento.html                          ficha, mudanças no cadastro, lista das OS
            2025-04-01 OS 202500535 CORRETIVA (fechada)/
                OS.html                               datas, quem abriu, técnico, defeito, causa, serviço,
                                                      mão de obra, materiais com preço, serviços externos, custos
                pendencias/
                    01 - AGUARDANDO EMPENHO (115 dias).html

Gerado no fim de cada registro mensal (dados/snapshots/AAAA-MM/) ou pelo botão da aba Histórico.
"""
from __future__ import annotations

import datetime as dt
import html
import io
import logging
import re
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import Path

import pandas as pd

from .banco import Banco
from .historico_os import formatar_reais, reais, sem_prefixo

log = logging.getLogger(__name__)

SITUACOES = {"F": "fechada", "A": "aberta", "P": "pendente", "C": "cancelada"}
LIMITE_PASTA_EQUIP = 70     # caminhos curtos: o Windows não abre caminhos com mais de 260 caracteres
LIMITE_ARQUIVO = 60

CSS = """
/* identidade visual das telas internas do Effort: fundo cinza claro, Tahoma, campos em caixas cinza,
   grades com cabeçalho cinza e linhas alternadas, títulos de janela em laranja */
:root{--laranja:#ff8728;--fundo:#efefef;--painel:#fff;--campo:#dddddd;--cab:#888888;--linha2:#dddddd;
--borda:#808080;--borda2:#e4e4e4;--tx:#000;--tx2:#575757;--alerta:#8b0000;--lateral:#555555;--link:#0000ee}
*{box-sizing:border-box}html{background:var(--fundo)}
body{font:12px/1.35 Tahoma,Arial,sans-serif;color:var(--tx);background:var(--fundo);margin:0}
.topo{background:#fff;border-bottom:1px solid #ccc;display:flex;align-items:center;gap:16px;padding:10px 16px}
.topo b{color:var(--laranja);font:600 22px/1 Montserrat,"Segoe UI",Arial,sans-serif;letter-spacing:-.5px}
.topo span{flex:1;text-align:center;color:var(--tx2);font-size:14px;text-transform:uppercase}
.janela{background:var(--painel);margin:12px;border:1px solid #ccc;min-height:calc(100vh - 90px)}
.janela>.conteudo{padding:8px 10px 24px}
h1{color:var(--laranja);font:bold 15px Tahoma,Arial,sans-serif;margin:0;padding:10px 10px 6px;border-bottom:1px solid var(--borda2)}
.trilha{padding:6px 10px;background:#fff;border-bottom:1px solid var(--borda2);font-size:12px}
.trilha a{color:#333;text-decoration:none;margin-right:14px}.trilha a:hover{text-decoration:underline}
.trilha a::before{content:"▸ ";color:var(--laranja)}
.sub{color:var(--tx2);margin:6px 0 10px}
a{color:var(--link)}
.grade{display:flex;flex-wrap:wrap;gap:6px 18px;background:var(--fundo);border:1px solid var(--borda2);padding:8px 10px}
.grade div{display:flex;align-items:center;gap:6px}
.grade div span{color:var(--tx)}
.grade div b{font-weight:normal;background:var(--campo);padding:2px 6px;min-width:90px;color:var(--tx)}
h2{margin:16px 0 0;padding:4px 8px;background:#777;color:#fff;font:normal 12px Tahoma,Arial,sans-serif}
.rolar{overflow-x:auto;border:1px solid var(--borda)}
table{border-collapse:collapse;width:100%;font-size:11.5px;color:var(--tx2);background:#fff}
th{background:var(--cab);color:#fff;font-weight:normal;text-align:left;padding:4px 8px}
td{padding:4px 8px;border-bottom:1px solid var(--borda);vertical-align:top}
tbody tr:nth-child(even) td{background:var(--linha2)}
td.n,th.n{text-align:right;white-space:nowrap}tr.tot td{font-weight:bold;color:#000;background:#fff!important}
h2+.rolar{border-top:0}
.tag{display:inline-block;padding:1px 6px;font-size:11px;background:var(--campo);color:#000;font-weight:bold}
.tag.aberta{background:var(--alerta);color:#fff}
.vazio{color:var(--tx2);font-style:italic;padding:6px 8px;margin:0;background:#fff;border:1px solid var(--borda2);border-top:0}
input{font:inherit;padding:3px 6px;width:100%;max-width:420px;border:1px solid var(--borda);background:#fff;margin:4px 0 10px}
.busca{display:flex;align-items:center;gap:8px}.busca label{white-space:nowrap}
.rodape{color:var(--tx2);font-size:11px;padding:10px 12px}
@media print{.topo,.trilha{display:none}.janela{margin:0;border:0}html,body{background:#fff}}
"""


# --------------------------------------------------------------------------- utilidades
def _e(v) -> str:
    return html.escape("" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))


def nome_arquivo(texto: str, limite: int) -> str:
    """Nome que abre em qualquer sistema: sem acento, sem caracteres proibidos, curto."""
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    t = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .")
    return (t[:limite].rstrip(" .-") or "sem nome")


def _pagina(titulo: str, corpo: str) -> str:
    return (f"<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>{_e(titulo)}</title>"
            f"<style>{CSS}</style></head><body><div class='topo'><b>Histórico</b><span>Manutenção de Equipamentos</span></div>"
            f"<div class='janela'>{corpo}</div>"
            f"<p class='rodape'>Gerado em {dt.datetime.now():%d/%m/%Y %H:%M} a partir do "
            f"registro mensal do Effort (só leitura). Observações em texto livre e anexos ficam no Effort.</p></body></html>")


def _grade(campos: list[tuple[str, str]]) -> str:
    itens = "".join(f"<div><span>{_e(k)}:</span><b>{_e(v) or '&nbsp;'}</b></div>" for k, v in campos)
    return f"<div class='grade'>{itens}</div>"


def _tabela(cabecalho: list[str], linhas: list[list[str]], numericas: set[int] = frozenset(),
            total: list[str] | None = None, cru: set[int] = frozenset()) -> str:
    """cru: colunas que já vêm em HTML (links)."""
    th = "".join(f"<th class='{'n' if i in numericas else ''}'>{_e(c)}</th>" for i, c in enumerate(cabecalho))
    def tr(l, cls=""):
        return f"<tr class='{cls}'>" + "".join(
            f"<td class='{'n' if i in numericas else ''}'>{v if i in cru else _e(v)}</td>" for i, v in enumerate(l)) + "</tr>"
    corpo = "".join(tr(l) for l in linhas) + (tr(total, "tot") if total else "")
    return f"<div class='rolar'><table><thead><tr>{th}</tr></thead><tbody>{corpo}</tbody></table></div>"


def _dt(texto):
    """'dd/mm/aaaa hh:mm' ou 'dd/mm/aaaa' -> Timestamp (NaT se vazio). Rápido: chamado milhares de vezes."""
    texto = str(texto or "").strip()
    for fmt, n in (("%d/%m/%Y %H:%M", 16), ("%d/%m/%Y", 10)):
        try:
            return pd.Timestamp(dt.datetime.strptime(texto[:n], fmt))
        except ValueError:
            pass
    return pd.NaT


def _ordem(texto):
    t = _dt(texto)
    return t if pd.notna(t) else pd.Timestamp.min


def _dias(inicio, fim) -> str:
    if pd.isna(inicio):
        return ""
    fim = fim if pd.notna(fim) else pd.Timestamp.now()
    return str(int(round((fim - inicio).total_seconds() / 86400)))


# --------------------------------------------------------------------------- dados
def _carregar(banco: Banco) -> dict:
    eq = banco.cadastro("equipamentos", incluir_removidos=True)
    fech = banco.cadastro("os_fechadas", incluir_removidos=True)
    abertas = banco.tabela("os_abertas")
    prazos = {}
    coleta = banco.ultima_coleta()
    if coleta:
        oc = banco.os_da_coleta(coleta["id"])
        prazos = {r.os: r for r in oc.itertuples()}
        if abertas.empty:   # registro mensal ainda sem OS abertas: usa a última coleta (sem detalhe)
            abertas = oc.rename(columns={"codigo": "tag"}).assign(fechamento="", id_effort="")
    oss = pd.concat([fech.assign(_aberta=False), abertas.assign(_aberta=True, ate=None)], ignore_index=True)
    oss = oss.drop_duplicates("os", keep="last")   # OS que fechou depois do registro: vale a mais nova
    det = banco.tabela("os_detalhe").set_index("os")
    ativ = defaultdict(list)
    for r in banco.tabela("os_atividades").to_dict("records"):
        ativ[r["os"]].append(r)
    mud = banco.mudancas()
    mud = mud[(mud["cadastro"] == "equipamentos") & (mud["tipo"] != "entrou")]
    vinc = {}
    for nome in ("planos_equipamentos", "contratos_equipamentos"):
        v = banco.cadastro(nome)
        g = defaultdict(list)
        for r in v.to_dict("records"):
            g[r["tag"]].append(r)
        vinc[nome] = g
    return {"eq": eq, "oss": oss, "det": det, "ativ": ativ, "mud": mud, "prazos": prazos,
            "planos": vinc["planos_equipamentos"], "contratos": vinc["contratos_equipamentos"]}


# --------------------------------------------------------------------------- páginas
def _pagina_os(o: dict, d: dict | None, at: list[dict], equip_txt: str, pend_links: list[tuple[str, dict]],
               prazo) -> str:
    sit = SITUACOES.get(str(o.get("situacao", "")), str(o.get("situacao", "")).lower() or ("aberta" if o["_aberta"] else "fechada"))
    d = d or {}
    abertura = d.get("abertura") or o.get("abertura", "")
    fechamento = d.get("fechamento") or o.get("fechamento", "")
    ab, fe = _dt(abertura), _dt(fechamento)
    por_tipo = defaultdict(list)
    for a in at:
        por_tipo[a["tipo"]].append(a)
    partes = [f"<h1>OS {_e(o['os'])} · {_e(o.get('tipo', ''))} <span class='tag {'aberta' if sit != 'fechada' else ''}'>OS {_e(sit.capitalize())}</span></h1>",
              f"<div class='trilha'><a href='../equipamento.html'>{_e(equip_txt)}</a><a href='../../indice.html'>Todos os equipamentos</a></div>",
              f"<div class='conteudo'><p class='sub'>{_e(equip_txt)} · {_e(o.get('setor', ''))}</p>"]
    campos = [("Aberta em", abertura), ("Aberta por", d.get("aberta_por", "")),
              ("Fechada em", fechamento or ("em aberto" if sit != "fechada" else "")),
              ("Dias aberta", _dias(ab, fe)), ("Técnico responsável", d.get("responsavel") or o.get("responsavel", "")),
              ("Prioridade", d.get("prioridade", "")), ("Complexidade", d.get("complexidade", ""))]
    if d.get("parada"):
        campos += [("Equipamento parado em", d["parada"]), ("Voltou a funcionar em", d.get("funcionamento", ""))]
    partes.append(_grade(campos))
    if not d:
        partes.append("<p class='vazio'>O detalhe desta OS ainda não foi lido do Effort; entra num dos próximos registros.</p>")

    # o que aconteceu
    defeitos, feitos = por_tipo["ocorrenciasolucionada"] + por_tipo["ocorrencia"], por_tipo["ocorrenciaservico"]
    if defeitos or feitos:
        partes.append("<h2>O que aconteceu</h2>")
        partes.append(_tabela(["", "Data", "Descrição", "Causa"],
                              [["Defeito", a["data"], a["descricao"], a["causa"]] for a in defeitos] +
                              [["Serviço realizado", a["data"], a["descricao"], a["causa"]] for a in feitos]))

    # pendências
    partes.append("<h2>Pendências</h2>")
    if pend_links:
        linhas = []
        for arq, p in pend_links:
            linhas.append([f"<a href='pendencias/{html.escape(arq, quote=True)}'>{_e(p['pendencia'])}</a>", p["data"],
                           p["fechada_em"] or "em aberto", p["dias"]])
        partes.append(_tabela(["Pendência", "Aberta em", "Fechada em", "Dias"], linhas, {3}, cru={0}))
    elif prazo is not None and getattr(prazo, "ultima_pendencia", ""):
        partes.append(_tabela(["Pendência atual", "Aberta em", "Prazo"],
                              [[sem_prefixo(prazo.ultima_pendencia), prazo.pendencia_abertura, prazo.pendencia_prazo]]))
    else:
        partes.append("<p class='vazio'>Nenhuma pendência registrada.</p>")

    # mão de obra, materiais e serviços externos
    if por_tipo["servico"]:
        partes.append("<h2>Mão de obra</h2>")
        partes.append(_tabela(["Data", "Técnico", "Horas", "Valor"],
                              [[a["data"], a["descricao"], a["quantidade"], a["valor_total"]] for a in por_tipo["servico"]], {2, 3}))
    pecas = por_tipo["peca"]
    partes.append("<h2>Materiais (peças)</h2>")
    if pecas:
        tot = reais(pd.Series([a["valor_total"] for a in pecas])).sum()
        partes.append(_tabela(["Data", "Material", "Qtd.", "Valor unitário", "Total", "Lote"],
                              [[a["data"], a["descricao"], a["quantidade"], a["valor_unitario"], a["valor_total"], a["lote"]] for a in pecas],
                              {2, 3, 4}, total=["", "Total", "", "", formatar_reais(tot), ""]))
    else:
        partes.append("<p class='vazio'>Nenhum material lançado.</p>")
    ext = por_tipo["servicoexterno"]
    if ext:
        partes.append("<h2>Serviços externos</h2>")
        partes.append(_tabela(["Data", "Empresa", "Valor"], [[a["data"], a["descricao"], a["valor_total"]] for a in ext], {2}))
    if d:
        partes.append("<h2>Custos da OS</h2>")
        partes.append(_grade([("Materiais", d.get("custo_pecas", "")), ("Serviços externos", d.get("custo_servicos_externos", "")),
                              ("Mão de obra", d.get("custo_mao_de_obra", "")), ("Total", d.get("custo_total", ""))]))
    outros = [(t, por_tipo[t]) for t in ("autor", "procedimento", "procedimentoajuste") if por_tipo[t]]
    anexos = len(por_tipo["anexo"]) + len(por_tipo["foto"])
    if outros or anexos:
        partes.append("<h2>Assinaturas e anexos</h2>")
        linhas = [[{"autor": "Assinatura", "procedimento": "Procedimento", "procedimentoajuste": "Procedimento"}[t], a["data"], a["descricao"]]
                  for t, l in outros for a in l]
        if linhas:
            partes.append(_tabela(["", "Data", "Descrição"], linhas))
        if anexos:
            partes.append(f"<p class='sub'>{anexos} anexo(s) ou foto(s) no Effort (os arquivos não são copiados).</p>")
    return _pagina(f"OS {o['os']}", "".join(partes) + "</div>")


def _pagina_pendencia(p: dict, n: int, total: int, o: dict, equip_txt: str, prazo) -> str:
    campos = [("Pendência", p["pendencia"]), ("Ordem na OS", f"{n} de {total}"), ("Aberta em", p["data"]),
              ("Fechada em", p["fechada_em"] or "em aberto"), ("Dias", p["dias"])]
    if not p["fechada_em"] and prazo is not None and sem_prefixo(getattr(prazo, "ultima_pendencia", "")) == p["pendencia"]:
        campos.append(("Prazo de conclusão", prazo.pendencia_prazo))
    corpo = (f"<h1>{_e(p['pendencia'])}</h1>"
             f"<div class='trilha'><a href='../OS.html'>OS {_e(o['os'])}</a><a href='../../equipamento.html'>{_e(equip_txt)}</a></div>"
             f"<div class='conteudo'><p class='sub'>OS {_e(o['os'])} · {_e(o.get('tipo', ''))} · {_e(equip_txt)}</p>"
             + _grade(campos) + "</div>")
    return _pagina(p["pendencia"], corpo)


# --------------------------------------------------------------------------- zip
def gerar_zip(banco: Banco, destino: Path | io.BytesIO, referencia: str | None = None) -> dict:
    """Monta o zip. Devolve contagens. destino pode ser um caminho ou um BytesIO."""
    dados = _carregar(banco)
    eq, oss, det, ativ, mud, prazos = (dados[k] for k in ("eq", "oss", "det", "ativ", "mud", "prazos"))
    referencia = referencia or dt.date.today().strftime("%Y-%m")
    raiz = f"historico_{referencia}"
    det_dict = det.to_dict("index")

    eq_idx = {r["tag"]: r for r in eq.to_dict("records")}
    por_tag = defaultdict(list)
    for o in oss.to_dict("records"):
        por_tag[o.get("tag") or "(sem TAG)"].append(o)
    tags = sorted(set(eq_idx) | set(por_tag))

    usados, cont = set(), {"equipamentos": 0, "os": 0, "pendencias": 0}
    indice = []
    if isinstance(destino, Path):
        destino.parent.mkdir(parents=True, exist_ok=True)
        tmp = destino.with_suffix(".tmp")
        alvo = tmp
    else:
        alvo = destino
    with zipfile.ZipFile(alvo, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for tag in tags:
            e = eq_idx.get(tag, {"tag": tag})
            partes_nome = [tag, e.get("descricao", ""), e.get("modelo", "")]
            if e.get("patrimonio"):
                partes_nome.append(f"PAT {e['patrimonio']}")
            pasta = nome_arquivo(" - ".join(p for p in partes_nome if p), LIMITE_PASTA_EQUIP)
            base, i = pasta, 2
            while pasta.lower() in usados:
                pasta, i = f"{base} ({i})", i + 1
            usados.add(pasta.lower())
            equip_txt = " · ".join(p for p in [tag, e.get("descricao", ""), e.get("modelo", "")] if p)

            lista = sorted(por_tag.get(tag, []), key=lambda o: _ordem(o.get("abertura")), reverse=True)
            linhas_os, gasto, n_abertas, pastas_os = [], 0.0, 0, set()
            for o in lista:
                d = det_dict.get(o["os"])
                at = ativ.get(o["os"], [])
                ab = _dt(o.get("abertura", ""))
                sit = SITUACOES.get(str(o.get("situacao", "")), "aberta" if o["_aberta"] else "fechada")
                n_abertas += sit != "fechada"
                nome_os = nome_arquivo(f"{ab:%Y-%m-%d} OS {o['os']} {o.get('tipo', '')} ({sit})" if pd.notna(ab)
                                       else f"OS {o['os']} {o.get('tipo', '')} ({sit})", 60)
                while nome_os.lower() in pastas_os:
                    nome_os += "_"
                pastas_os.add(nome_os.lower())

                pend = [a for a in at if str(a["tipo"]).startswith("pendencia")]
                pend.sort(key=lambda a: _ordem(a["data"]))
                pend_links = []
                for n, a in enumerate(pend, start=1):
                    ini, fim = _dt(a["data"]), _dt(a["fechada_em"])
                    p = {"pendencia": sem_prefixo(a["descricao"]), "data": a["data"], "fechada_em": a["fechada_em"] or "",
                         "dias": _dias(ini, fim)}
                    arq = nome_arquivo(f"{n:02d} - {p['pendencia']} ({p['dias'] or '?'} dias)", LIMITE_ARQUIVO) + ".html"
                    pend_links.append((arq, p))
                prazo = prazos.get(o["os"]) if sit != "fechada" else None
                caminho_os = f"{raiz}/{pasta}/{nome_os}"
                z.writestr(f"{caminho_os}/OS.html", _pagina_os(o, d, at, equip_txt, pend_links, prazo))
                for n, (arq, p) in enumerate(pend_links, start=1):
                    z.writestr(f"{caminho_os}/pendencias/{arq}", _pagina_pendencia(p, n, len(pend_links), o, equip_txt, prazo))
                cont["os"] += 1
                cont["pendencias"] += len(pend_links)

                custo = reais(pd.Series([(d or {}).get("custo_total", "")]))[0]
                gasto += 0 if pd.isna(custo) else custo
                defeito = next((a["descricao"] for a in at if a["tipo"] in ("ocorrenciasolucionada", "ocorrencia")), "")
                link = f"<a href='{html.escape(nome_os, quote=True)}/OS.html'>{_e(o['os'])}</a>"
                linhas_os.append([o.get("abertura", ""), link, o.get("tipo", ""),
                                  f"<span class='tag {'aberta' if sit != 'fechada' else ''}'>{_e(sit)}</span>",
                                  _e(defeito), str(len(pend_links)), _e(formatar_reais(custo) if pd.notna(custo) else "")])

            # página do equipamento
            if isinstance(e.get("ate"), str) and e["ate"]:
                situacao = f"saiu do Effort em {e['ate']}"
            elif "inativo" in e:
                situacao = "inativo" if e.get("inativo") == "SIM" else "ativo"
            else:
                situacao = "não está no cadastro de equipamentos"
            corpo = [f"<h1>{_e(tag)} · {_e(e.get('descricao', ''))}</h1>",
                     f"<div class='trilha'><a href='../indice.html'>Todos os equipamentos</a></div>",
                     f"<div class='conteudo'><p class='sub'>{_e(e.get('modelo', ''))} · {_e(e.get('fabricante', ''))} · {_e(e.get('setor', ''))} · {_e(situacao)}</p>",
                     _grade([("Patrimônio", e.get("patrimonio", "")), ("Nº de série", e.get("numero_serie", "")),
                             ("Situação", e.get("situacao", "")), ("Criticidade", e.get("criticidade", "")),
                             ("Prioridade", e.get("prioridade", "")), ("Tem plano", e.get("tem_plano", "")),
                             ("Tem contrato", e.get("tem_contrato", "")), ("No registro desde", e.get("desde", "")),
                             ("OS (abertas)", f"{len(lista)} ({n_abertas})"), ("Gasto nas OS", formatar_reais(gasto))])]
            planos = dados["planos"].get(tag, [])
            if planos:
                corpo.append("<h2>Planos de manutenção</h2>")
                corpo.append(_tabela(["Plano", "Periodicidade", "Última realização", "Próxima abertura", "Última OS aberta", "Responsável"],
                                     [[r["plano"], r["periodicidade"], r["ultima_realizacao"], r["proxima_abertura"],
                                       r["ultima_os_aberta"], r["responsavel"]] for r in planos]))
            contratos = dados["contratos"].get(tag, [])
            if contratos:
                corpo.append("<h2>Contratos</h2>")
                corpo.append(_tabela(["Nº", "Contrato", "Fornecedor", "Valor do equipamento"],
                                     [[r["numero"], r["contrato"], r["fornecedor"], r["valor"]] for r in contratos], {3}))
            h = mud[mud["chave"] == tag]
            if not h.empty:
                corpo.append("<h2>Mudanças no cadastro</h2>")
                corpo.append(_tabela(["Mês", "O quê", "Campo", "Antes", "Depois"],
                                     h[["referencia", "tipo", "campo", "antes", "depois"]].fillna("").values.tolist()))
            corpo.append(f"<h2>Ordens de serviço ({len(lista)})</h2>")
            corpo.append(_tabela(["Abertura", "OS", "Tipo", "Situação", "Defeito", "Pendências", "Custo"], linhas_os,
                                 {5, 6}, cru={1, 3, 4, 6}) if linhas_os else "<p class='vazio'>Nenhuma OS registrada.</p>")
            z.writestr(f"{raiz}/{pasta}/equipamento.html", _pagina(tag, "".join(corpo) + "</div>"))
            cont["equipamentos"] += 1
            indice.append([f"<a href='{html.escape(pasta, quote=True)}/equipamento.html'>{_e(tag)}</a>", _e(e.get("descricao", "")),
                           _e(e.get("modelo", "")), _e(e.get("patrimonio", "")), _e(e.get("setor", "")),
                           str(len(lista)), str(n_abertas), _e(formatar_reais(gasto) if gasto else "")])

        busca = ("<div class='busca'><label for='q'>Todo ou parte do nome:</label><input id='q' placeholder='TAG, descrição, modelo, patrimônio ou setor' "
                 "oninput=\"var q=this.value.toLowerCase();document.querySelectorAll('tbody tr').forEach("
                 "function(r){r.style.display=r.textContent.toLowerCase().indexOf(q)<0?'none':''})\"></div>")
        z.writestr(f"{raiz}/indice.html", _pagina(
            "Histórico de equipamentos",
            f"<h1>Histórico de Equipamentos</h1><div class='conteudo'><p class='sub'>Referência {referencia} · {cont['equipamentos']} equipamentos, "
            f"{cont['os']} OS, {cont['pendencias']} pendências</p>{busca}"
            + _tabela(["TAG", "Descrição", "Modelo", "Patrimônio", "Setor", "OS", "Abertas", "Gasto"], indice,
                      {5, 6, 7}, cru=set(range(8))) + "</div>"))
        z.writestr(f"{raiz}/LEIA-ME.txt", (
            f"Histórico de equipamentos da MEH, referência {referencia}.\r\n\r\n"
            "Comece por indice.html (abre no navegador). Cada pasta é um equipamento (TAG - descrição - modelo - patrimônio).\r\n"
            "Dentro dela, equipamento.html tem a ficha e a lista das OS; cada OS é uma pasta com OS.html\r\n"
            "e a subpasta pendencias, com uma página por pendência, na ordem em que foram abertas.\r\n\r\n"
            "É uma cópia para consulta, feita só com leitura do Effort. Observações em texto livre e anexos\r\n"
            "não são copiados; consulte o Effort para eles.\r\n").encode("utf-8"))
    if isinstance(destino, Path):
        tmp.replace(destino)
    log.info("Zip de pastas: %s equipamentos, %s OS, %s pendências.", cont["equipamentos"], cont["os"], cont["pendencias"])
    return cont
