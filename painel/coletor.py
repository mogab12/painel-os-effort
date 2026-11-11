# Effort não tem API, então o playwright faz o que uma pessoa faria.
# usar um usuário só pra isso, o Effort só deixa uma sessão por usuário
from __future__ import annotations

import datetime as dt
import html as html_lib
import logging
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser

import pandas as pd

log = logging.getLogger("painel.coletor")

RE_OS = re.compile(r"^\d{9}$")
RE_CONTAGEM = re.compile(r"\s*\(\d+\)\s*$")

# doc() busca o frame de novo toda vez, o Effort troca ele a cada filtro
JS_DOC = "() => { const f = document.getElementById('resultado'); return f && f.contentDocument; }"

JS_ESTADO = """() => {
  const f = document.getElementById('resultado');
  const d = f && f.contentDocument;
  if (!d || d.readyState !== 'complete') return null;
  const total = d.getElementById('hidTotalRegistros');
  const paginas = d.getElementById('hidTotalPaginas');
  if (!total) return null;
  const pag = ((d.getElementById('infoPaginacao') || {}).textContent || '1 de 1').trim();  // ex.: "1 de 2"
  const primeira = [...d.querySelectorAll('#Grade tr')].map(r => r.cells[1] && r.cells[1].innerText.trim())
                   .find(t => /^\\d{9}$/.test(t || '')) || '';
  return {marca: d.__painelMarca === true, total: Number(total.value), paginas: Number((paginas || {}).value || 1),
          primeira, pagina: Number((pag.match(/^(\\d+)/) || [0, 1])[1])};
}"""

JS_MARCAR = "() => { const d = document.getElementById('resultado').contentDocument; d.__painelMarca = true; }"

JS_LER_GRADE = """() => {
  const d = document.getElementById('resultado').contentDocument;
  // células da linha + o id interno da OS (valor do checkbox) no fim
  return [...d.querySelectorAll('#Grade tr')]
         .map(r => [...r.cells].map(c => c.innerText.trim()).concat([(r.querySelector('input[name=dgradechk]') || {}).value || '']))
         .filter(c => /^\\d{9}$/.test(c[1] || ''));
}"""

JS_FOLHAS = """(prefixo) => {
  const d = document.getElementById('resultado').contentDocument;
  const re = new RegExp('^' + prefixo + '\\\\d+$');
  return [...d.querySelectorAll('#tree1 td[id]')].filter(td => re.test(td.id))
         .map(td => ({id: td.id, texto: td.textContent.trim()}));
}"""

JS_CLICAR = """(id) => {
  const d = document.getElementById('resultado').contentDocument;
  const el = d.getElementById(id);
  if (!el) throw new Error('Elemento não encontrado no Effort: ' + id);
  el.click();
}"""


# tela de escolher empresa depois do login
JS_ESCOLHER_EMPRESA = """(alvo) => {
  const norm = s => (s || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const itens = [...document.querySelectorAll('#lista_empresas li')];
  if (!itens.length || document.getElementById('lista_empresas').offsetParent === null) return null;
  const t = norm(alvo);
  const li = itens.find(e => norm(e.textContent) === t)
          || itens.find(e => norm(e.textContent).startsWith(t) || t.startsWith(norm(e.textContent)));
  if (!li) return {ok: false, opcoes: itens.map(e => e.textContent.trim())};
  li.click();
  return {ok: true, nome: li.textContent.trim()};
}"""


@dataclass
class ConfigColetor:
    url_base: str
    usuario: str
    senha: str
    headless: bool = True
    liberar_acesso: bool = True  # só com usuário dedicado
    timeout_s: int = 90
    empresa: str = "Manutenção de Equipamentos"
    # seletores do login
    sel_usuario: str = "#login:visible, input:not([type]):visible, input[type=text]:visible, input[type=email]:visible"
    sel_senha: str = "#senha:visible, input[type=password]:visible"
    sel_entrar: str = "#logar:visible, input[type=submit]:visible, button[type=submit]:visible, button:has-text('Acessar'), a:has-text('Entrar')"


