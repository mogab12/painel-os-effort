"""Registro mensal (histórico) dos cadastros do Effort (oficina Manutenção de Equipamentos). SÓ LEITURA.

Uso:
    python -m painel.snapshot --agora            # fotografa agora (referência = mês de ontem)
    python -m painel.snapshot --agora --visivel  # idem, com o navegador na tela

Equipamentos: a pesquisa de /Equipamento.aspx exporta para Excel (botão "Excel") todas as linhas
de uma vez, mas só com TAG, descrição, modelo, fabricante, setor, nº de série, patrimônio e inativo.
Criticidade, prioridade, plano, contrato e situação vêm de uma exportação por valor de filtro:
o equipamento que aparece na exportação "criticidade = ALTA" tem criticidade alta, e assim por diante.
Ativos e inativos são exportados separadamente (a caixa "Inativo" mostra só os inativos).
Conferido em nov/2026: 2.577 ativos + 414 inativos, cerca de 1 minuto.

Contratos, fornecedores e fabricantes: exportação Excel das telas de pesquisa (todos os registros).
OS fechadas: a grade da pesquisa de OS com só "Fechada" marcada (5.830 em nov/2026). O detalhe de cada OS
(responsável, datas, custos e atividades) é lido UMA vez, na tela de consulta, só para as OS que ainda não
estão no banco; a primeira carga leva cerca de 1h40 e é retomada na rodada seguinte se passar do limite.

A gravação é incremental (banco.aplicar_snapshot): só o que entrou, saiu ou mudou é escrito.

Nada aqui clica em Salvar, Alterar, Excluir, Fechar ou Reabrir: só pesquisa, exporta, consulta e faz logout.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html as html_lib
import logging
import os
import re
import shutil
import sys
import time
import traceback
import warnings
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd

from . import alertas, config
from .banco import COLUNAS_OS_ATIVIDADES, COLUNAS_OS_DETALHE, COLUNAS_SNAPSHOT, Banco
from .coletor import RE_ATIVIDADE, Coletor, ConfigColetor, ErroColeta, separar_equipamento
from .diff import gravar_log

log = logging.getLogger("painel.snapshot")

# cabeçalho da exportação Excel de /Equipamento.aspx -> coluna no banco
COLUNAS_EXPORT_EQUIPAMENTO = {
    "TAG": "tag", "Equipamento": "descricao", "Modelo": "modelo", "Fabricante": "fabricante", "Setor": "setor",
    "Nº Série": "numero_serie", "Patrimônio": "patrimonio", "Inativo": "inativo",
}

# atributo -> {valor: campos do formulário de pesquisa que filtram esse valor}
FILTROS_EQUIPAMENTO = {
    "criticidade": {"ALTA": {"cboCriticidade": "1"}, "MEDIA": {"cboCriticidade": "2"}, "BAIXA": {"cboCriticidade": "3"}},
    "prioridade": {"ALTA": {"cboPrioridade": "1"}, "MEDIA": {"cboPrioridade": "2"}, "BAIXA": {"cboPrioridade": "3"}},
    "tem_plano": {"SIM": {"cboPlanosManutencao": "1"}},
    "tem_contrato": {"SIM": {"cboContratos": "1"}},
    # "situacao" é montado na hora, com a lista do próprio Effort (PROPRIO, COMODATO, LOCADO, EMPRESTADO...)
}
PADRAO_EQUIPAMENTO = {"tem_plano": "NÃO", "tem_contrato": "NÃO"}  # quem não aparece no filtro "com"


def referencia(quando: dt.date) -> str:
    """AAAA-MM do mês fotografado: a rodada do dia 1º fotografa o mês que acabou de terminar."""
    return f"{quando - dt.timedelta(days=1):%Y-%m}"


# --------------------------------------------------------------------------- leitura (sem Effort)
def ler_export_equipamentos(arquivo) -> pd.DataFrame:
    """Lê a exportação Excel da pesquisa de equipamentos e devolve as colunas do banco."""
    df = pd.read_excel(arquivo, dtype=str)
    faltando = [c for c in COLUNAS_EXPORT_EQUIPAMENTO if c not in df.columns]
    if faltando:
        raise ErroColeta(f"A exportação de equipamentos mudou de formato; faltam as colunas {faltando}.")
    df = df[list(COLUNAS_EXPORT_EQUIPAMENTO)].rename(columns=COLUNAS_EXPORT_EQUIPAMENTO)
    return df.fillna("").apply(lambda s: s.str.strip())


def montar_equipamentos(base: pd.DataFrame, grupos: dict[str, dict[str, set[str]]], total: int | None = None) -> pd.DataFrame:
    """Junta a exportação base com os atributos vindos das exportações filtradas.

    grupos = {atributo: {valor: {tags que apareceram nesse filtro}}}. Um atributo cujos grupos somam
    mais que o total foi ignorado pelo Effort (filtro sem efeito) e fica em branco, com aviso.
    """
    df = base.copy()
    total = total if total is not None else len(df)
    for atributo, valores in grupos.items():
        valores = {v: {t for t in tags if t} for v, tags in valores.items()}   # sem TAG não dá para cruzar
        soma = sum(len(t) for t in valores.values())
        if soma > total:
            log.warning("Filtro de %s parece não ter efeito (%s > %s equipamentos); atributo em branco.", atributo, soma, total)
            df[atributo] = ""
            continue
        mapa: dict[str, str] = {}
        for valor, tags in valores.items():
            for t in tags:
                if t in mapa and mapa[t] != valor:
                    log.warning("Equipamento %s aparece em %s=%s e %s=%s; mantido o primeiro.", t, atributo, mapa[t], atributo, valor)
                    continue
                mapa[t] = valor
        padrao = PADRAO_EQUIPAMENTO.get(atributo, "")
        df[atributo] = [mapa.get(t, padrao) if t else "" for t in df["tag"]]
    return df.reindex(columns=COLUNAS_SNAPSHOT["equipamentos"], fill_value="")


def chave_unica(df: pd.DataFrame) -> pd.DataFrame:
    """TAG é a chave da fotografia. Equipamento sem TAG ganha uma chave pelo nº de série (ou setor);
    TAG repetida ganha sufixo " (2)", " (3)"... Os dois casos aparecem no relatório de qualidade."""
    df = df.copy()
    sem = df["tag"] == ""
    if sem.any():
        log.warning("%s equipamento(s) sem TAG no Effort.", int(sem.sum()))
        df.loc[sem, "tag"] = [f"(sem TAG) série {s}" if s else f"(sem TAG) {setor}"
                              for s, setor in zip(df.loc[sem, "numero_serie"], df.loc[sem, "setor"])]
    n = df.groupby("tag").cumcount()
    if (n > 0).any():
        log.warning("TAGs repetidas no Effort: %s", sorted(df.loc[n > 0, "tag"].unique()))
        df.loc[n > 0, "tag"] = df.loc[n > 0, "tag"] + " (" + (n[n > 0] + 1).astype(str) + ")"
    return df


def ler_planilha(arquivo, header=0) -> pd.DataFrame:
    """Lê a exportação do Effort, que às vezes é .xlsx e às vezes .xls antigo, sempre como texto."""
    with open(arquivo, "rb") as f:
        xls = f.read(4) == bytes.fromhex("d0cf11e0")
    kw = {"engine": "xlrd", "engine_kwargs": {"logfile": open(os.devnull, "w")}} if xls else {}
    with warnings.catch_warnings():   # "Workbook contains no default style": inofensivo, vem do arquivo do Effort
        warnings.simplefilter("ignore", UserWarning)
        return pd.read_excel(arquivo, dtype=str, header=header, **kw)


def _limpar(df: pd.DataFrame) -> pd.DataFrame:
    return df.astype(object).fillna("").astype(str).apply(lambda s: s.str.strip())


def ler_export_simples(arquivo, colunas: dict[str, str], cadastro: str) -> pd.DataFrame:
    """Exportação com o cabeçalho na primeira linha (fornecedores, fabricantes)."""
    df = ler_planilha(arquivo)
    faltando = [c for c in colunas if c not in df.columns]
    if faltando:
        raise ErroColeta(f"A exportação de {cadastro} mudou de formato; faltam as colunas {faltando}.")
    return _limpar(df[list(colunas)].rename(columns=colunas))


COLUNAS_EXPORT_FORNECEDOR = {"Código": "codigo", "Razão Social/Nome": "razao_social", "Nome Fantasia": "nome_fantasia",
                             "1º Telefone": "telefone", "CPF/CNPJ": "cnpj", "Assist. Téc.": "assistencia_tecnica",
                             "Ativo": "ativo"}
COLUNAS_EXPORT_FABRICANTE = {"Código": "codigo", "Razão Social": "razao_social", "Telefone": "telefone", "Ativo": "ativo"}


def _juntar_quebras(texto: str) -> list[str]:
    """No relatório de contratos o texto vem quebrado pela largura da célula: quebra com espaço antes é
    só diagramação; quebra sem espaço separa campos (fornecedor / descrição)."""
    return [p.strip() for p in texto.replace(" \n", " ").split("\n") if p.strip()]


def ler_export_contratos(arquivo) -> pd.DataFrame:
    """Relatório 'Contratos de Manutenção' (.xls): título, cabeçalho na linha que começa com 'Número',
    uma linha por contrato e um rodapé 'Total de Registro(s)'."""
    bruto = ler_planilha(arquivo, header=None).fillna("")
    inicio = next((i for i, v in enumerate(bruto[0]) if str(v).strip() == "Número"), None)
    if inicio is None:
        raise ErroColeta("A exportação de contratos mudou de formato; não achei o cabeçalho 'Número'.")
    cab = [str(x).strip() for x in bruto.iloc[inicio]]
    try:
        c_forn, c_sit, c_vig, c_cob, c_val = (cab.index(n) for n in ("Fornecedor/Descrição", "Situação", "Vigência", "Cobertura", "Valor"))
    except ValueError as e:
        raise ErroColeta(f"A exportação de contratos mudou de formato: {e}.") from e
    linhas = []
    for _, r in bruto.iloc[inicio + 1:].iterrows():
        if str(r[0]).strip().startswith("Total de Registro"):
            continue
        numero = re.sub(r"\s+", "", str(r[0]))   # número não tem espaço: o que houver é quebra de diagramação
        if not numero:
            continue
        partes = _juntar_quebras(str(r[c_forn]))
        vig = re.findall(r"\d{2}/\d{2}/\d{4}", str(r[c_vig]))
        linhas.append({"numero": numero, "fornecedor": partes[0] if partes else "", "descricao": " ".join(partes[1:]),
                       "situacao": str(r[c_sit]).strip(), "vigencia_inicio": vig[0] if vig else "",
                       "vigencia_fim": vig[1] if len(vig) > 1 else "", "cobertura": str(r[c_cob]).strip(),
                       "valor": str(r[c_val]).strip()})
    return pd.DataFrame(linhas, columns=COLUNAS_SNAPSHOT["contratos"])


def ler_export_modelos(arquivo) -> pd.DataFrame:
    """Relatório 'Modelos' (.xls): título, cabeçalho na linha que começa com 'Código', uma linha por modelo."""
    bruto = ler_planilha(arquivo, header=None).fillna("")
    inicio = next((i for i, v in enumerate(bruto[0]) if str(v).strip() == "Código"), None)
    if inicio is None:
        raise ErroColeta("A exportação de modelos mudou de formato; não achei o cabeçalho 'Código'.")
    cab = [str(x).strip() for x in bruto.iloc[inicio]]
    nomes = {"Código": "codigo", "Descrição": "descricao", "Modelo": "modelo", "Fabricante": "fabricante",
             "Criticidade": "criticidade", "Reg. ANVISA": "registro_anvisa"}
    faltando = [n for n in nomes if n not in cab]
    if faltando:
        raise ErroColeta(f"A exportação de modelos mudou de formato; faltam as colunas {faltando}.")
    df = bruto.iloc[inicio + 1:, [cab.index(n) for n in nomes]]
    df.columns = list(nomes.values())
    df = _limpar(df)
    return df[(df["codigo"] != "") & ~df["codigo"].str.startswith("Total")].reset_index(drop=True)


# ícone do nó na árvore de planos (PlanoManutencao.aspx) -> tipo do plano
TIPOS_PLANO = {"gico_planomanutencao.png": "Interno", "gico_planocontrato.png": "Contrato", "gico_planoexterno.png": "Externo",
               "gico_planopnpcontrato.png": "PNP contrato", "gico_planopnpassistencia.png": "PNP assistência",
               "gico_planosemperiodicidade.png": "Sem periodicidade", "gico_planoinativo.png": "Inativo"}

# lê os nós da árvore (obout) já desenhada: texto, ícone e o grupo em que o nó está
JS_ARVORE = """() => [...document.querySelectorAll('td.ob_t2')].map(td => {
  const c = td.closest('table.ob_t2c'); const g = c && c.previousElementSibling && c.previousElementSibling.querySelector('td.ob_t2');
  const img = td.parentElement.querySelector('td.ob_t4 img');
  return {id: td.id, texto: td.innerText.trim(), icone: img ? img.getAttribute('src').split('/').pop() : '',
          grupo: g ? g.innerText.trim() : ''} })"""


def ler_arvore_setores(nos: list[dict]) -> pd.DataFrame:
    """Árvore de Setor.aspx: 'SIGLA - NOME' por setor, dentro de grupos (pastas). Setor desativado tem ícone próprio."""
    linhas = []
    for n in nos:
        if n["icone"] not in ("ico_setor.png", "ico_setordesativado.png"):
            continue   # raiz, empresa e pastas de grupo
        sigla, _, nome = n["texto"].partition(" - ")
        if not nome:   # sem " - ": não tem sigla
            sigla, nome = "", n["texto"]
        grupo = n["grupo"] if n["grupo"] != "MANUTENÇÃO DE EQUIPAMENTOS" else ""
        linhas.append({"id": n["id"], "sigla": sigla.strip(), "nome": nome.strip(), "grupo": grupo,
                       "ativo": "NÃO" if n["icone"] == "ico_setordesativado.png" else "SIM"})
    return pd.DataFrame(linhas, columns=COLUNAS_SNAPSHOT["setores"])


def ler_arvore_planos(nos: list[dict]) -> pd.DataFrame:
    """Árvore de PlanoManutencao.aspx: nome, 'Periodicidade: X' e às vezes 'Procedimento: Y'; o ícone diz o tipo."""
    linhas = []
    for n in nos:
        if n["icone"] not in TIPOS_PLANO:
            continue
        partes = [p.strip() for p in n["texto"].split("\n") if p.strip()]
        campo = lambda rotulo: next((p.split(":", 1)[1].strip() for p in partes[1:] if p.startswith(rotulo + ":")), "")
        linhas.append({"id": n["id"], "nome": partes[0] if partes else "", "periodicidade": campo("Periodicidade"),
                       "procedimento": campo("Procedimento"), "tipo": TIPOS_PLANO[n["icone"]],
                       "ativo": "NÃO" if n["icone"] == "gico_planoinativo.png" else "SIM", "n_equipamentos": ""})
    return pd.DataFrame(linhas, columns=COLUNAS_SNAPSHOT["planos"])   # n_equipamentos vem do relatório de planos


RE_PROCEDIMENTO = re.compile(r"^(\d+)\s*-\s*(.*?)(?:\s+v([\d.]+))?(?:\s*\(PUBLICA[ÇC][ÃA]O:\s*([^)]*)\))?\s*$", re.I)


def ler_arvore_procedimentos(nos: list[dict]) -> pd.DataFrame:
    """Árvore de frmprocedimentoraiz.aspx: '45 - CALIBRACAO v1.0 (PUBLICAÇÃO: 05/03/2026)'."""
    linhas = []
    for n in nos:
        texto = " ".join(str(n.get("texto", "")).split())
        m = RE_PROCEDIMENTO.match(texto)
        if not m or n.get("id", "").startswith("root"):
            continue
        linhas.append({"id": m.group(1), "nome": m.group(2), "versao": m.group(3) or "", "publicacao": m.group(4) or ""})
    return pd.DataFrame(linhas, columns=COLUNAS_SNAPSHOT["procedimentos"])


def ler_motivos_pendencia(resposta: dict) -> pd.DataFrame:
    """Resposta JSON de /api/v1/PendencyReasons/grid (a mesma que a tela Motivos de Pendência usa)."""
    sim = lambda v: "SIM" if v else "NÃO"
    linhas = [{"id": str(i.get("Id", "")), "descricao": str(i.get("Description", "")).strip(),
               "prazo": f"{i.get('AttendanceTerm', '')} {i.get('UnityTerm', '')}".strip(),
               "ativo": sim(i.get("Active")), "pendencia_inicial": sim(i.get("InitialPendency")),
               "define_fluxo": sim(i.get("DefineFlow"))} for i in resposta.get("Items", [])]
    if resposta.get("summary") is not None and int(resposta["summary"]) != len(linhas):
        raise ErroColeta(f"Motivos de pendência: a API informa {resposta['summary']}, mas devolveu {len(linhas)}.")
    return pd.DataFrame(linhas, columns=COLUNAS_SNAPSHOT["motivos_pendencia"])


COLUNAS_REL_PLANOS = {"TAG": "tag", "Planos de Manutenção": "plano", "Periodicidade": "periodicidade",
                      "Últ. Realização": "ultima_realizacao", "Última OS Aberta": "ultima_os_aberta",
                      "Próx. Data de Abertura": "proxima_abertura", "Responsável": "responsavel"}
COLUNAS_REL_CONTRATOS = {"Nº Contrato": "numero", "Contrato": "contrato", "Fornecedor": "fornecedor", "TAG": "tag",
                         "Patrimônio": "patrimonio", "Descrição": "descricao", "Setor": "setor", "Valor": "valor"}


def ler_relatorio_planos_equipamentos(arquivo) -> pd.DataFrame:
    """Relatório 'Planos de Manutenção por Equipamento' em XLS: um equipamento por plano."""
    df = ler_export_simples(arquivo, COLUNAS_REL_PLANOS, "planos por equipamento")
    df = df[df["tag"] != ""]
    df.insert(0, "chave", df["tag"] + " | " + df["plano"])
    return repetidos_com_sufixo(df[COLUNAS_SNAPSHOT["planos_equipamentos"]].reset_index(drop=True), "chave")


def ler_relatorio_contratos_equipamentos(arquivo) -> pd.DataFrame:
    """Relatório 'Contratos com Fornecedores', analítico, em XLS: uma linha por equipamento coberto.
    Contrato sem equipamento vem com a TAG vazia e fica de fora (é o que o relatório de contratos aponta)."""
    df = ler_export_simples(arquivo, COLUNAS_REL_CONTRATOS, "contratos com fornecedores")
    df = df[df["tag"] != ""]
    df.insert(0, "chave", df["numero"] + " | " + df["tag"])
    return repetidos_com_sufixo(df[COLUNAS_SNAPSHOT["contratos_equipamentos"]].reset_index(drop=True), "chave")


def repetidos_com_sufixo(df: pd.DataFrame, chave: str) -> pd.DataFrame:
    """Chave repetida ganha " (2)", " (3)"... (ex.: dois contratos com o mesmo número)."""
    df = df.copy()
    n = df.groupby(chave).cumcount()
    if (n > 0).any():
        log.warning("%s repetido(s) no Effort: %s", chave, sorted(df.loc[n > 0, chave].unique()))
        df.loc[n > 0, chave] = df.loc[n > 0, chave] + " (" + (n[n > 0] + 1).astype(str) + ")"
    return df


def montar_os_fechadas(linhas: list[list[str]]) -> pd.DataFrame:
    """Linhas da grade da pesquisa de OS: [check, OS, TAG, Equipamento/Setor, Tipo, Situação, Abertura, Fechamento, id interno]."""
    registros = []
    for c in linhas:
        equip, setor, _ = separar_equipamento(c[3] if len(c) > 3 else "")
        registros.append({"os": c[1], "tag": c[2], "equipamento": equip, "setor": setor, "tipo": c[4],
                          "situacao": c[5], "abertura": c[6], "fechamento": c[7] if len(c) > 8 else "",
                          "id_effort": c[-1]})
    return pd.DataFrame(registros, columns=COLUNAS_SNAPSHOT["os_fechadas"])


# tela de consulta da OS (frmos_alterar.aspx): id do campo -> coluna; datas e horas são juntadas
CAMPOS_OS = {"cbotecnico": "responsavel", "cboprioridade": "prioridade", "cbocomplexidadeos": "complexidade",
             "txtUsuarioAbertura": "aberta_por", "txttotalpecas": "custo_pecas",
             "txttotalservicosexternos": "custo_servicos_externos", "txttotalmaodeobra": "custo_mao_de_obra",
             "txttotalos": "custo_total", "txtcod_os_nominal": "os"}
DATAS_OS = {"abertura": ("txtdataabertura", "txthoraabertura"), "fechamento": ("txtdatafechamento", "txthorafechamento"),
            "parada": ("txtdataparada", "txthoraparada"), "funcionamento": ("txtdatafuncionamento", "txthorafuncionamento")}


class _Inputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.valores: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "input" and a.get("id"):
            self.valores[a["id"]] = html_lib.unescape(a.get("value") or "").strip()


def ler_os_detalhe(html: str) -> dict:
    p = _Inputs()
    p.feed(html)
    v = p.valores
    d = {col: v.get(campo, "") for campo, col in CAMPOS_OS.items()}
    for col, (data, hora) in DATAS_OS.items():
        d[col] = f"{v.get(data, '')} {v.get(hora, '')}".strip()
    return d


def ler_os_atividades(html: str, os_numero: str) -> list[dict]:
    """Atividades da OS (pendências, ocorrências, serviços, serviços externos, anexos...), sem o texto livre
    das observações: só tipo, data, descrição, data de fechamento da pendência e causa da ocorrência."""
    saida = []
    for cod, corpo in RE_ATIVIDADE.findall(html):
        icone = re.search(r"img/ico_([a-z_]+)\.png", corpo, re.I)
        # <br> separa as partes da célula: "FECHADA EM ..." | "CAUSA: ..." | "OCORRÊNCIA: ..." | texto livre
        tds = [html_lib.unescape(re.sub(r"(?s)<[^>]+>", " ", re.sub(r"(?i)<br\s*/?>", "|", t)))
               for t in re.findall(r"(?s)<td[^>]*>(.*?)</td>", corpo)]
        tds = [[" ".join(p.split()) for p in t.split("|") if p.strip()] for t in tds]
        partes = tds[3] if len(tds) > 3 else []
        fechada = next((m for m in (re.search(r"FECHADA EM (\d{2}/\d{2}/\d{4}) às (\d{2}:\d{2})", p) for p in partes) if m), None)
        causa = next((p.split(":", 1)[1].strip() for p in partes if p.upper().startswith("CAUSA:")), "")
        lote = next((p.split(":", 1)[1].strip() for p in partes if p.upper().startswith("LOTE:")), "")
        celula = lambda i: " ".join(tds[i]) if len(tds) > i else ""   # colunas QTD, Valor e Total da grade
        saida.append({"os": os_numero, "tipo": icone.group(1).lower() if icone else "", "id": cod,
                      "data": " ".join(tds[1]) if len(tds) > 1 else "", "descricao": " ".join(tds[2]) if len(tds) > 2 else "",
                      "fechada_em": f"{fechada.group(1)} {fechada.group(2)}" if fechada else "", "causa": causa,
                      "quantidade": celula(4), "valor_unitario": celula(5), "valor_total": celula(6), "lote": lote})
    return saida


# --------------------------------------------------------------------------- coleta (Effort)
class Fotografo:
    def __init__(self, cfg: ConfigColetor):
        self.cfg = cfg
        self.coletor = Coletor(cfg)

    def _exportar_equipamentos(self, ctx, campos: dict, inativo: bool, situacao: tuple[str, str] | None = None):
        """Pesquisa com os filtros e baixa a exportação Excel. Devolve (total informado, DataFrame)."""
        pg = ctx.new_page()
        pg.set_default_timeout(self.cfg.timeout_s * 1000)
        try:
            pg.goto(f"{self.cfg.url_base}/Equipamento.aspx", wait_until="networkidle")
            for campo, valor in {"cboRegistros": "5", **campos}.items():   # 5 = todos os registros
                pg.select_option(f"#{campo}", valor)
            if inativo:
                pg.check("#chkInativo")
            if situacao:
                # campo de autocompletar: o id vai no campo oculto, o nome no visível
                pg.evaluate("([id, nome]) => { document.getElementById('txtSituacaoNew').value = id;"
                            " document.getElementById('cboSituacaoNew').value = nome; }", list(situacao))
            with pg.expect_navigation(wait_until="networkidle"):
                pg.click("#btnProcurar")
            total = int(pg.input_value("#hidTotalRegistros") or 0)
            if total == 0:
                return 0, pd.DataFrame(columns=list(COLUNAS_EXPORT_EQUIPAMENTO.values()))
            with pg.expect_download() as d:
                pg.evaluate("() => document.getElementById('btnExcel').click()")
            df = ler_export_equipamentos(d.value.path())
            if len(df) != total:
                raise ErroColeta(f"Exportação de equipamentos com {len(df)} linhas, mas a pesquisa informa {total}.")
            return total, df
        finally:
            pg.close()

    def _situacoes(self, ctx) -> list[tuple[str, str]]:
        r = ctx.request.post(f"{self.cfg.url_base}/CarregaDados.asmx/CarregaStatusEquipamento", data="{}",
                             headers={"Content-Type": "application/json; charset=utf-8"})
        return [(str(i), str(n).strip()) for i, n in r.json().get("d", [])]

    def equipamentos(self, ctx) -> pd.DataFrame:
        situacoes = self._situacoes(ctx)
        filtros = {**FILTROS_EQUIPAMENTO, "situacao": {nome: {"_situacao": (i, nome)} for i, nome in situacoes}}
        partes = []
        for inativo in (False, True):
            total, base = self._exportar_equipamentos(ctx, {}, inativo)
            grupos: dict[str, dict[str, set[str]]] = {}
            for atributo, valores in filtros.items():
                grupos[atributo] = {}
                for valor, campos in valores.items():
                    campos = dict(campos)
                    sit = campos.pop("_situacao", None)
                    _, df = self._exportar_equipamentos(ctx, campos, inativo, sit)
                    grupos[atributo][valor] = set(df["tag"])
            log.info("Equipamentos %s: %s.", "inativos" if inativo else "ativos", total)
            partes.append(montar_equipamentos(base, grupos, total))
        return chave_unica(pd.concat(partes, ignore_index=True))

    def _exportar_tela(self, ctx, tela: str, campos: dict, procurar: str, excel: str, ler):
        """Abre uma tela de pesquisa, pesquisa com os campos dados e lê a exportação Excel com `ler`.
        Os campos são preenchidos por JavaScript porque alguns ficam ocultos na tela."""
        pg = ctx.new_page()
        pg.set_default_timeout(self.cfg.timeout_s * 1000)
        try:
            pg.goto(f"{self.cfg.url_base}/{tela}", wait_until="networkidle")
            pg.evaluate("(c) => { for (const [k, v] of Object.entries(c)) { const e = document.getElementById(k);"
                        " e.value = v; e.dispatchEvent(new Event('change')); } }", campos)
            with pg.expect_navigation(wait_until="networkidle"):
                pg.click(f"#{procurar}")
            exibindo = re.search(r"Exibindo (\d+) registros", pg.inner_text("body"))
            with pg.expect_download() as d:
                pg.evaluate(f"() => document.getElementById('{excel}').click()")
            df = ler(d.value.path())
            if exibindo and int(exibindo.group(1)) != len(df):
                raise ErroColeta(f"{tela}: a exportação tem {len(df)} linhas, mas a tela mostra {exibindo.group(1)}.")
            return df
        finally:
            pg.close()

    def contratos(self, ctx) -> pd.DataFrame:
        df = self._exportar_tela(ctx, "ContratoManutencao.aspx", {"cbosituacao": "1"}, "btnProcurar", "btnExportarExcel",
                                 ler_export_contratos)
        return repetidos_com_sufixo(df, "numero")

    def fornecedores(self, ctx) -> pd.DataFrame:
        df = self._exportar_tela(ctx, "frmfornecedor.aspx", {"cboRegistros": "3", "cboSituacao": "1"}, "btnprocurar",
                                 "btnExportarExcel", lambda a: ler_export_simples(a, COLUNAS_EXPORT_FORNECEDOR, "fornecedores"))
        return repetidos_com_sufixo(df, "codigo")

    def fabricantes(self, ctx) -> pd.DataFrame:
        df = self._exportar_tela(ctx, "frmfabricante.aspx", {"cboRegistros": "3", "cboStatus": "1"}, "btnProcurar",
                                 "btnExportarExcel", lambda a: ler_export_simples(a, COLUNAS_EXPORT_FABRICANTE, "fabricantes"))
        return repetidos_com_sufixo(df, "codigo")

    def modelos(self, ctx) -> pd.DataFrame:
        df = self._exportar_tela(ctx, "Modelo.aspx", {"cboRegistros": "5"}, "btnprocurar", "btnExportarExcel", ler_export_modelos)
        return repetidos_com_sufixo(df, "codigo")

    def _arvore(self, ctx, tela: str, preparar: str, botao: str) -> list[dict]:
        """Abre a tela, ajusta o filtro, pesquisa (como uma pessoa faria) e lê a árvore desenhada."""
        pg = ctx.new_page()
        pg.set_default_timeout(self.cfg.timeout_s * 1000)
        try:
            pg.goto(f"{self.cfg.url_base}/{tela}", wait_until="networkidle")
            pg.evaluate(f"() => {{ {preparar}; }}")
            with pg.expect_navigation(wait_until="networkidle"):
                pg.evaluate(f"() => document.getElementById('{botao}').click()")
            return pg.evaluate(JS_ARVORE)
        finally:
            pg.close()

    def setores(self, ctx) -> pd.DataFrame:
        # cboFiltro 3 = ativos e desativados
        nos = self._arvore(ctx, "Setor.aspx", "document.getElementById('cboFiltro').value = '3'", "btnPesquisar")
        return repetidos_com_sufixo(ler_arvore_setores(nos), "id")

    def planos(self, ctx) -> pd.DataFrame:
        nos = self._arvore(ctx, "PlanoManutencao.aspx", "document.getElementById('chkInativo').checked = true", "btnprocurar")
        return repetidos_com_sufixo(ler_arvore_planos(nos), "id")

    def procedimentos(self, ctx) -> pd.DataFrame:
        # cboSituacao 3 = ativos e desativados
        nos = self._arvore(ctx, "frmprocedimentoraiz.aspx", "document.getElementById('cboSituacao').value = '3'", "btnProcurar")
        return repetidos_com_sufixo(ler_arvore_procedimentos(nos), "id")

    def motivos_pendencia(self, ctx) -> pd.DataFrame:
        pg = ctx.new_page()
        pg.set_default_timeout(self.cfg.timeout_s * 1000)
        try:   # abre a tela antes, como uma pessoa, e pede à API dela todos os motivos (só leitura)
            pg.goto(f"{self.cfg.url_base}/Pendencia.aspx", wait_until="networkidle")
            r = ctx.request.post(f"{self.cfg.url_base}/api/v1/PendencyReasons/grid?companyId=2",
                                 data="Skip=0&Take=5000&Sort=&Search=",
                                 headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                                          "X-Requested-With": "XMLHttpRequest"})
            if not r.ok:
                raise ErroColeta(f"Motivos de pendência: a API respondeu {r.status}.")
            return repetidos_com_sufixo(ler_motivos_pendencia(r.json()), "id")
        finally:
            pg.close()

    def _relatorio_xls(self, ctx, tela: str, campos: dict, marcar: list[str], ler):
        """Gera um relatório do Effort em XLS (botão Gerar, só consulta) e lê o arquivo baixado."""
        pg = ctx.new_page()
        pg.set_default_timeout(self.cfg.timeout_s * 1000)
        try:
            pg.goto(f"{self.cfg.url_base}/{tela}", wait_until="networkidle")
            pg.evaluate("([c, m]) => { for (const [k, v] of Object.entries(c)) document.getElementById(k).value = v;"
                        " for (const id of m) { const e = document.getElementById(id); e.checked = true; } }", [campos, marcar])
            with pg.expect_download(timeout=max(self.cfg.timeout_s, 300) * 1000) as d:
                pg.evaluate("() => document.getElementById('btnimprimir').click()")
            return ler(d.value.path())
        finally:
            pg.close()

    def planos_equipamentos(self, ctx) -> pd.DataFrame:
        return self._relatorio_xls(ctx, "relopeequipamentopreventiva_frm.aspx", {}, ["rbXLS"],
                                   ler_relatorio_planos_equipamentos)

    def contratos_equipamentos(self, ctx) -> pd.DataFrame:
        # cbosituacao 1 = todas; analítico = um equipamento por linha
        return self._relatorio_xls(ctx, "RelContrato_frm.aspx", {"cbosituacao": "1"}, ["rdbAnalitico", "rdbXLS"],
                                   ler_relatorio_contratos_equipamentos)

    def os_fechadas(self) -> pd.DataFrame:
        est = self.coletor.pesquisar(situacoes=("fechada",))
        df = montar_os_fechadas(self.coletor.ler_todas_paginas())
        if len(df) != est["total"]:
            raise ErroColeta(f"Li {len(df)} OS fechadas, mas o Effort informa {est['total']}.")
        return repetidos_com_sufixo(df, "os")

    def os_abertas(self) -> pd.DataFrame:
        est = self.coletor.pesquisar(situacoes=("aberta", "pendente"))
        df = montar_os_fechadas(self.coletor.ler_todas_paginas())
        if len(df) != est["total"]:
            raise ErroColeta(f"Li {len(df)} OS abertas, mas o Effort informa {est['total']}.")
        return repetidos_com_sufixo(df, "os")

    def detalhes_os(self, ctx, os_df: pd.DataFrame, ja_lidas: set[str], ao_ler, limite_s: float,
                    rotulo: str = "fechadas") -> int:
        """Lê a tela de consulta (só leitura) de cada OS ainda sem detalhe no banco e grava em lotes de 100.
        Para no limite de tempo; o que faltar fica para o próximo registro."""
        faltam = os_df[~os_df["os"].isin(ja_lidas) & (os_df["id_effort"] != "")]
        # mais recentes primeiro: são as mais consultadas, e a primeira carga pode levar mais de uma rodada
        faltam = faltam.assign(_f=pd.to_datetime(faltam["fechamento"], format="%d/%m/%Y", errors="coerce")) \
                       .sort_values("_f", ascending=False, na_position="last")
        log.info("Detalhe de OS %s: %s já no banco, %s a ler.", rotulo, len(ja_lidas & set(os_df["os"])), len(faltam))
        inicio, lidas, det, ativ, falhas_seguidas = time.monotonic(), 0, [], [], 0
        for _, o in faltam.iterrows():
            if time.monotonic() - inicio > limite_s:
                log.warning("Limite de %s min atingido; %s OS ficam para o próximo registro.", int(limite_s / 60), len(faltam) - lidas)
                break
            try:
                h = ctx.request.get(f"{self.cfg.url_base}/frmos_alterar.aspx?Codigo={o['id_effort']}&Consultar=S&Origem=P").text()
                d = ler_os_detalhe(h)
                if d.get("os") != o["os"]:
                    raise ErroColeta(f"a tela de consulta devolveu a OS {d.get('os')!r}")
                det.append(d)
                ativ += ler_os_atividades(h, o["os"])
                falhas_seguidas = 0
            except Exception as e:  # noqa: BLE001 - uma OS com problema não derruba a fotografia
                log.warning("Detalhe da OS %s não lido: %s", o["os"], e)
                falhas_seguidas += 1
                if falhas_seguidas >= 20:   # rede ou Effort fora do ar: para e deixa o resto para a próxima rodada
                    log.error("20 OS seguidas sem resposta do Effort; leitura de detalhes interrompida.")
                    break
            lidas += 1
            if len(det) >= 100:
                ao_ler(pd.DataFrame(det), pd.DataFrame(ativ))
                det, ativ = [], []
            if lidas % 500 == 0:
                log.info("Detalhe de OS: %s de %s.", lidas, len(faltam))
        ao_ler(pd.DataFrame(det, columns=COLUNAS_OS_DETALHE), pd.DataFrame(ativ, columns=COLUNAS_OS_ATIVIDADES))
        return lidas

    def fotografar(self, ja_lidas: set[str] | None = None, ao_ler_detalhes=None, limite_detalhes_s: float = 0,
                   ao_ler_abertas=None) -> dict[str, pd.DataFrame]:
        """Cadastros e OS fechadas (devolvidos); OS abertas ficam em self.abertas, com o detalhe gravado
        por ao_ler_abertas a cada registro (elas mudam)."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            navegador = p.chromium.launch(headless=self.cfg.headless)
            ctx = navegador.new_context(viewport={"width": 1600, "height": 900}, locale="pt-BR", accept_downloads=True)
            try:
                self.coletor.page = ctx.new_page()
                self.coletor.page.set_default_timeout(self.cfg.timeout_s * 1000)
                self.coletor.entrar()
                self.coletor.guardar_sessao()
                dados = {}
                for nome, ler in (("equipamentos", self.equipamentos), ("contratos", self.contratos),
                                  ("fornecedores", self.fornecedores), ("fabricantes", self.fabricantes),
                                  ("modelos", self.modelos), ("setores", self.setores), ("planos", self.planos),
                                  ("procedimentos", self.procedimentos), ("motivos_pendencia", self.motivos_pendencia),
                                  ("planos_equipamentos", self.planos_equipamentos),
                                  ("contratos_equipamentos", self.contratos_equipamentos)):
                    dados[nome] = ler(ctx)
                    log.info("%s: %s.", nome.capitalize(), len(dados[nome]))
                contagem = dados["planos_equipamentos"].groupby("plano").size()
                dados["planos"]["n_equipamentos"] = dados["planos"]["nome"].map(contagem).fillna(0).astype(int).astype(str)
                dados["os_fechadas"] = self.os_fechadas()
                log.info("OS fechadas: %s.", len(dados["os_fechadas"]))
                inicio = time.monotonic()
                if ao_ler_abertas:
                    self.abertas = self.os_abertas()
                    log.info("OS abertas: %s.", len(self.abertas))
                    self.detalhes_os(ctx, self.abertas, set(), ao_ler_abertas, limite_detalhes_s, "abertas")
                if ao_ler_detalhes and limite_detalhes_s > 0:
                    resta = limite_detalhes_s - (time.monotonic() - inicio)
                    self.detalhes_os(ctx, dados["os_fechadas"], ja_lidas or set(), ao_ler_detalhes, max(resta, 60))
                return dados
            finally:
                self.coletor.sair()
                navegador.close()


