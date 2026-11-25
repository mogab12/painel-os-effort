from __future__ import annotations

import datetime as dt
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

ESQUEMA = """
CREATE TABLE IF NOT EXISTS coletas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inicio TEXT NOT NULL,
    fim TEXT,
    status TEXT NOT NULL,          -- rodando | ok | erro
    origem TEXT,                   -- agendada | manual | demo
    total INTEGER,
    erro TEXT
);
CREATE TABLE IF NOT EXISTS os (
    coleta_id INTEGER NOT NULL REFERENCES coletas(id),
    os TEXT NOT NULL,
    codigo TEXT, equipamento TEXT, modelo TEXT, setor TEXT, plano TEXT,
    tipo TEXT, situacao TEXT, abertura TEXT, ultima_pendencia TEXT, responsavel TEXT,
    pendencia_abertura TEXT, pendencia_prazo TEXT,   -- da pendência em aberto que vence primeiro
    PRIMARY KEY (coleta_id, os)
);
-- fotografia mensal dos cadastros (painel/snapshot.py), gravada de forma incremental:
--   hist_<cadastro>: uma linha por registro; desde = mês em que apareceu, ate = mês em que sumiu (NULL = existe)
--   hist_mudancas: o que entrou, saiu, mudou ou voltou em cada fotografia
--   os_detalhe / os_atividades: detalhe de cada OS fechada, gravado uma única vez
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referencia TEXT NOT NULL,      -- AAAA-MM
    inicio TEXT NOT NULL,
    fim TEXT,
    status TEXT NOT NULL,          -- rodando | ok | erro
    origem TEXT,                   -- agendada | manual
    totais TEXT,                   -- JSON {entidade: linhas}
    erro TEXT
);
CREATE TABLE IF NOT EXISTS hist_mudancas (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    referencia TEXT NOT NULL,
    cadastro TEXT NOT NULL,
    chave TEXT NOT NULL,
    tipo TEXT NOT NULL,            -- entrou | saiu | mudou | voltou
    campo TEXT, antes TEXT, depois TEXT
);
CREATE INDEX IF NOT EXISTS ix_hist_mudancas ON hist_mudancas (cadastro, chave);
CREATE TABLE IF NOT EXISTS os_detalhe (
    os TEXT PRIMARY KEY, responsavel TEXT, prioridade TEXT, complexidade TEXT, abertura TEXT, fechamento TEXT,
    parada TEXT, funcionamento TEXT, aberta_por TEXT,
    custo_pecas TEXT, custo_servicos_externos TEXT, custo_mao_de_obra TEXT, custo_total TEXT,
    lido_em TEXT, versao INTEGER,
    aberta INTEGER                 -- 1: lida enquanto a OS estava aberta (é relida a cada registro e quando fechar)
);
CREATE TABLE IF NOT EXISTS os_abertas (   -- OS abertas e pendentes no último registro mensal (substituída a cada um)
    os TEXT PRIMARY KEY, tag TEXT, equipamento TEXT, setor TEXT, tipo TEXT, situacao TEXT, abertura TEXT,
    fechamento TEXT, id_effort TEXT, lido_em TEXT
);
CREATE TABLE IF NOT EXISTS os_atividades (
    os TEXT NOT NULL, tipo TEXT NOT NULL, id TEXT NOT NULL,
    data TEXT, descricao TEXT, fechada_em TEXT, causa TEXT,
    quantidade TEXT, valor_unitario TEXT, valor_total TEXT, lote TEXT,   -- peças e serviços com preço
    PRIMARY KEY (os, tipo, id)
);
"""
# colunas de cada cadastro fotografado; a chave é a primeira
COLUNAS_SNAPSHOT = {
    "equipamentos": ["tag", "descricao", "modelo", "fabricante", "setor", "numero_serie", "patrimonio", "inativo",
                     "situacao", "criticidade", "prioridade", "tem_plano", "tem_contrato"],
    "contratos": ["numero", "fornecedor", "descricao", "situacao", "vigencia_inicio", "vigencia_fim", "cobertura", "valor"],
    "fornecedores": ["codigo", "razao_social", "nome_fantasia", "telefone", "cnpj", "assistencia_tecnica", "ativo"],
    "fabricantes": ["codigo", "razao_social", "telefone", "ativo"],
    "modelos": ["codigo", "descricao", "modelo", "fabricante", "criticidade", "registro_anvisa"],
    "setores": ["id", "sigla", "nome", "grupo", "ativo"],
    "planos": ["id", "nome", "periodicidade", "procedimento", "tipo", "ativo", "n_equipamentos"],
    "procedimentos": ["id", "nome", "versao", "publicacao"],
    "motivos_pendencia": ["id", "descricao", "prazo", "ativo", "pendencia_inicial", "define_fluxo"],
    # vínculos (chave = "A | B"): equipamento x plano e contrato x equipamento
    "planos_equipamentos": ["chave", "tag", "plano", "periodicidade", "ultima_realizacao", "ultima_os_aberta",
                            "proxima_abertura", "responsavel"],
    "contratos_equipamentos": ["chave", "numero", "contrato", "fornecedor", "tag", "patrimonio", "descricao", "setor",
                               "valor"],
    "os_fechadas": ["os", "tag", "equipamento", "setor", "tipo", "situacao", "abertura", "fechamento", "id_effort"],
}
COLUNAS_OS_DETALHE = ["os", "responsavel", "prioridade", "complexidade", "abertura", "fechamento", "parada",
                      "funcionamento", "aberta_por", "custo_pecas", "custo_servicos_externos", "custo_mao_de_obra",
                      "custo_total"]