class ErroColeta(RuntimeError):
    pass


class Coletor:
    def __init__(self, cfg: ConfigColetor):
        self.cfg = cfg
        self.page = None

    def _esperar_resultado(self, mudou_de: dict | None = None) -> dict:
        fim = time.monotonic() + self.cfg.timeout_s
        while time.monotonic() < fim:
            est = self.page.evaluate(JS_ESTADO)
            if est:
                if mudou_de is None:
                    return est
                recarregou = not est["marca"]
                conteudo_mudou = (est["total"], est["primeira"], est["pagina"]) != (
                    mudou_de["total"], mudou_de["primeira"], mudou_de["pagina"])
                if recarregou or conteudo_mudou:
                    time.sleep(0.5)  # grade ainda desenhando
                    return self.page.evaluate(JS_ESTADO) or est
            time.sleep(0.4)
        raise ErroColeta("O Effort não respondeu a tempo (frame de resultado).")

    def _acao(self, id_elemento: str) -> dict:
        antes = self._esperar_resultado()
        self.page.evaluate(JS_MARCAR)
        antes["marca"] = True
        self.page.evaluate(JS_CLICAR, id_elemento)
        return self._esperar_resultado(mudou_de=antes)

    def entrar(self):
        cfg, page = self.cfg, self.page
        page.goto(f"{cfg.url_base}/Login.aspx", wait_until="domcontentloaded")
        if "Desktop.aspx" in page.url:
            return
        liberar = page.get_by_text("Liberar Acesso", exact=False)
        if liberar.count() and liberar.first.is_visible():
            self._liberar(liberar)
        if page.locator(cfg.sel_senha).count():
            page.locator(cfg.sel_usuario).first.fill(cfg.usuario)
            page.locator(cfg.sel_senha).first.fill(cfg.senha)
            entrar = page.locator(cfg.sel_entrar)
            if entrar.count():
                entrar.first.click()
            else:
                page.locator(cfg.sel_senha).first.press("Enter")
        # pode cair no aviso de sessão ou na escolha de empresa
        from playwright.sync_api import Error as ErroPlaywright

        ultimo_clique = 0.0
        for _ in range(int(cfg.timeout_s / 0.5)):
            if "Desktop.aspx" in page.url:
                return
            try:
                liberar = page.get_by_text("Liberar Acesso", exact=False)
                if liberar.count() and liberar.first.is_visible():
                    self._liberar(liberar)
                elif cfg.empresa and time.monotonic() - ultimo_clique > 8:
                    escolha = page.evaluate(JS_ESCOLHER_EMPRESA, cfg.empresa)
                    if escolha and not escolha["ok"]:
                        raise ErroColeta(f"A empresa \"{cfg.empresa}\" não está na lista do Effort. Opções: "
                                         + "; ".join(escolha["opcoes"]) + ". Ajuste EFFORT_EMPRESA no .env.")
                    if escolha:
                        ultimo_clique = time.monotonic()
                        log.info("Empresa escolhida: %s", escolha["nome"])
            except ErroPlaywright as e:
                # página trocou no meio, tenta de novo
                if "context was destroyed" not in str(e) and "navigat" not in str(e):
                    raise
            time.sleep(0.5)
        raise ErroColeta(f"Não foi possível entrar no Effort (parou em {page.url}). "
                         "Verifique usuário, senha e os seletores de login.")

    def _liberar(self, botao):
        if not self.cfg.liberar_acesso:
            raise ErroColeta("Já existe uma sessão aberta com este usuário e liberar_acesso está desligado.")
        log.warning("Sessão anterior em andamento: clicando em 'Liberar Acesso'.")
        botao.first.click()
        self.page.wait_for_load_state("domcontentloaded")

    def pesquisar(self) -> dict:
        page = self.page
        page.goto(f"{self.cfg.url_base}/frmos_pesquisa.aspx", wait_until="domcontentloaded")
        page.wait_for_selector("#chkaberta", timeout=self.cfg.timeout_s * 1000)
        for sel, marcar in (("#chkaberta", True), ("#chkpendente", True), ("#chkfechada", False), ("#chkcancelada", False)):
            if page.is_checked(sel) != marcar:
                page.click(sel)
        page.click("#btnprocurar")
        est = self._esperar_resultado()
        log.info("Pesquisa: %s OS em %s página(s).", est["total"], est["paginas"])
        return est

    def ler_todas_paginas(self) -> list[list[str]]:
        est = self._esperar_resultado()
        linhas, vistos = [], set()
        for pagina in range(max(1, est["paginas"])):
            if pagina > 0:
                est = self._acao("btnproximo")
            for c in self.page.evaluate(JS_LER_GRADE):
                if c[1] not in vistos:
                    vistos.add(c[1])
                    linhas.append(c)
        if len(linhas) != est["total"]:
            log.warning("Li %s OS, mas o Effort informa %s.", len(linhas), est["total"])
        return linhas

    def _voltar_ao_inicio(self):
        est = self._esperar_resultado()
        if est["pagina"] > 1:
            self._acao("btnprimeirapagina")

    def mapear_por_filtro(self, prefixo: str) -> dict[str, str]:
        mapa: dict[str, str] = {}
        folhas = self.page.evaluate(JS_FOLHAS, prefixo)
        log.info("Filtro %s: %s itens.", prefixo, len(folhas))
        for folha in folhas:
            nome = RE_CONTAGEM.sub("", folha["texto"]).strip()
            self._voltar_ao_inicio()
            # id muda, procura pelo texto
            atuais = {RE_CONTAGEM.sub("", f["texto"]).strip(): f["id"] for f in self.page.evaluate(JS_FOLHAS, prefixo)}
            if nome not in atuais:
                log.warning("Item '%s' sumiu da árvore; pulando.", nome)
                continue
            self._acao(atuais[nome])
            for c in self.ler_todas_paginas():
                mapa[c[1]] = nome
            self._acao("btnVoltar")
        return mapa

    def ler_prazos(self, linhas: list[list[str]]) -> dict[str, dict]:
        # pega a pendência que vence primeiro de cada OS
        req, base = self.page.context.request, self.cfg.url_base
        pendentes = [c for c in linhas if (c[5] if len(c) > 5 else "").strip().upper().startswith("P") and c[-1].isdigit()]
        log.info("Prazos: lendo as pendências de %s OS pendentes.", len(pendentes))
        prazos, falhas = {}, 0
        for i, c in enumerate(pendentes, 1):
            interno = c[-1]
            try:
                r = req.get(f"{base}/frmos_alterar.aspx?Codigo={interno}&Consultar=S&Origem=P")
                abertas = [ler_pendencia(req.get(
                    f"{base}/frmospendencia_alterar.aspx?OS={interno}&Codigo={cp}&Consultar=S").text())
                    for cp in pendencias_abertas_da_os(r.text())]
                escolhida = vence_primeiro(abertas)
                if escolhida:
                    prazos[c[1]] = escolhida
            except Exception as e:  # noqa: BLE001
                falhas += 1
                log.warning("Prazo da OS %s não lido: %s", c[1], e)
            if i % 50 == 0:
                log.info("Prazos: %s de %s OS.", i, len(pendentes))
        if pendentes and falhas == len(pendentes):
            raise ErroColeta("Não foi possível ler o prazo de nenhuma pendência (telas de consulta do Effort mudaram?).")
        return prazos

    def coletar(self) -> pd.DataFrame:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            navegador = p.chromium.launch(headless=self.cfg.headless)
            try:
                self.page = navegador.new_page(viewport={"width": 1600, "height": 900}, locale="pt-BR")
                self.page.set_default_timeout(self.cfg.timeout_s * 1000)
                self.entrar()
                self.pesquisar()
                linhas = self.ler_todas_paginas()
                pendencias = self.mapear_por_filtro("Pendencia")
                responsaveis = self.mapear_por_filtro("Responsavel")
                prazos = self.ler_prazos(linhas)
            finally:
                navegador.close()
        return montar_tabela(linhas, pendencias, responsaveis, prazos)


