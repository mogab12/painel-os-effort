# Painel de OS abertas — Manutenção de Equipamentos Hospitalares (Effort)

Painel web com as OS abertas e pendentes do Effort (Global Things), separadas por quem precisa agir e por responsável, com a ação sugerida para cada uma. Os dados são coletados do Effort automaticamente em horários definidos, ou na hora pelo botão **Atualizar agora**.

Não depende de nenhuma licença pessoal nem de um navegador aberto: roda num servidor com um usuário de serviço do Effort.

## Como funciona

```
 Effort (site)  ──►  coletor (Playwright, navegador sem tela)  ──►  SQLite (histórico)  ──►  painel (Streamlit)
                         ▲                                                                       │
                   agendador (cron)                                              botão "Atualizar agora"
```

- **`painel/coletor.py`** entra no Effort, abre a pesquisa de OS com "Aberta" e "Pendente", lê todas as páginas da grade e usa a árvore de filtros da esquerda ("Última Pendência" e "Responsável") para saber a pendência e o responsável de cada OS. Para cada OS pendente, abre a OS e as pendências em aberto em modo consulta (só leitura) para ler quando a pendência foi aberta e o prazo de conclusão; com mais de uma pendência em aberto, vale a que vence primeiro. O Effort não tem API pública; o coletor faz o mesmo que uma pessoa faria na tela.
- **`regras.yaml`** diz, para cada pendência, quem precisa agir, quão fácil é resolver e qual a ação sugerida. **Para mudar a classificação, edite este arquivo**; não precisa mexer no código. Pendências novas que nenhuma regra reconhece aparecem num aviso no topo do painel.
- **`painel/regras.py`** aplica as regras: idade da OS, facilidade, duplicidade (mais de uma OS aberta do mesmo tipo no mesmo equipamento; corretiva + preventiva não conta) e prioridade.
- **`painel/banco.py`** guarda cada coleta no SQLite (`dados/painel.db`). Com isso o painel mostra a evolução do backlog ao longo dos dias.
- **`app.py`** é o painel: aba "Visão geral" e uma aba por responsável, com as OS da mais fácil para a mais difícil.
- **`painel/agendador.py`** dispara a coleta nos horários de `AGENDA_CRON`.

## Antes de tudo: o usuário do Effort

O Effort aceita **uma sessão por usuário**. Se o coletor usar o login de alguém, cada coleta derruba a sessão dessa pessoa. Peça à administração do Effort (ou à Global Things) um **usuário dedicado**, de preferência só leitura, que enxergue as OS da oficina Manutenção de Equipamentos, por exemplo `painel.ec`.

Peça também, se possível, autorização formal da TI para a automação e para hospedar o painel na rede do hospital.

## Rodando no seu computador (para testar)

Requer Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate            # no Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium

cp .env.example .env                 # e preencha usuário e senha
python -m painel.coletar --demo      # dados fictícios, só para ver o painel
streamlit run app.py                 # abre em http://localhost:8501
```

Primeira coleta real, com o navegador visível para acompanhar:

```bash
python -m painel.coletar --visivel
```

Se o login falhar, provavelmente os campos da tela de login têm outros seletores: ajuste `sel_usuario`, `sel_senha` e `sel_entrar` em `ConfigColetor` (`painel/coletor.py`). As demais telas (pesquisa, grade, paginação e filtros) usam os ids internos do Effort (`frmos_pesquisa.aspx`, `#resultado`, `#Grade`, `#tree1`, `#btnproximo`, `#btnVoltar`, `#infoPaginacao`), conferidos em outubro de 2026.

Testes:

```bash
pip install pytest
pytest
```

## Hospedando (servidor do hospital)

O jeito mais simples é Docker num servidor ou máquina virtual da rede interna:

```bash
cp .env.example .env     # preencher
docker compose up -d --build
```

Isso sobe dois serviços: o **painel** na porta 8501 e o **agendador**, que coleta nos horários de `AGENDA_CRON`. Os dados ficam na pasta `./dados`. Para expor como `https://painel-ec.hospital...`, peça à TI um proxy reverso (nginx, IIS) apontando para a porta 8501.

Sem Docker, num servidor Linux: instale como acima e crie dois serviços do systemd, `streamlit run app.py` e `python -m painel.agendador`. No Windows, use o Agendador de Tarefas para rodar `python -m painel.coletar --origem agendada` nos horários desejados.

**Cuidados com a hospedagem:**
- São dados internos do hospital (equipamentos, setores, nomes de técnicos). Prefira a rede interna. Se for para a internet (Render, Railway, VPS), configure `SENHA_PAINEL` e HTTPS.
- Não use o Streamlit Community Cloud com repositório público.
- Nunca suba o `.env` nem a pasta `dados/` para o Git (já estão no `.gitignore`).

## Manutenção

| Situação | O que fazer |
|---|---|
| Apareceu o aviso "Pendências sem regra" | Adicionar a pendência em `regras.yaml` |
| Quer mudar quem age ou a facilidade de uma pendência | Editar a regra em `regras.yaml` |
| Coleta falhando (aviso no topo do painel) | Ver o erro no aviso; rodar `python -m painel.coletar --visivel` para ver onde para |
| Global Things mudou a tela | Ajustar ids ou seletores em `painel/coletor.py` |
| Trocar os horários | Mudar `AGENDA_CRON` no `.env` e reiniciar o agendador |

Uma coleta completa leva de 5 a 10 minutos: o coletor clica em cada pendência e em cada responsável da árvore de filtros e depois lê o prazo de cada OS pendente (cerca de 1 segundo por OS).
