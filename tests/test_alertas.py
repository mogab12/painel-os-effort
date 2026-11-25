import datetime as dt
from pathlib import Path

import pandas as pd

from painel.alertas import carregar, relatorio_contratos, relatorio_qualidade

RAIZ = Path(__file__).resolve().parents[1]
HOJE = dt.date(2026, 11, 24)
CFG = carregar(RAIZ / "alertas.yaml")


def contratos(*linhas):
    return pd.DataFrame(linhas, columns=["numero", "fornecedor", "descricao", "situacao", "vigencia_inicio",
                                         "vigencia_fim", "cobertura", "valor"]).assign(ate=None)


def equipamentos(*linhas):
    return pd.DataFrame(linhas, columns=["tag", "descricao", "setor", "inativo", "situacao", "criticidade",
                                         "prioridade", "tem_plano", "tem_contrato"]).assign(ate=None)


def test_relatorio_de_contratos():
    c = contratos(["1", "ALFA", "CALIBRAÇÃO", "VENCIDOS", "01/01/2025", "01/11/2026", "EQUIPAMENTOS", "100,00"],
                  ["2", "BETA", "X", "A VENCER", "01/01/2026", "10/12/2026", "EQUIPAMENTOS", "0,00"],
                  ["3", "GAMA", "Y", "ENCERRADOS", "01/01/2020", "01/01/2025", "EQUIPAMENTOS", "0,00"],
                  ["4", "DELTA", "Z", "VIGENTES", "01/01/2026", "01/01/2028", "EQUIPAMENTOS", "1.000,00"],
                  ["4 (2)", "DELTA", "Z", "VIGENTES", "01/01/2026", "01/01/2028", "EQUIPAMENTOS", "1.000,00"])
    e = equipamentos(["A-1", "BOMBA", "UTI", "NÃO", "COMODATO", "ALTA", "ALTA", "SIM", "NÃO"],
                     ["A-2", "MONITOR", "CC", "NÃO", "LOCADO", "ALTA", "ALTA", "SIM", "SIM"],
                     ["A-3", "FOCO", "CC", "SIM", "COMODATO", "", "", "NÃO", "NÃO"])   # inativo: não conta
    r = relatorio_contratos(c, e, CFG, HOJE)
    achados = set(zip(r["chave"], r["problema"]))
    assert ("1", "Vencido há 23 dias (01/11/2026)") in achados
    assert ("2", "Vence em 16 dias (10/12/2026): faixa de 30 dias") in achados and ("2", "Valor zerado") in achados
    assert not any(k == "3" for k, _ in achados)                   # encerrado: não alerta
    assert ("4", "Número repetido em 2 contratos") in achados
    assert list(r[r.problema.str.contains("sem contrato")]["chave"]) == ["A-1"]


def test_relatorio_de_qualidade():
    e = equipamentos(["A-1", "BOMBA", "UTI", "NÃO", "PROPRIO", "ALTA", "", "NÃO", "NÃO"],
                     ["A-2", "MONITOR", "CC", "NÃO", "PROPRIO", "BAIXA", "BAIXA", "NÃO", "NÃO"],
                     ["(sem TAG) série 1", "", "LAB", "NÃO", "", "", "", "", ""])
    planos = pd.DataFrame([["1", "PREVENTIVA X", "", "", "Sem periodicidade", "SIM"],
                           ["2", "PREVENTIVA Y", "1 Ano", "", "Interno", "SIM"],
                           ["3", "VELHO", "", "", "Inativo", "NÃO"]],
                          columns=["id", "nome", "periodicidade", "procedimento", "tipo", "ativo"]).assign(ate=None)
    setores = pd.DataFrame([["1", "UTI", "UTI ADULTO", "", "SIM"], ["2", "", "SEM SIGLA", "", "SIM"],
                            ["3", "CC", "CENTRO CIRURGICO.", "", "SIM"]],
                           columns=["id", "sigla", "nome", "grupo", "ativo"]).assign(ate=None)
    fornec = pd.DataFrame([["10", "ALFA COMÉRCIO LTDA", "", "", "", "Sim", "Sim"],
                           ["11", "BETA SA", "", "1133334444", "01.234.567/0001-89", "Sim", "Sim"],
                           ["12", "SEM CONTRATO", "", "", "", "Não", "Sim"]],
                          columns=["codigo", "razao_social", "nome_fantasia", "telefone", "cnpj", "assistencia_tecnica", "ativo"]).assign(ate=None)
    c = contratos(["1", "ALFA COMERCIO LTDA", "X", "VIGENTES", "", "", "", "1,00"], ["2", "BETA SA", "Y", "VIGENTES", "", "", "", "1,00"])
    r = relatorio_qualidade(e, planos, setores, fornec, c, CFG)
    achados = set(zip(r["chave"], r["problema"]))
    assert ("A-1", "Sem prioridade") in achados
    assert ("A-1", "Criticidade ALTA sem plano de manutenção") in achados
    assert not any(k == "A-2" for k, _ in achados)                 # baixa sem plano: tudo bem
    assert ("(sem TAG) série 1", "Equipamento sem TAG") in achados
    assert ("1", "Plano ativo sem periodicidade") in achados and ("3", "Plano ativo sem periodicidade") not in achados
    assert ("2", "Setor sem sigla") in achados and ("3", "Nome do setor termina com ponto") in achados
    assert ("10", "Fornecedor com contrato sem CNPJ e sem telefone") in achados   # cruzado pelo nome, sem acento
    assert not any(k in ("11", "12") for k, _ in achados)


def test_contrato_vigente_sem_equipamentos():
    import datetime as dt
    import pandas as pd
    from painel.alertas import PADRAO, relatorio_contratos
    contratos = pd.DataFrame([
        {"numero": "A", "fornecedor": "F", "descricao": "D", "situacao": "VIGENTES", "vigencia_fim": "01/01/2028", "valor": "10,00"},
        {"numero": "B", "fornecedor": "F", "descricao": "D", "situacao": "VIGENTES", "vigencia_fim": "01/01/2028", "valor": "10,00"},
        {"numero": "C", "fornecedor": "F", "descricao": "D", "situacao": "ENCERRADOS", "vigencia_fim": "01/01/2020", "valor": "10,00"}])
    equip = pd.DataFrame(columns=["tag", "descricao", "setor", "situacao", "inativo", "tem_contrato"])
    vinc = pd.DataFrame([{"numero": "A", "tag": "X-1"}])
    r = relatorio_contratos(contratos, equip, PADRAO, dt.date(2026, 11, 25), vinc)
    assert list(r.loc[r["problema"].str.contains("sem equipamentos"), "chave"]) == ["B"]
    assert not relatorio_contratos(contratos, equip, PADRAO, dt.date(2026, 11, 25))["problema"].str.contains("sem equip").any()