def separar_equipamento(texto: str) -> tuple[str, str, str]:
    # equipamento, setor e plano
    partes = [p.strip() for p in texto.split("\n") if p.strip()]
    equip = partes[0] if partes else ""
    setor = partes[1] if len(partes) > 1 else ""
    plano = next((p.split(":", 1)[1].strip() for p in partes[2:] if p.upper().startswith("PLANO")), "")
    return equip, setor, plano


# pendência aberta = ico_pendencia.png
RE_ATIVIDADE = re.compile(r"<tr\b[^>]*GSR\(this, event, (?:&#39;|')(\d+)(?:&#39;|')\)[^>]*>(.*?)</tr>", re.S | re.I)
CAMPOS_PENDENCIA = {"cbopendencia": "pendencia", "txtdatapendenciaabertura": "abertura_data",
                    "txthorapendenciaabertura": "abertura_hora", "txtdataprazo": "prazo_data", "txthoraprazo": "prazo_hora"}


def pendencias_abertas_da_os(html: str) -> list[str]:
    return [cod for cod, corpo in RE_ATIVIDADE.findall(html) if "ico_pendencia.png" in corpo]


class _Campos(HTMLParser):
    def __init__(self):
        super().__init__()
        self.valores: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "input" and a.get("id") in CAMPOS_PENDENCIA:
            self.valores[CAMPOS_PENDENCIA[a["id"]]] = html_lib.unescape(a.get("value") or "").strip()