# --------------------------------------------------------------------------- execução
def exportar_atual(pasta: Path, banco: Banco):
    """Cópia legível do estado atual (com o que já sumiu do Effort marcado em 'ate') em dados/snapshots/atual/,
    para abrir sem o app. O banco é a fonte; estas planilhas são refeitas a cada fotografia."""
    pasta.mkdir(parents=True, exist_ok=True)
    for entidade in COLUNAS_SNAPSHOT:
        banco.cadastro(entidade, incluir_removidos=True).to_excel(pasta / f"{entidade}.xlsx", index=False, engine="openpyxl")
    for nome in ("os_detalhe", "os_atividades"):
        banco.tabela(nome).to_excel(pasta / f"{nome}.xlsx", index=False, engine="openpyxl")


def executar(origem: str = "manual", visivel: bool = False, hoje: dt.date | None = None) -> int:
    banco = Banco(config.BANCO)
    ref = referencia(hoje or dt.date.today())
    snap_id = banco.iniciar_snapshot(ref, origem)
    if snap_id is None:
        log.info("O Effort já está em uso por outra coleta ou registro mensal; nada a fazer.")
        return 0
    try:
        if not (config.EFFORT_USUARIO and config.EFFORT_SENHA):
            raise RuntimeError("Defina EFFORT_USUARIO e EFFORT_SENHA no .env.")
        if config.SNAPSHOT_ANEXOS:
            log.warning("SNAPSHOT_ANEXOS ligado, mas o download de anexos ainda não foi implementado; ignorado.")
        fotografo = Fotografo(ConfigColetor(
            url_base=config.EFFORT_URL, usuario=config.EFFORT_USUARIO, senha=config.EFFORT_SENHA,
            headless=config.HEADLESS and not visivel, liberar_acesso=config.EFFORT_LIBERAR_ACESSO,
            empresa=config.EFFORT_EMPRESA, timeout_s=180,
        ))
        dados = fotografo.fotografar(ja_lidas=banco.os_com_detalhe(), ao_ler_detalhes=banco.gravar_os_detalhe,
                                     limite_detalhes_s=config.SNAPSHOT_OS_MAX_MIN * 60,
                                     ao_ler_abertas=lambda d, a: banco.gravar_os_detalhe(d, a, aberta=True))
        vazios = [e for e, df in dados.items() if df.empty]
        if vazios:
            raise RuntimeError(f"O registro mensal veio com cadastro vazio ({', '.join(vazios)}); nada foi gravado.")
        totais = {}
        for entidade, df in dados.items():
            m = banco.aplicar_snapshot(snap_id, ref, entidade, df)
            totais[entidade] = {"linhas": len(df), **m.resumo()}
            log.info("%s: %s linhas; %s", entidade, len(df), m.resumo())
        totais["os_com_detalhe"] = len(banco.os_com_detalhe())
        if getattr(fotografo, "abertas", None) is not None:
            banco.gravar_os_abertas(fotografo.abertas)
            totais["os_abertas"] = len(fotografo.abertas)
        banco.concluir_snapshot(snap_id, totais)
        gravar_log(config.SNAPSHOTS / ref / "mudancas.xlsx", banco.mudancas(referencia=ref), ref)
        cad = {e: banco.cadastro(e) for e in COLUNAS_SNAPSHOT}
        cfg = alertas.carregar(config.RAIZ / "alertas.yaml")
        alertas.relatorio_contratos(cad["contratos"], cad["equipamentos"], cfg, hoje or dt.date.today(),
                                    cad["contratos_equipamentos"]) \
            .to_excel(config.SNAPSHOTS / ref / "relatorio_contratos.xlsx", index=False, engine="openpyxl")
        alertas.relatorio_qualidade(cad["equipamentos"], cad["planos"], cad["setores"], cad["fornecedores"], cad["contratos"], cfg) \
            .to_excel(config.SNAPSHOTS / ref / "relatorio_qualidade.xlsx", index=False, engine="openpyxl")
        exportar_atual(config.SNAPSHOTS / "atual", banco)
        from .pastas import gerar_zip
        gerar_zip(banco, config.SNAPSHOTS / ref / f"historico_equipamentos_{ref}.zip")
        mantidas = banco.limpar_antigos(config.SNAPSHOT_MANTER)
        for pasta in config.SNAPSHOTS.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]"):
            if pasta.is_dir() and pasta.name not in mantidas:
                shutil.rmtree(pasta)
                log.info("Registro de %s apagado (só os %s últimos ficam).", pasta.name, config.SNAPSHOT_MANTER)
        log.info("Registro mensal %s (%s) concluído. Arquivos em %s.", snap_id, ref, config.SNAPSHOTS)
        return 0
    except Exception as e:  # noqa: BLE001 - qualquer falha vira registro no banco
        log.error("Registro mensal %s falhou: %s", snap_id, e)
        banco.falhar_snapshot(snap_id, f"{e}\n\n{traceback.format_exc()}")
        return 1
    except KeyboardInterrupt:
        banco.falhar_snapshot(snap_id, "Interrompida pelo usuário (Ctrl+C).")
        return 130


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agora", action="store_true", help="registra agora")
    ap.add_argument("--visivel", action="store_true", help="mostra o navegador")
    ap.add_argument("--origem", default="manual", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if not a.agora:
        ap.print_help()
        sys.exit(2)
    sys.exit(executar(origem=a.origem, visivel=a.visivel))


if __name__ == "__main__":
    main()
