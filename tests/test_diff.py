import pandas as pd

from painel.diff import comparar, gravar_log


def cad(*linhas):
    return pd.DataFrame(linhas, columns=["tag", "descricao", "setor", "criticidade"])


ANTES = cad(["A-1", "BOMBA", "UTI", "ALTA"], ["A-2", "MONITOR", "CC", "MEDIA"], ["A-3", "INCUBADORA", "NEO", None])
DEPOIS = cad(["A-1", "BOMBA", "UTI PED", "BAIXA"], ["A-3", "INCUBADORA", "NEO", ""], ["A-4", "FOCO", "CC", "BAIXA"])


def test_entrou_saiu_mudou():
    m = comparar(ANTES, DEPOIS, "tag")
    assert list(m.entrou["tag"]) == ["A-4"]
    assert list(m.saiu["tag"]) == ["A-2"]
    assert m.mudou.to_dict("records") == [
        {"chave": "A-1", "campo": "criticidade", "antes": "ALTA", "depois": "BAIXA"},
        {"chave": "A-1", "campo": "setor", "antes": "UTI", "depois": "UTI PED"},
    ]
    assert m.resumo() == {"entrou": 1, "saiu": 1, "mudou": 1}


def test_vazio_nan_e_espacos_nao_contam_como_mudanca():
    m = comparar(cad(["A-1", "BOMBA ", "UTI", None]), cad(["A-1", "BOMBA", " UTI", ""]), "tag")
    assert m.vazio


def test_coluna_nova_nao_gera_mudanca_em_tudo():
    antes = cad(["A-1", "BOMBA", "UTI", "ALTA"]).drop(columns="criticidade")
    assert comparar(antes, cad(["A-1", "BOMBA", "UTI", "ALTA"]), "tag").vazio


def test_grava_planilha_de_mudancas(tmp_path):
    log = pd.DataFrame([
        {"cadastro": "equipamentos", "chave": "A-1", "tipo": "mudou", "campo": "setor", "antes": "UTI", "depois": "UTI PED"},
        {"cadastro": "equipamentos", "chave": "A-1", "tipo": "mudou", "campo": "criticidade", "antes": "ALTA", "depois": "BAIXA"},
        {"cadastro": "equipamentos", "chave": "A-4", "tipo": "entrou", "campo": None, "antes": None, "depois": None},
        {"cadastro": "contratos", "chave": "C-1", "tipo": "saiu", "campo": None, "antes": None, "depois": None},
    ])
    arq = tmp_path / "2026-11" / "mudancas.xlsx"
    gravar_log(arq, log, "2026-11")
    abas = pd.read_excel(arq, sheet_name=None)
    assert set(abas) == {"Resumo", "equipamentos", "contratos"}
    r = abas["Resumo"].set_index("cadastro")
    assert (r.loc["equipamentos", "entrou"], r.loc["equipamentos", "mudou"], r.loc["contratos", "saiu"]) == (1, 1, 1)
    assert len(abas["equipamentos"]) == 3
