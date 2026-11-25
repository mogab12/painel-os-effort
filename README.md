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

## Histórico mensal do Effort

Uma vez por mês (dia 1º, às 2h; `SNAPSHOT_CRON` no `.env`) o sistema guarda um registro (log) dos cadastros da oficina no banco do painel, para termos um registro próprio caso o Effort fique fora do ar ou algum dado se perca. Para rodar na hora: `python -m painel.snapshot --agora`. O resultado aparece na aba **Histórico** do painel.

| Cadastro | De onde vem | Chave |
|---|---|---|
| Equipamentos (ativos e inativos) | exportação Excel de `Equipamento.aspx`; criticidade, prioridade, plano, contrato e situação vêm de uma exportação por valor de filtro | TAG |
| Contratos | exportação de `ContratoManutencao.aspx` | número |
| Fornecedores, fabricantes e modelos | exportações de `frmfornecedor.aspx`, `frmfabricante.aspx` e `Modelo.aspx` | código |
| Setores e planos de manutenção | árvores de `Setor.aspx` e `PlanoManutencao.aspx` (sigla, grupo, periodicidade, procedimento, tipo, ativo, nº de equipamentos) | id do Effort |
| Procedimentos de manutenção | árvore de `frmprocedimentoraiz.aspx` (nome, versão, data de publicação) | id do Effort |
| Motivos de pendência | a mesma API que a tela `Pendencia.aspx` usa (descrição, prazo, ativo) | id do Effort |
| Planos x equipamentos | relatório "Planos de Manutenção por Equipamento" em XLS (plano, periodicidade, última realização, próxima abertura, responsável) | TAG + plano |
| Contratos x equipamentos | relatório "Contratos com Fornecedores", analítico, em XLS (contrato, equipamento coberto e valor) | nº do contrato + TAG |
| OS fechadas | pesquisa de OS só com "Fechada" | nº da OS |
| Detalhe e atividades de cada OS fechada | tela de consulta da OS: responsável, datas, custos, pendências, ocorrências e causas, serviços, e as peças e serviços externos com quantidade, valor e total | nº da OS |

**Quanto fica guardado.** Só os dois últimos registros (`SNAPSHOT_MANTER`, padrão 2). Ao terminar o terceiro, o mais antigo é apagado: a pasta do mês em `dados/snapshots/`, as mudanças daquele registro e os itens que tinham saído do Effort naquele mês. O detalhe das OS fica, porque é o conteúdo das pastas por equipamento e relê-lo custaria 1h40 por mês.

**Como é gravado.** Só a diferença: o que entrou é inserido, os campos que mudaram são atualizados, e o que sumiu do Effort é marcado com o mês em que sumiu (não é apagado). O que não mudou não é tocado. Cada entrada, saída e mudança (campo, antes e depois) fica em `hist_mudancas`. O detalhe de cada OS fechada é lido uma única vez; a primeira carga (cerca de 5.800 OS, 1 segundo cada) leva perto de 1h40 e, se passar de `SNAPSHOT_OS_MAX_MIN` (180 min), continua no registro seguinte, das OS mais recentes para as mais antigas.

**Só leitura.** O coletor apenas pesquisa, exporta e abre telas em modo consulta. Não baixa anexos. Faz logout no fim e nunca roda ao mesmo tempo que a coleta de OS (as duas usam a mesma trava, porque o Effort aceita uma sessão por usuário). As observações em texto livre das OS não são guardadas.

**Relatórios** (aba Histórico e arquivos do mês; limites em `alertas.yaml`):
- contratos: vencidos, a vencer em 30/60/90 dias, valor zerado, número repetido, contrato vigente sem equipamentos vinculados, equipamento em comodato ou locado sem contrato;
- qualidade do cadastro: equipamento ativo sem TAG, sem criticidade ou sem prioridade; criticidade alta ou média sem plano; plano ativo sem periodicidade; setor sem sigla ou com ponto final; fornecedor com contrato sem CNPJ ou telefone.

**Pastas por equipamento** (`dados/snapshots/AAAA-MM/historico_equipamentos_AAAA-MM.zip`, também no botão "Baixar pastas" da aba Histórico). É o jeito mais fácil de consultar sem o app: uma pasta por equipamento, com nome no formato TAG - descrição - modelo - PAT patrimônio. Dentro dela há uma pasta por OS, aberta ou fechada, e dentro de cada OS a subpasta `pendencias`, com uma página por pendência. Tudo é página HTML, que abre no navegador e é ligada por links. Comece pelo `indice.html`, que tem busca. A página da OS mostra quem abriu, o técnico, as datas, o defeito e a causa, o serviço feito, as pendências com os dias de cada uma, a mão de obra, os materiais com preço, os serviços externos e os custos. O zip é refeito no fim de cada registro (cerca de 30 MB). O detalhe das OS abertas é relido a cada registro, porque elas mudam; quando a OS fecha, o detalhe é lido de novo e fica gravado.

**Arquivos para abrir sem o app** (`dados/snapshots/`):
- `AAAA-MM/mudancas.xlsx`: o que mudou naquele mês, com um resumo e uma aba por cadastro;
- `AAAA-MM/relatorio_contratos.xlsx` e `relatorio_qualidade.xlsx`: os relatórios daquele mês;
- `atual/*.xlsx`: o estado atual de cada cadastro (inclusive o que já saiu, na coluna `ate`), o detalhe e as atividades das OS. São refeitos a cada registro.

**O que ele NÃO é.** Não é um backup que volta para o Effort: é uma cópia para consulta. Se o Effort perder dados, a restauração tem de ser feita pela Globalthings; este registro serve para conferir o que existia e para migrar para outro sistema, se for preciso.

**Espaço.** O banco ocupa cerca de 8 MB e cresce uns 0,5 MB por mês (umas 300 OS fechadas novas). Cada mês guardado ocupa cerca de 40 MB em `dados/snapshots/AAAA-MM/`, quase tudo o zip das pastas; como só ficam dois meses, o total fica perto de 90 MB. As planilhas de `atual/` somam cerca de 3 MB e são sobrescritas.

## Manutenção

| Situação | O que fazer |
|---|---|
| Apareceu o aviso "Pendências sem regra" | Adicionar a pendência em `regras.yaml` |
| Quer mudar quem age ou a facilidade de uma pendência | Editar a regra em `regras.yaml` |
| Coleta falhando (aviso no topo do painel) | Ver o erro no aviso; rodar `python -m painel.coletar --visivel` para ver onde para |
| Global Things mudou a tela | Ajustar ids ou seletores em `painel/coletor.py` |
| Trocar os horários | Mudar `AGENDA_CRON` no `.env` e reiniciar o agendador |

Uma coleta completa leva de 5 a 10 minutos: o coletor clica em cada pendência e em cada responsável da árvore de filtros e depois lê o prazo de cada OS pendente (cerca de 1 segundo por OS).
