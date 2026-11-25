import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from painel.coletor import ErroColeta
from painel.snapshot import ler_export_equipamentos, montar_equipamentos, referencia

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_referencia_e_o_mes_de_ontem():
    assert referencia(dt.date(2026, 12, 1)) == "2026-11"   # rodada agendada do dia 1º
    assert referencia(dt.date(2026, 11, 24)) == "2026-11"  # rodada manual no meio do mês
    assert referencia(dt.date(2027, 1, 1)) == "2026-12"


def test_le_exportacao_de_equipamentos():
    df = ler_export_equipamentos(FIXTURES / "equipamentos_export.xlsx")
    assert list(df.columns) == ["tag", "descricao", "modelo", "fabricante", "setor", "numero_serie", "patrimonio", "inativo"]
    assert len(df) == 4
    l = df.set_index("tag").loc["MONI-0001"]
    assert l.descricao == "MONITOR MULTIPARAMETRICO"          # sem espaço sobrando
    assert l.patrimonio == "" and l.numero_serie == "SN-0002"  # vazio vira "", não NaN


def test_exportacao_com_outro_formato_falha_com_mensagem(tmp_path):
    arq = tmp_path / "x.xlsx"
    pd.DataFrame({"TAG": ["A"], "Descrição": ["B"]}).to_excel(arq, index=False)
    with pytest.raises(ErroColeta, match="mudou de formato"):
        ler_export_equipamentos(arq)


def base():
    return ler_export_equipamentos(FIXTURES / "equipamentos_export.xlsx")


def test_atributos_vem_dos_filtros():
    df = montar_equipamentos(base(), {
        "criticidade": {"ALTA": {"BINF-0001", "MONI-0001"}, "MEDIA": {"INCU-0001"}, "BAIXA": set()},
        "tem_plano": {"SIM": {"MONI-0001"}},
        "situacao": {"PROPRIO": {"BINF-0001", "MONI-0001", "INCU-0001"}, "COMODATO": {"BINF-0002"}},
    }).set_index("tag")
    assert df.loc["BINF-0001", "criticidade"] == "ALTA" and df.loc["INCU-0001", "criticidade"] == "MEDIA"
    assert df.loc["BINF-0002", "criticidade"] == ""           # em nenhum filtro: sem criticidade
    assert df.loc["MONI-0001", "tem_plano"] == "SIM" and df.loc["BINF-0001", "tem_plano"] == "NÃO"
    assert df.loc["BINF-0002", "situacao"] == "COMODATO"
    assert df.loc["BINF-0001", "tem_contrato"] == ""           # atributo não coletado fica em branco


def test_filtro_ignorado_pelo_effort_deixa_o_atributo_em_branco():
    # cada situação devolveu todos os equipamentos: o filtro não teve efeito
    todos = set(base()["tag"])
    df = montar_equipamentos(base(), {"situacao": {"PROPRIO": todos, "COMODATO": todos}})
    assert (df["situacao"] == "").all()


def test_equipamento_em_dois_valores_fica_com_o_primeiro():
    df = montar_equipamentos(base(), {"prioridade": {"ALTA": {"BINF-0001"}, "BAIXA": {"BINF-0001"}}}).set_index("tag")
    assert df.loc["BINF-0001", "prioridade"] == "ALTA"


def test_equipamento_sem_tag_ou_repetido_ganha_chave_unica():
    from painel.snapshot import chave_unica
    df = pd.DataFrame({"tag": ["", "", "A-1", "A-1", ""], "numero_serie": ["1129", "695", "", "", ""],
                       "setor": ["LAB", "LAB", "UTI", "UTI", "CC"]})
    assert list(chave_unica(df)["tag"]) == ["(sem TAG) série 1129", "(sem TAG) série 695", "A-1", "A-1 (2)", "(sem TAG) CC"]


def test_sem_tag_nao_herda_atributos():
    b = base()
    b.loc[0, "tag"] = ""
    df = montar_equipamentos(b, {"criticidade": {"ALTA": {"", "MONI-0001"}}, "tem_plano": {"SIM": {""}}})
    assert df.loc[0, "criticidade"] == "" and df.loc[0, "tem_plano"] == ""


def test_le_relatorio_de_contratos():
    from painel.snapshot import ler_export_contratos, repetidos_com_sufixo
    df = ler_export_contratos(FIXTURES / "contratos_export.xlsx")
    assert len(df) == 4
    c = df.iloc[0]
    assert c.fornecedor == "EMPRESA ALFA COMÉRCIO E CALIBRAÇÃO DE EQUIPAMENTOS"   # quebra de diagramação juntada
    assert c.descricao == "CALIBRAÇÃO EQUIPAMENTOS DE FISIOTERAPIA"
    assert (c.vigencia_inicio, c.vigencia_fim, c.situacao) == ("26/01/2025", "26/01/2026", "VENCIDOS")
    assert df.iloc[1].numero == "154.00006536/2025-60" and df.iloc[1].valor == "1234,56"   # "154. \n0000..." também
    assert list(repetidos_com_sufixo(df, "numero")["numero"])[2:] == ["21.1.1922.62.1", "21.1.1922.62.1 (2)"]