COLUNAS_OS_ATIVIDADES = ["os", "tipo", "id", "data", "descricao", "fechada_em", "causa",
                         "quantidade", "valor_unitario", "valor_total", "lote"]
# sobe quando o leitor da OS passa a guardar algo novo: as OS lidas com versão menor são lidas de novo
VERSAO_DETALHE_OS = 2
TRAVA_SNAPSHOT_MINUTOS = 240
COLUNAS_OS = ["os", "codigo", "equipamento", "modelo", "setor", "plano", "tipo",
              "situacao", "abertura", "ultima_pendencia", "responsavel", "pendencia_abertura", "pendencia_prazo"]
TRAVA_MINUTOS = 45  # depois disso considera travada


def agora() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


class Banco:
    def __init__(self, caminho: str | Path):
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        with self._con() as c:
            c.executescript(ESQUEMA)
            # banco antigo, sem as colunas novas
            existentes = {r[1] for r in c.execute("PRAGMA table_info(os)")}
            for col in ("pendencia_abertura", "pendencia_prazo"):
                if col not in existentes:
                    c.execute(f"ALTER TABLE os ADD COLUMN {col} TEXT")
            for tabela, novas in (("os_detalhe", ["versao", "aberta"]), ("os_atividades", COLUNAS_OS_ATIVIDADES)):
                existentes = {r[1] for r in c.execute(f"PRAGMA table_info({tabela})")}
                for col in novas:
                    if col not in existentes:
                        c.execute(f"ALTER TABLE {tabela} ADD COLUMN {col} {'INTEGER' if col in ('versao', 'aberta') else 'TEXT'}")
            for entidade, colunas in COLUNAS_SNAPSHOT.items():
                c.execute(f"CREATE TABLE IF NOT EXISTS hist_{entidade} (" + ", ".join(f"{col} TEXT" for col in colunas)
                          + f", desde TEXT, ate TEXT, PRIMARY KEY ({colunas[0]}))")
                existentes = {r[1] for r in c.execute(f"PRAGMA table_info(hist_{entidade})")}
                for col in colunas:
                    if col not in existentes:
                        c.execute(f"ALTER TABLE hist_{entidade} ADD COLUMN {col} TEXT")

    @contextmanager
    def _con(self):
        con = sqlite3.connect(self.caminho, timeout=30)
        try:
            yield con
            con.commit()
        finally:
            con.close()

    @staticmethod
    def _rodando(c, tabela: str, minutos: int) -> dict | None:
        limite = (dt.datetime.now() - dt.timedelta(minutes=minutos)).isoformat(timespec="seconds")
        c.execute(f"UPDATE {tabela} SET status='erro', erro='Interrompida', fim=? "
                  "WHERE status='rodando' AND inicio < ?", (agora(), limite))
        r = c.execute(f"SELECT id, inicio, origem FROM {tabela} WHERE status='rodando' ORDER BY id DESC LIMIT 1").fetchone()
        return {"id": r[0], "inicio": r[1], "origem": r[2], "tipo": tabela} if r else None

    def coleta_em_andamento(self) -> dict | None:
        with self._con() as c:
            return self._rodando(c, "coletas", TRAVA_MINUTOS)

    def snapshot_em_andamento(self) -> dict | None:
        with self._con() as c:
            return self._rodando(c, "snapshots", TRAVA_SNAPSHOT_MINUTOS)

    def effort_ocupado(self) -> dict | None:
        """Coleta de OS ou fotografia rodando: o Effort aceita uma sessão por usuário, então só uma por vez."""
        return self.coleta_em_andamento() or self.snapshot_em_andamento()

    def _iniciar(self, sql: str, params: tuple) -> int | None:
        # confere a trava e registra o início na mesma transação: duas coletas nunca começam juntas
        con = sqlite3.connect(self.caminho, timeout=30, isolation_level=None)
        try:
            con.execute("BEGIN IMMEDIATE")
            if self._rodando(con, "coletas", TRAVA_MINUTOS) or self._rodando(con, "snapshots", TRAVA_SNAPSHOT_MINUTOS):
                con.execute("COMMIT")
                return None
            novo = con.execute(sql, params).lastrowid
            con.execute("COMMIT")
            return novo
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    def iniciar_coleta(self, origem: str) -> int | None:
        # None se o Effort já estiver em uso por outra coleta ou fotografia
        return self._iniciar("INSERT INTO coletas (inicio, status, origem) VALUES (?, 'rodando', ?)", (agora(), origem))

    def concluir_coleta(self, coleta_id: int, dados: pd.DataFrame):
        dados = dados.reindex(columns=COLUNAS_OS).fillna("")
        dados.insert(0, "coleta_id", coleta_id)
        with self._con() as c:
            dados.to_sql("os", c, if_exists="append", index=False)
            c.execute("UPDATE coletas SET status='ok', fim=?, total=? WHERE id=?", (agora(), len(dados), coleta_id))

    def datar_coleta(self, coleta_id: int, quando: dt.datetime):
        # só pro demo
        iso = quando.isoformat(timespec="seconds")
        with self._con() as c:
            c.execute("UPDATE coletas SET inicio=?, fim=? WHERE id=?", (iso, iso, coleta_id))

    def falhar_coleta(self, coleta_id: int, erro: str):
        with self._con() as c:
            c.execute("UPDATE coletas SET status='erro', fim=?, erro=? WHERE id=?", (agora(), erro[:2000], coleta_id))

    def ultima_coleta(self, status: str | None = "ok") -> dict | None:
        sql = "SELECT id, inicio, fim, status, origem, total, erro FROM coletas"
        sql += " WHERE status=?" if status else ""
        sql += " ORDER BY id DESC LIMIT 1"
        with self._con() as c:
            r = c.execute(sql, (status,) if status else ()).fetchone()
        if not r:
            return None
        return dict(zip(["id", "inicio", "fim", "status", "origem", "total", "erro"], r))

    def os_da_coleta(self, coleta_id: int) -> pd.DataFrame:
        with self._con() as c:
            return pd.read_sql_query("SELECT * FROM os WHERE coleta_id=?", c, params=(coleta_id,)).drop(columns="coleta_id")

    def historico(self, dias: int = 120) -> pd.DataFrame:
        # última coleta ok de cada dia
        desde = (dt.datetime.now() - dt.timedelta(days=dias)).isoformat()
        with self._con() as c:
            df = pd.read_sql_query(
                "SELECT c.id, c.inicio, o.situacao, o.ultima_pendencia, o.responsavel, o.abertura "
                "FROM coletas c JOIN os o ON o.coleta_id = c.id WHERE c.status='ok' AND c.inicio >= ?",
                c, params=(desde,))
        if df.empty:
            return df
        df["dia"] = pd.to_datetime(df["inicio"]).dt.date
        ultimas = df.groupby("dia")["id"].max()
        return df[df["id"].isin(ultimas)]

    # ------------------------------------------------------------ fotografias mensais
    def iniciar_snapshot(self, referencia: str, origem: str) -> int | None:
        return self._iniciar("INSERT INTO snapshots (referencia, inicio, status, origem) VALUES (?, ?, 'rodando', ?)",
                             (referencia, agora(), origem))

    def aplicar_snapshot(self, snapshot_id: int, referencia: str, entidade: str, novo: pd.DataFrame):
        """Grava só a diferença: insere o que entrou, atualiza os campos que mudaram e marca (ate = referência)
        o que sumiu. O que não mudou não é tocado. Devolve as Mudancas."""
        from .diff import comparar
        colunas = COLUNAS_SNAPSHOT[entidade]
        chave = colunas[0]
        novo = novo.reindex(columns=colunas, fill_value="").astype(object).fillna("").astype(str)
        tabela = f"hist_{entidade}"
        with self._con() as c:
            todos = pd.read_sql_query(f"SELECT * FROM {tabela}", c)
            atuais = todos[todos["ate"].isna()].drop(columns=["desde", "ate"])
            removidos = set(todos.loc[todos["ate"].notna(), chave])
            m = comparar(atuais, novo, chave)
            log = []
            for _, l in m.entrou.iterrows():
                valores = [l.get(col, "") for col in colunas]
                if l[chave] in removidos:   # voltou a existir: reativa, mantendo o "desde" original
                    c.execute(f"UPDATE {tabela} SET " + ", ".join(f"{col}=?" for col in colunas[1:]) + f", ate=NULL WHERE {chave}=?",
                              valores[1:] + [l[chave]])
                    log.append((l[chave], "voltou", None, None, None))
                else:
                    c.execute(f"INSERT INTO {tabela} ({', '.join(colunas)}, desde) VALUES ({', '.join('?' * len(colunas))}, ?)",
                              valores + [referencia])
                    log.append((l[chave], "entrou", None, None, None))
            for _, l in m.mudou.iterrows():
                c.execute(f"UPDATE {tabela} SET {l['campo']}=? WHERE {chave}=?", (l["depois"], l["chave"]))
                log.append((l["chave"], "mudou", l["campo"], l["antes"], l["depois"]))
            for k in m.saiu[chave]:
                c.execute(f"UPDATE {tabela} SET ate=? WHERE {chave}=?", (referencia, k))
                log.append((k, "saiu", None, None, None))
            c.executemany("INSERT INTO hist_mudancas (snapshot_id, referencia, cadastro, chave, tipo, campo, antes, depois) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [(snapshot_id, referencia, entidade, *x) for x in log])
        return m

    def gravar_os_detalhe(self, detalhes: pd.DataFrame, atividades: pd.DataFrame, aberta: bool = False):
        """Detalhe das OS fechadas: gravado uma vez por OS (uma OS fechada não muda). OS abertas (aberta=True)
        são regravadas a cada registro e lidas de novo, de vez, quando fecharem."""
        with self._con() as c:
            if not detalhes.empty:
                d = detalhes.reindex(columns=COLUNAS_OS_DETALHE, fill_value="").astype(str)
                d["lido_em"] = agora()
                d["versao"] = VERSAO_DETALHE_OS
                d["aberta"] = int(aberta)
                c.executemany(f"INSERT OR REPLACE INTO os_detalhe ({', '.join(d.columns)}) VALUES ({', '.join('?' * len(d.columns))})",
                              d.itertuples(index=False))
            if not detalhes.empty:   # releitura: as atividades da OS são substituídas pelas novas
                c.executemany("DELETE FROM os_atividades WHERE os=?", [(o,) for o in detalhes["os"]])
            if not atividades.empty:
                a = atividades.reindex(columns=COLUNAS_OS_ATIVIDADES, fill_value="").astype(str)
                c.executemany(f"INSERT OR REPLACE INTO os_atividades ({', '.join(a.columns)}) VALUES ({', '.join('?' * len(a.columns))})",
                              a.itertuples(index=False))

    def os_com_detalhe(self) -> set[str]:
        with self._con() as c:
            return {r[0] for r in c.execute("SELECT os FROM os_detalhe WHERE versao >= ? AND COALESCE(aberta, 0) = 0",
                                            (VERSAO_DETALHE_OS,))}

    def gravar_os_abertas(self, df: pd.DataFrame):
        cols = COLUNAS_SNAPSHOT["os_fechadas"]
        with self._con() as c:
            c.execute("DELETE FROM os_abertas")
            d = df.reindex(columns=cols, fill_value="").astype(str).assign(lido_em=agora())
            c.executemany(f"INSERT OR REPLACE INTO os_abertas ({', '.join(d.columns)}) VALUES ({', '.join('?' * len(d.columns))})",
                          d.itertuples(index=False))

    def concluir_snapshot(self, snapshot_id: int, totais: dict):
        with self._con() as c:
            c.execute("UPDATE snapshots SET status='ok', fim=?, totais=? WHERE id=?", (agora(), json.dumps(totais), snapshot_id))

    def falhar_snapshot(self, snapshot_id: int, erro: str):
        with self._con() as c:
            c.execute("UPDATE snapshots SET status='erro', fim=?, erro=? WHERE id=?", (agora(), erro[:2000], snapshot_id))

    def limpar_antigos(self, manter: int = 2) -> set[str]:
        """Guarda só os `manter` últimos registros mensais concluídos. Apaga dos mais antigos: o registro,
        as mudanças, o que tinha saído do Effort naqueles meses e o detalhe de OS que não existe mais.
        Devolve as referências (AAAA-MM) mantidas, para apagar as pastas de arquivos dos outros meses."""
        with self._con() as c:
            ok = c.execute("SELECT id, referencia FROM snapshots WHERE status='ok' ORDER BY id DESC").fetchall()
            if len(ok) <= manter:
                return {r[1] for r in ok}
            corte = ok[manter - 1][0]
            mantidas = {r[1] for r in ok[:manter]}
            c.execute("DELETE FROM hist_mudancas WHERE snapshot_id < ?", (corte,))
            c.execute("DELETE FROM snapshots WHERE id < ? AND status != 'rodando'", (corte,))
            marcas = ", ".join("?" * len(mantidas))
            for entidade in COLUNAS_SNAPSHOT:
                c.execute(f"DELETE FROM hist_{entidade} WHERE ate IS NOT NULL AND ate NOT IN ({marcas})", tuple(mantidas))
            vivas = "SELECT os FROM hist_os_fechadas UNION SELECT os FROM os_abertas"
            c.execute(f"DELETE FROM os_atividades WHERE os NOT IN ({vivas})")
            c.execute(f"DELETE FROM os_detalhe WHERE os NOT IN ({vivas})")
        with sqlite3.connect(self.caminho, timeout=30) as c:   # devolve o espaço ao disco
            c.execute("VACUUM")
        return mantidas

    def snapshots(self) -> pd.DataFrame:
        with self._con() as c:
            return pd.read_sql_query("SELECT * FROM snapshots ORDER BY id DESC", c)

    def cadastro(self, entidade: str, incluir_removidos: bool = False) -> pd.DataFrame:
        with self._con() as c:
            df = pd.read_sql_query(f"SELECT * FROM hist_{entidade}", c)
        return df if incluir_removidos else df[df["ate"].isna()].drop(columns="ate")

    def mudancas(self, snapshot_id: int | None = None, referencia: str | None = None) -> pd.DataFrame:
        sql, p = "SELECT * FROM hist_mudancas", ()
        if snapshot_id is not None:
            sql, p = sql + " WHERE snapshot_id=?", (snapshot_id,)
        elif referencia is not None:
            sql, p = sql + " WHERE referencia=?", (referencia,)
        with self._con() as c:
            return pd.read_sql_query(sql + " ORDER BY cadastro, tipo, chave, campo", c, params=p)

    def tabela(self, nome: str) -> pd.DataFrame:
        assert nome in ("os_detalhe", "os_atividades", "os_abertas")
        with self._con() as c:
            return pd.read_sql_query(f"SELECT * FROM {nome}", c)
