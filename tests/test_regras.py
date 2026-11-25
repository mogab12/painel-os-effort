import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from painel.regras import Regras, classificar, normalizar, resumo

RAIZ = Path(__file__).resolve().parents[1]
HOJE = dt.date(2026, 10, 21)


@pytest.fixture(scope="module")
def regras():
    return Regras.carregar(RAIZ / "regras.yaml")


def os_(numero, pendencia="", situacao="P", dias=10, codigo=None, tipo="CORRETIVA", resp="Fulano"):
    return {
        "os": str(numero), "codigo": codigo or f"EQ-{numero}", "equipamento": "INCUBADORA",
        "setor": "UNIDADE NEONATAL", "tipo": tipo, "situacao": situacao,
        "abertura": (HOJE - dt.timedelta(days=dias)).strftime("%d/%m/%Y"),
        "ultima_pendencia": pendencia, "responsavel": resp,
    }


def linha(regras, **kw):
    return classificar(pd.DataFrame([os_(1, **kw)]), regras, HOJE).iloc[0]


def test_normalizar_tira_prefixo_e_acentos():
    assert normalizar("(MEH) AGUARDANDO PEÇA") == "AGUARDANDO PECA"
    assert normalizar("  solicitaçao   de transporte ") == "SOLICITACAO DE TRANSPORTE"


def test_fechar_os_e_rapida(regras):
    l = linha(regras, pendencia="(MEH) FECHAR OS")
    assert (l.depende_de, l.facilidade, l.ordem_facilidade) == ("MEH", "Rápida", 1)


def test_pendente_sem_motivo(regras):
    l = linha(regras, pendencia="", situacao="P")
    assert l.acao_sugerida == "Registrar o motivo da pendência" and l.facilidade == "Rápida"


def test_aberta_sem_pendencia_e_triagem(regras):
    l = linha(regras, pendencia="", situacao="A")
    assert l.facilidade == "Trabalho da MEH" and l.acao_sugerida.startswith("Triar")


def test_pedido_de_compra_aberto(regras):
    l = linha(regras, pendencia="", situacao="A", tipo="PEDIDO DE COMPRA")
    assert l.acao_sugerida == "Dar andamento ao pedido de compra"


def classifica_um(regras, d):
    return classificar(pd.DataFrame([d]), regras, HOJE).iloc[0]


def test_certificado_vira_rapido_quando_o_prazo_expira(regras):
    no_prazo = classifica_um(regras, com_prazo(1, "(MEH) AGUARDANDO CERTIFICADO", 0))
    assert no_prazo.facilidade == "Depende de outros" and no_prazo.acao_sugerida == "Aguardar certificado"
    expirada = classifica_um(regras, com_prazo(1, "(MEH) AGUARDANDO CERTIFICADO", -1))
    assert expirada.facilidade == "Rápida" and expirada.acao_sugerida.startswith("Cobrar certificado")


def test_orcamento_nao_confunde_com_aprovacao(regras):
    r1, _ = regras.regra_pendencia("(MEH) AGUARDANDO APROVAÇÃO DE ORÇAMENTO")
    r2, _ = regras.regra_pendencia("(MEH) AGUARDANDO ORÇAMENTO")
    assert "APROVACAO" in r1["contem"] and r2["contem"] == "AGUARDANDO ORCAMENTO"


def test_terceiros_escala_depois_de_30_dias_expirada(regras):
    pend = "(MEH) AGUARDANDO EXECUÇÃO DO SERVIÇO TERCEIROS"
    assert classifica_um(regras, com_prazo(1, pend, -30)).acao_sugerida == "Acompanhar"
    l = classifica_um(regras, com_prazo(1, pend, -31))
    assert l.depende_de == "Fornecedor / terceiros" and l.acao_sugerida.startswith("Cobrar retorno e escalar")
    # OS muito antiga, mas sem prazo expirado: não escala
    assert classifica_um(regras, com_prazo(1, pend, 10, pend_ha_dias=300, dias=400)).acao_sugerida == "Acompanhar"


def test_zmanutencao_reconhecida(regras):
    l = linha(regras, pendencia="(ZMANUTENÇÃO) AGUARDANDO EXECUÇÃO DO SERVIÇO TERCEIROS")
    assert l.depende_de == "Fornecedor / terceiros" and not l.regra_desconhecida


def test_pendencia_nova_fica_marcada(regras):
    l = linha(regras, pendencia="(MEH) AGUARDANDO ALGO QUE NAO EXISTE")
    assert l.regra_desconhecida and l.depende_de == "Não classificado"


def test_duplicidade_ignora_codigos_genericos(regras):
    df = classificar(pd.DataFrame([
        os_(1, codigo="INCU-0013"), os_(2, codigo="INCU-0013"),
        os_(3, codigo="TERM-0001"), os_(4, codigo="TERM-0001"),
    ]), regras, HOJE).set_index("os")
    assert df.loc["1", "duplicada"] and df.loc["2", "duplicada"]
    assert not df.loc["3", "duplicada"] and not df.loc["4", "duplicada"]
    assert "conferir duplicidade (2" in df.loc["1", "acao_sugerida"]


