import pandas as pd

from painel.banco import Banco


def test_coleta_e_fotografia_nunca_rodam_juntas(tmp_path):
    b = Banco(tmp_path / "t.db")
    snap = b.iniciar_snapshot("2026-11", "manual")
    assert snap and b.iniciar_coleta("manual") is None          # Effort ocupado pela fotografia
    b.falhar_snapshot(snap, "teste")
    coleta = b.iniciar_coleta("manual")
    assert coleta and b.iniciar_snapshot("2026-11", "manual") is None  # e vice-versa
    assert b.effort_ocupado()["tipo"] == "coletas"
    assert b.ultima_coleta("ok") is None   # fotografia nunca aparece como coleta de OS


def eq(*linhas):
    return pd.DataFrame(linhas, columns=["tag", "setor", "criticidade"])


def fotografar(b, ref, df):
    s = b.iniciar_snapshot(ref, "manual")
    m = b.aplicar_snapshot(s, ref, "equipamentos", df)
    b.concluir_snapshot(s, {})
    return m


def test_gravacao_incremental(tmp_path):
    b = Banco(tmp_path / "t.db")
    fotografar(b, "2026-10", eq(["A-1", "UTI", "ALTA"], ["A-2", "CC", "BAIXA"], ["A-3", "NEO", "MEDIA"]))
    # nov: A-1 mudou de setor, A-2 sumiu, A-4 entrou, A-3 igual
    m = fotografar(b, "2026-11", eq(["A-1", "UTI PED", "ALTA"], ["A-3", "NEO", "MEDIA"], ["A-4", "CC", "BAIXA"]))
    assert m.resumo() == {"entrou": 1, "saiu": 1, "mudou": 1}
    todos = b.cadastro("equipamentos", incluir_removidos=True).set_index("tag")
    assert todos.loc["A-1", "setor"] == "UTI PED" and todos.loc["A-1", "desde"] == "2026-10"   # atualizado no lugar
    assert todos.loc["A-2", "ate"] == "2026-11"            # sumiu do Effort: marcado, não apagado
    assert todos.loc["A-3", "desde"] == "2026-10"          # sem mudança: intocado
    assert todos.loc["A-4", "desde"] == "2026-11"
    assert list(b.cadastro("equipamentos")["tag"]) == ["A-1", "A-3", "A-4"]
    log = b.mudancas(referencia="2026-11")
    assert sorted(log["tipo"]) == ["entrou", "mudou", "saiu"]
    assert log[log.tipo == "mudou"][["chave", "campo", "antes", "depois"]].values.tolist() == [["A-1", "setor", "UTI", "UTI PED"]]
    # dez: A-2 volta, nada mais muda
    m = fotografar(b, "2026-12", eq(["A-1", "UTI PED", "ALTA"], ["A-2", "CC", "BAIXA"], ["A-3", "NEO", "MEDIA"], ["A-4", "CC", "BAIXA"]))
    l = b.cadastro("equipamentos").set_index("tag").loc["A-2"]
    assert l.desde == "2026-10" and b.mudancas(referencia="2026-12")["tipo"].tolist() == ["voltou"]
    # jan: nada mudou, nada é escrito
    assert fotografar(b, "2027-01", b.cadastro("equipamentos").drop(columns="desde")).vazio
    assert b.mudancas(referencia="2027-01").empty


def test_detalhe_de_os_gravado_uma_vez(tmp_path):
    b = Banco(tmp_path / "t.db")
    b.gravar_os_detalhe(pd.DataFrame([{"os": "202400002", "responsavel": "TECNICO A"}]),
                        pd.DataFrame([{"os": "202400002", "tipo": "pendenciaconcluida", "id": "15", "descricao": "X"}]))
    assert b.os_com_detalhe() == {"202400002"}
    assert len(b.tabela("os_atividades")) == 1


def test_guarda_so_os_dois_ultimos_registros(tmp_path):
    b = Banco(tmp_path / "t.db")
    fotografar(b, "2026-09", eq(["A-1", "UTI", "ALTA"], ["A-2", "CC", "BAIXA"]))
    fotografar(b, "2026-10", eq(["A-1", "UTI", "ALTA"]))                     # A-2 saiu em 2026-10
    fotografar(b, "2026-11", eq(["A-1", "UTI", "ALTA"], ["A-3", "CC", "MEDIA"]))
    assert b.limpar_antigos(2) == {"2026-10", "2026-11"}
    assert list(b.snapshots()["referencia"]) == ["2026-11", "2026-10"]
    assert set(b.mudancas()["referencia"]) == {"2026-10", "2026-11"}         # o log de 2026-09 foi apagado
    assert set(b.cadastro("equipamentos", incluir_removidos=True)["tag"]) == {"A-1", "A-2", "A-3"}
    fotografar(b, "2026-12", eq(["A-1", "UTI", "ALTA"], ["A-3", "CC", "MEDIA"]))
    b.limpar_antigos(2)
    # A-2 saiu num mês que não é mais guardado: some de vez
    assert set(b.cadastro("equipamentos", incluir_removidos=True)["tag"]) == {"A-1", "A-3"}
