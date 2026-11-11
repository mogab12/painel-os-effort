from __future__ import annotations

import datetime as dt
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
"""
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

    @contextmanager
    def _con(self):
        con = sqlite3.connect(self.caminho, timeout=30)
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def coleta_em_andamento(self) -> dict | None:
        limite = (dt.datetime.now() - dt.timedelta(minutes=TRAVA_MINUTOS)).isoformat(timespec="seconds")
        with self._con() as c:
            c.execute("UPDATE coletas SET status='erro', erro='Coleta interrompida', fim=? "
                      "WHERE status='rodando' AND inicio < ?", (agora(), limite))
            r = c.execute("SELECT id, inicio, origem FROM coletas WHERE status='rodando' "
                          "ORDER BY id DESC LIMIT 1").fetchone()
        return {"id": r[0], "inicio": r[1], "origem": r[2]} if r else None

    def iniciar_coleta(self, origem: str) -> int | None:
        # None se já tiver outra rodando
        if self.coleta_em_andamento():
            return None
        with self._con() as c:
            cur = c.execute("INSERT INTO coletas (inicio, status, origem) VALUES (?, 'rodando', ?)", (agora(), origem))
            return cur.lastrowid

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