def ler_pendencia(html: str) -> dict:
    p = _Campos()
    p.feed(html)
    v = p.valores
    return {"pendencia": v.get("pendencia", ""),
            "abertura": f"{v.get('abertura_data', '')} {v.get('abertura_hora', '')}".strip(),
            "prazo": f"{v.get('prazo_data', '')} {v.get('prazo_hora', '')}".strip()}


def _data(texto: str) -> dt.datetime | None:
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(texto, fmt)
        except ValueError:
            pass
    return None


def vence_primeiro(pendencias: list[dict]) -> dict | None:
    # sem prazo vai pro fim
    if not pendencias:
        return None
    return min(pendencias, key=lambda p: _data(p["prazo"]) or dt.datetime.max)


def montar_tabela(linhas, pendencias, responsaveis, prazos=None) -> pd.DataFrame:
    prazos = prazos or {}
    registros = []
    for c in linhas:
        # check, OS, código, equip/setor, tipo, situação, abertura, fechamento, id
        equip, setor, plano = separar_equipamento(c[3] if len(c) > 3 else "")
        prazo = prazos.get(c[1], {})
        registros.append({
            "os": c[1],
            "codigo": c[2] if len(c) > 2 else "",
            "equipamento": equip.split(" - ")[0].strip(),
            "modelo": equip,
            "setor": setor,
            "plano": plano,
            "tipo": c[4] if len(c) > 4 else "",
            "situacao": (c[5] if len(c) > 5 else "").strip()[:1].upper(),
            "abertura": c[6] if len(c) > 6 else "",
            "ultima_pendencia": prazo.get("pendencia") or pendencias.get(c[1], ""),
            "responsavel": responsaveis.get(c[1], ""),
            "pendencia_abertura": prazo.get("abertura", ""),
            "pendencia_prazo": prazo.get("prazo", ""),
        })
    return pd.DataFrame(registros)