def test_duplicidade_so_conta_os_do_mesmo_tipo(regras):
    df = classificar(pd.DataFrame([
        os_(1, codigo="MONI-0001", tipo="CORRETIVA"), os_(2, codigo="MONI-0001", tipo="PREVENTIVA"),
        os_(3, codigo="BOMB-0002", tipo="PREVENTIVA"), os_(4, codigo="BOMB-0002", tipo="PREVENTIVA"),
        os_(5, codigo="BOMB-0002", tipo="CORRETIVA"),
    ]), regras, HOJE).set_index("os")
    assert not df.loc["1", "duplicada"] and not df.loc["2", "duplicada"]
    assert df.loc["3", "duplicada"] and df.loc["4", "duplicada"] and not df.loc["5", "duplicada"]
    assert "conferir duplicidade (2" in df.loc["3", "acao_sugerida"]


def test_prioritaria_e_resumo(regras):
    df = classificar(pd.DataFrame([
        os_(1, pendencia="(MEH) FECHAR OS"),
        os_(2, pendencia="(MEH) AGUARDANDO ANALISE DO EQUIPAMENTO", dias=45),
        os_(3, pendencia="(MEH) AGUARDANDO SETOR COMPRAS", dias=200),
    ]), regras, HOJE).set_index("os")
    assert df.loc["1", "prioritaria"] and df.loc["2", "prioritaria"] and not df.loc["3", "prioritaria"]
    r = resumo(df, regras)
    assert r == {"total": 3, "ec": 2, "externo": 1, "rapidas": 1, "antigas": 1, "expiradas": 0}


def test_ordena_do_mais_facil(regras):
    df = classificar(pd.DataFrame([
        os_(1, pendencia="(MEH) AGUARDANDO SETOR COMPRAS"),
        os_(2, pendencia="(MEH) FECHAR OS"),
    ]), regras, HOJE)
    assert list(df["os"]) == ["2", "1"]


def com_prazo(numero, pendencia, prazo_em_dias, pend_ha_dias=5, **kw):
    d = os_(numero, pendencia=pendencia, **kw)
    d["pendencia_abertura"] = f"{HOJE - dt.timedelta(days=pend_ha_dias):%d/%m/%Y} 10:00"
    d["pendencia_prazo"] = f"{HOJE + dt.timedelta(days=prazo_em_dias):%d/%m/%Y} 15:37"
    return d


def test_prazo_em_texto_e_expiradas(regras):
    df = classificar(pd.DataFrame([
        com_prazo(1, "(MEH) AGUARDANDO SETOR COMPRAS", -9), com_prazo(2, "(MEH) AGUARDANDO SETOR COMPRAS", -1),
        com_prazo(3, "(MEH) AGUARDANDO SETOR COMPRAS", 0), com_prazo(4, "(MEH) AGUARDANDO SETOR COMPRAS", 5),
        os_(5, situacao="A"),
    ]), regras, HOJE).set_index("os")
    assert list(df.loc[["1", "2", "3", "4", "5"], "prazo_txt"]) == [
        "Expirada há 9 dias", "Expirada há 1 dia", "Vence hoje", "Vence em 5 dias", "Sem prazo"]
    assert list(df.loc[["1", "2", "3", "4", "5"], "expirada"]) == [True, True, False, False, False]
    assert df.loc["1", "prioritaria"] and not df.loc["4", "prioritaria"]
    assert resumo(df.reset_index(), regras)["expiradas"] == 2


def test_fila_ordena_pelo_prazo_dentro_da_facilidade(regras):
    df = classificar(pd.DataFrame([
        com_prazo(1, "(MEH) AGUARDANDO SETOR COMPRAS", 10), com_prazo(2, "(MEH) AGUARDANDO SETOR COMPRAS", -20),
        os_(3, pendencia="(MEH) AGUARDANDO SETOR COMPRAS"), com_prazo(4, "(MEH) AGUARDANDO SETOR COMPRAS", -2),
    ]), regras, HOJE)
    assert list(df["os"]) == ["2", "4", "1", "3"]


def test_acao_muda_pelo_prazo_e_nao_pela_idade(regras):
    pend = "(MEH) AGUARDANDO ALMOXARIFADO"
    # pendência antiga, ainda no prazo: acompanhar
    assert classifica_um(regras, com_prazo(1, pend, 10, pend_ha_dias=60, dias=200)).acao_sugerida == \
        "Acompanhar entrega do almoxarifado"
    # pendência nova, mas já expirada: cobrar
    assert classifica_um(regras, com_prazo(1, pend, -1, pend_ha_dias=3)).acao_sugerida == "Cobrar entrega do almoxarifado"
    # sem prazo registrado: a ação não muda
    assert classifica_um(regras, os_(1, pendencia=pend, dias=300)).acao_sugerida == "Acompanhar entrega do almoxarifado"