def test_le_fornecedores():
    from painel.snapshot import COLUNAS_EXPORT_FORNECEDOR, ler_export_simples
    df = ler_export_simples(FIXTURES / "fornecedores_export.xlsx", COLUNAS_EXPORT_FORNECEDOR, "fornecedores")
    assert df.iloc[0].to_dict() == {"codigo": "001", "razao_social": "EMPRESA ALFA LTDA", "nome_fantasia": "ALFA",
                                    "telefone": "1133334444", "cnpj": "01.234.567/0001-89",
                                    "assistencia_tecnica": "Sim", "ativo": "Sim"}
    assert df.iloc[1].cnpj == ""


def test_le_os_em_modo_consulta():
    from painel.snapshot import ler_os_atividades, ler_os_detalhe
    html = (FIXTURES / "os_consulta.html").read_text()
    d = ler_os_detalhe(html)
    assert (d["os"], d["responsavel"], d["abertura"], d["fechamento"]) == ("202400002", "TECNICO A", "28/05/2024 15:03", "05/05/2025 11:18")
    assert d["custo_total"] == "1.234,50" and d["parada"] == ""
    a = ler_os_atividades(html, "202400002")
    assert [(x["tipo"], x["id"]) for x in a] == [("ocorrenciaservico", "3"), ("pendenciaconcluida", "15"),
                                                 ("ocorrenciasolucionada", "3"), ("peca", "87"), ("servicoexterno", "73")]
    assert a[0]["causa"] == "FALHÁ ELÉTRICA"                    # só a causa, sem o texto livre
    assert a[1]["descricao"] == "(MEH) AGUARDANDO APROVAÇÃO DE ORÇAMENTO" and a[1]["fechada_em"] == "13/06/2024 07:44"
    assert all("texto livre" not in str(v) for x in a for v in x.values())


def test_os_fechadas_da_grade():
    from painel.snapshot import montar_os_fechadas
    df = montar_os_fechadas([["", "202400048", "VEN-0025", "VENTILADOR - SAVINA (DRAGER)\nPRONTO SOCORRO ADULTO",
                              "CORRETIVA", "F", "03/05/2024", "13/03/2025", "48"]])
    assert df.iloc[0].to_dict() == {"os": "202400048", "tag": "VEN-0025", "equipamento": "VENTILADOR - SAVINA (DRAGER)",
                                    "setor": "PRONTO SOCORRO ADULTO", "tipo": "CORRETIVA", "situacao": "F",
                                    "abertura": "03/05/2024", "fechamento": "13/03/2025", "id_effort": "48"}


def test_pecas_e_servicos_com_preco():
    from painel.snapshot import ler_os_atividades
    a = {x["id"]: x for x in ler_os_atividades((FIXTURES / "os_consulta.html").read_text(), "202400002")}
    assert (a["87"]["tipo"], a["87"]["quantidade"], a["87"]["valor_unitario"], a["87"]["valor_total"], a["87"]["lote"]) == \
        ("peca", "1 UN", "361,00", "361,00", "N/A")
    assert (a["73"]["tipo"], a["73"]["descricao"], a["73"]["valor_total"]) == ("servicoexterno", "EMPRESA ALFA", "1.200,00")
    assert a["15"]["valor_total"] == ""      # pendência não tem preço


def test_arvore_de_setores():
    import json
    from painel.snapshot import ler_arvore_setores
    df = ler_arvore_setores(json.loads((FIXTURES / "arvore_setores.json").read_text())).set_index("id")
    assert list(df.index) == ["116", "8", "141", "9"]                 # sem raiz e sem pasta de grupo
    assert (df.loc["8", "sigla"], df.loc["8", "nome"], df.loc["8", "grupo"]) == ("500AC", "QUARTO 500 - ALOJAMENTO CONJUNTO", "ALOJAMENTO CONJUNTO.")
    assert df.loc["116", "grupo"] == "" and df.loc["141", "ativo"] == "NÃO"
    assert (df.loc["9", "sigla"], df.loc["9", "nome"]) == ("", "SETOR SEM SIGLA")


