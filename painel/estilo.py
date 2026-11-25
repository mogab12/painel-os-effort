from __future__ import annotations

import html

# cores que funcionam no tema claro e escuro
PALETA = {
    "texto_graf": "#8C8479", "grade": "#8C8479", "destaque": "#5A78B0", "foco": "#D29522",
    "destaque_aba": "#4A67A0",
    "quem": ["#5A78B0", "#C99A3B", "#A0628A", "#8F887C"],
    "fac": ["#6E9443", "#5A78B0", "#B5AD9F"],
    "idade": ["#A3B4D6", "#7590C2", "#45619A", "#C8573B"],
}


def paleta(_tema: str | None = None) -> dict:
    return PALETA


def css() -> str:
    return """
<style>
:root { --linha: color-mix(in srgb, currentColor 18%%, transparent); }
html, body, .stApp { font-variant-numeric: tabular-nums; }
.stApp { -webkit-tap-highlight-color: transparent; }
button, [role="tab"], [role="radio"], summary, label { touch-action: manipulation; }
.block-container { padding-top: 2rem; max-width: 1200px; overflow-x: hidden; }
header[data-testid="stHeader"] { background: transparent; }

h1, h2, h3 { font-weight: 600; letter-spacing: 0; text-wrap: balance; scroll-margin-top: 4rem; }
.stApp h1 { font-size: 1.85rem !important; line-height: 1.2 !important; }
h2, h3 { margin-top: 2rem; }
h3 { font-size: 1.15rem; }
.suave { opacity: .72; }

/* legenda dos gráficos */
.leg-graf { display: flex; flex-wrap: wrap; gap: .3rem 1.2rem; margin: 0 0 .4rem; font-size: .88rem; }
.leg-graf span { display: inline-flex; align-items: center; gap: .4rem; }
.leg-graf i { width: 10px; height: 10px; border-radius: 2px; flex: none; }

/* faixa de resumo */
.faixa-titulo { font-family: 'Source Serif 4', Georgia, serif; font-size: 1.3rem; line-height: 1.5; font-weight: 500; max-width: 46rem; margin: .4rem 0 1.1rem; }
.faixa-titulo b { font-weight: 600; }
.faixa { display: flex; height: 14px; gap: 2px; overflow: hidden; }
.faixa > span { display: block; min-width: 3px; }
.legenda { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: .6rem 1.5rem; margin: .8rem 0 1.6rem; }
.legenda div { display: flex; align-items: baseline; gap: .55rem; font-size: .95rem; }
.legenda i { width: 9px; height: 9px; flex: none; border-radius: 50%%; }
.legenda b { font-size: 1.2rem; font-weight: 600; }

/* métricas: linha no topo, sem cartão */
[data-testid="stMetric"] { border-top: 2px solid currentColor; padding: .5rem 0 0; background: none; }
[data-testid="stMetricLabel"] p { white-space: normal; overflow: visible; text-overflow: clip; line-height: 1.25; font-size: .9rem; }
[data-testid="stMetricValue"] { font-family: 'Source Serif 4', Georgia, serif; font-weight: 600; }

/* abas: um botão por técnico, com a selecionada preenchida */
[data-testid="stTabs"] [role="tablist"] { flex-wrap: wrap; gap: .5rem; padding-bottom: 1rem; margin-bottom: .5rem;
  border-bottom: 1px solid var(--linha); overflow: visible; }
[data-testid="stTabs"] .react-aria-SelectionIndicator, [data-testid="stTabs"] [data-baseweb="tab-highlight"] { display: none; }
[data-testid="stTabs"] [role="tab"] { height: auto; padding: .6rem 1.1rem; border-radius: 6px;
  border: 1px solid color-mix(in srgb, currentColor 35%%, transparent);
  background: color-mix(in srgb, currentColor 5%%, transparent); transition: background-color .15s ease; }
[data-testid="stTabs"] [role="tab"] p { font-size: 1rem; font-weight: 500; }
[data-testid="stTabs"] [role="tab"]:hover { background: color-mix(in srgb, currentColor 12%%, transparent); }
[data-testid="stTabs"] [role="tab"][aria-selected="true"] { background: %(destaque_aba)s; border-color: %(destaque_aba)s; color: #fff; }
[data-testid="stTabs"] [role="tab"][aria-selected="true"] p { font-weight: 700; color: #fff; }
[data-testid="stTabs"] [role="tab"]:first-child { margin-right: .75rem; }
[data-testid="stTabs"] [role="tab"]:last-child { margin-left: .75rem; }

/* botões */
.stButton > button, .stDownloadButton > button { border-radius: 3px; font-weight: 600; }
:focus-visible { outline: 3px solid %(foco)s !important; outline-offset: 2px; }

/* tabela e avisos */
[data-testid="stDataFrame"] { border: 1px solid var(--linha); }
[data-testid="stAlert"] { border-radius: 3px; }
[data-testid="stExpander"] details { border-radius: 3px; border-color: var(--linha); }
.leg-graf, .legenda div { min-width: 0; overflow-wrap: anywhere; }

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition-duration: .01ms !important; animation-duration: .01ms !important; animation-iteration-count: 1 !important; scroll-behavior: auto !important; }
}
@media (max-width: 640px) {
  .block-container { padding-left: 1rem; padding-right: 1rem; }
  .stApp h1 { font-size: 1.4rem !important; }
  .faixa-titulo { font-size: 1.1rem; }
}
</style>
""" % PALETA


def faixa(contagens: dict[str, int], cores: dict[str, str], resumo_html: str) -> str:
    total = sum(contagens.values()) or 1
    segs = "".join(
        f'<span style="flex:{n};background:{cores[k]}" title="{html.escape(k)}: {n}"></span>'
        for k, n in contagens.items() if n)
    leg = "".join(
        f'<div><i aria-hidden="true" style="background:{cores[k]}"></i><b>{n}</b><span class="suave">{html.escape(k)} ({n / total:.0%})</span></div>'
        for k, n in contagens.items() if n)
    return f'<p class="faixa-titulo">{resumo_html}</p><div class="faixa" role="img" ' \
           f'aria-label="OS por quem precisa agir">{segs}</div><div class="legenda">{leg}</div>'


def legenda_grafico(itens: list[str], cores: list[str]) -> str:
    # em html pra quebrar linha sozinha
    return '<div class="leg-graf">' + "".join(
        f'<span><i aria-hidden="true" style="background:{c}"></i>{html.escape(n)}</span>' for n, c in zip(itens, cores)) + "</div>"
