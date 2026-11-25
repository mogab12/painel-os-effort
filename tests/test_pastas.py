import zipfile

import pandas as pd

from painel.banco import COLUNAS_SNAPSHOT, Banco
from painel.pastas import gerar_zip, nome_arquivo


def montar(tmp_path):
    b = Banco(tmp_path / "t.db")
    s = b.iniciar_snapshot("2026-11", "manual")
    b.aplicar_snapshot(s, "2026-11", "equipamentos", pd.DataFrame(
        [["GASTR-0009", "GASTROSCOPIO", "EG-590WR", "FUJIFILM", "ENDOSCOPIA", "8G2", "62019661", "NÃO"],
         ["MONI-0001", "MONITOR", "M1", "X", "UTI", "", "", "NÃO"]],
        columns=COLUNAS_SNAPSHOT["equipamentos"][:8]))
    b.aplicar_snapshot(s, "2026-11", "os_fechadas", pd.DataFrame(
        [["202500535", "GASTR-0009", "GASTROSCOPIO", "ENDOSCOPIA", "CORRETIVA", "F", "01/04/2025", "17/09/2025", "1152"]],
        columns=COLUNAS_SNAPSHOT["os_fechadas"]))
    b.concluir_snapshot(s, {})
    det = pd.DataFrame([{"os": "202500535", "responsavel": "TECNICO A", "abertura": "01/04/2025 11:19",
                         "fechamento": "17/09/2025 10:51", "custo_pecas": "523,00", "custo_total": "523,00"}])
    ativ = pd.DataFrame([
        {"os": "202500535", "tipo": "pendenciaconcluida", "id": "2", "data": "25/07/2025 11:40",
         "descricao": "(MEH) AGUARDANDO EMPENHO", "fechada_em": "31/07/2025 08:33"},
        {"os": "202500535", "tipo": "pendenciaconcluida", "id": "1", "data": "01/04/2025 11:19",
         "descricao": "(MEH) AGUARDANDO PEÇA", "fechada_em": "25/07/2025 11:39"},
        {"os": "202500535", "tipo": "peca", "id": "9", "data": "30/07/2025", "descricao": "376 - BORRACHA <B>",
         "quantidade": "1 UN", "valor_unitario": "523,00", "valor_total": "523,00"},
    ])
    b.gravar_os_detalhe(det, ativ)
    b.gravar_os_abertas(pd.DataFrame([["202601076", "GASTR-0009", "GASTROSCOPIO", "ENDOSCOPIA", "CORRETIVA", "P",
                                       "27/04/2026", "", "1500"]], columns=COLUNAS_SNAPSHOT["os_fechadas"]))
    return b


def test_zip_tem_pasta_por_equipamento_os_e_pendencia(tmp_path):
    b = montar(tmp_path)
    cont = gerar_zip(b, tmp_path / "h.zip", "2026-11")
    assert cont == {"equipamentos": 2, "os": 2, "pendencias": 2}
    nomes = zipfile.ZipFile(tmp_path / "h.zip").namelist()
    eq = "historico_2026-11/GASTR-0009 - GASTROSCOPIO - EG-590WR - PAT 62019661"
    assert f"{eq}/equipamento.html" in nomes
    assert f"{eq}/2026-04-27 OS 202601076 CORRETIVA (pendente)/OS.html" in nomes
    os_ = f"{eq}/2025-04-01 OS 202500535 CORRETIVA (fechada)"
    # pendências na ordem em que foram abertas, sem o prefixo (MEH) e sem acento no nome do arquivo
    assert sorted(n for n in nomes if n.startswith(os_ + "/pendencias/")) == [
        f"{os_}/pendencias/01 - AGUARDANDO PECA (115 dias).html",
        f"{os_}/pendencias/02 - AGUARDANDO EMPENHO (6 dias).html"]
    pagina = zipfile.ZipFile(tmp_path / "h.zip").read(f"{os_}/OS.html").decode()
    assert "TECNICO A" in pagina and "376 - BORRACHA &lt;B&gt;" in pagina and "R$ 523,00" in pagina
    assert "historico_2026-11/indice.html" in nomes and "historico_2026-11/MONI-0001 - MONITOR - M1/equipamento.html" in nomes


def test_os_lida_aberta_e_relida_quando_fechar(tmp_path):
    b = Banco(tmp_path / "t.db")
    b.gravar_os_detalhe(pd.DataFrame([{"os": "1"}]), pd.DataFrame(), aberta=True)
    b.gravar_os_detalhe(pd.DataFrame([{"os": "2"}]), pd.DataFrame())
    assert b.os_com_detalhe() == {"2"}      # a OS 1 foi lida aberta: entra de novo quando fechar


def test_nome_de_arquivo_seguro():
    assert nome_arquivo('BOMBA: "A/B" ÇÃO', 60) == "BOMBA A B CAO"
    assert len(nome_arquivo("X" * 200, 70)) == 70