def test_arvore_de_planos():
    import json
    from painel.snapshot import ler_arvore_planos
    df = ler_arvore_planos(json.loads((FIXTURES / "arvore_planos.json").read_text())).set_index("id")
    assert df.loc["106"].to_dict() == {"nome": "BALANÇAS - CALIBRAÇÃO", "periodicidade": "1 Ano",
                                       "procedimento": "45 - CALIBRACAO", "tipo": "Externo", "ativo": "SIM",
                                       "n_equipamentos": ""}
    assert (df.loc["77", "periodicidade"], df.loc["77", "tipo"]) == ("", "Sem periodicidade")
    assert df.loc["58", "ativo"] == "NÃO"


def test_relatorio_de_modelos():
    from painel.snapshot import ler_export_modelos
    df = ler_export_modelos(FIXTURES / "modelos_export.xlsx")
    assert list(df["codigo"]) == ["3932", "4390"]
    assert df.iloc[1].to_dict() == {"codigo": "4390", "descricao": "ALTO FLUXO", "modelo": "PRECISION FLOW",
                                    "fabricante": "FABRICANTE B", "criticidade": "ALTA", "registro_anvisa": "80000000001"}


def test_procedimentos_da_arvore():
    from painel.snapshot import ler_arvore_procedimentos
    df = ler_arvore_procedimentos([{"id": "root_tree1", "texto": "", "icone": "ico_transparente.png"},
                                   {"id": "45", "texto": "45 - CALIBRACAO v1.0 (PUBLICAÇÃO: 05/03/2026)", "icone": "x"},
                                   {"id": "9", "texto": "9 - RASCUNHO SEM PUBLICAR", "icone": "x"}])
    assert df.to_dict("records") == [{"id": "45", "nome": "CALIBRACAO", "versao": "1.0", "publicacao": "05/03/2026"},
                                     {"id": "9", "nome": "RASCUNHO SEM PUBLICAR", "versao": "", "publicacao": ""}]


def test_motivos_de_pendencia_da_api():
    from painel.snapshot import ler_motivos_pendencia
    df = ler_motivos_pendencia({"summary": 1, "Items": [
        {"Id": 43, "Description": "(MEH) AGUARDANDO ADIANTAMENTO ", "Active": True, "AttendanceTerm": 1,
         "UnityTerm": "Dia(s)", "InitialPendency": True, "DefineFlow": False}]})
    assert df.iloc[0].to_dict() == {"id": "43", "descricao": "(MEH) AGUARDANDO ADIANTAMENTO", "prazo": "1 Dia(s)",
                                    "ativo": "SIM", "pendencia_inicial": "SIM", "define_fluxo": "NÃO"}
    with pytest.raises(ErroColeta):
        ler_motivos_pendencia({"summary": 2, "Items": []})


def test_relatorios_de_vinculos(tmp_path):
    from painel.snapshot import ler_relatorio_contratos_equipamentos, ler_relatorio_planos_equipamentos
    arq = tmp_path / "p.xlsx"
    pd.DataFrame([["ALFLU-0014", "ALTO FLUXO", "MULTMED", "110215", "2147", "UTI ADULTO", "MULTMED",
                   "ALTO FLUXO MULTIMED PREVENTIVA", "15/09/2025", "202602182", "15/09/2026", "1 ano", "TECNICO A"]],
                 columns=["TAG", "Equipamento", "Modelo", "Nº de Série", "Patrimônio", "Setor", "Fabricante",
                          "Planos de Manutenção", "Últ. Realização", "Última OS Aberta", "Próx. Data de Abertura",
                          "Periodicidade", "Responsável"]).to_excel(arq, index=False)
    p = ler_relatorio_planos_equipamentos(arq).iloc[0]
    assert (p.chave, p.ultima_os_aberta, p.proxima_abertura) == ("ALFLU-0014 | ALTO FLUXO MULTIMED PREVENTIVA", "202602182", "15/09/2026")
    arq = tmp_path / "c.xlsx"
    cab = ["Empresa", "Contrato", "Tipo de Contrato", "Nº Contrato", "Fornecedor", "Data Inicial", "Data Final",
           "Método de Custo", "Valor Total", "TAG", "Patrimônio", "Nº de Série", "Descrição", "Setor", "Valor"]
    pd.DataFrame([["MEH", "ULTRASSOM", "PREV", "23.1.582.62.4", "GE", "01/05/2023", "01/05/2028", "PARCELADO",
                   "110.059,20", "US-0004", "200038175", "LP9", "ULTRASSOM - LOGIQ P9 (GE)", "RADIOLOGIA", "1.768,26"],
                  ["MEH", "FANEM", "PREV", "21.1.2195.62.6", "FANEM", "31/03/2022", "31/03/2027", "PARCELADO",
                   "0,00", None, None, None, None, None, "0,00"]], columns=cab).to_excel(arq, index=False)
    c = ler_relatorio_contratos_equipamentos(arq)
    assert list(c["chave"]) == ["23.1.582.62.4 | US-0004"] and c.iloc[0].valor == "1.768,26"   # contrato sem TAG fica de fora
