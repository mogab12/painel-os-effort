from painel.coletor import (RE_CONTAGEM, ler_pendencia, montar_tabela, pendencias_abertas_da_os, separar_equipamento,
                            vence_primeiro)

# linha real da grade
LINHA = ["", "202602509", "INCU-0002", "INCUBADORA - 1186 (FANEM)\nUNIDADE NEONATAL", "CORRETIVA", "A", "16/10/2026", ""]


def test_separar_equipamento_com_plano():
    assert separar_equipamento("MAMOGRAFO - SELENIA (HOLOGIC)\nRADIOLOGIA\nPLANO: MAMOGRAFO PREVENTIVA") == (
        "MAMOGRAFO - SELENIA (HOLOGIC)", "RADIOLOGIA", "MAMOGRAFO PREVENTIVA")


def test_tira_contagem_do_filtro():
    assert RE_CONTAGEM.sub("", "PAULO MALUSÁ ZANUZZIO (3)") == "PAULO MALUSÁ ZANUZZIO"


def test_montar_tabela():
    df = montar_tabela([LINHA], {"202602509": "(MEH) FECHAR OS"}, {"202602509": "ARTHUR LOPES"})
    l = df.iloc[0]
    assert (l.os, l.codigo, l.equipamento, l.setor, l.situacao) == ("202602509", "INCU-0002", "INCUBADORA", "UNIDADE NEONATAL", "A")
    assert l.ultima_pendencia == "(MEH) FECHAR OS" and l.responsavel == "ARTHUR LOPES"


HTML_OS = """<table><tr class="GRA" onclick="GSR(this, event, &#39;8114&#39;)"><td><img src="img/ico_pendencia.png"></td>
<td>15/10/2026 15:37</td><td>(MEH) AGUARDANDO EXECUÇÃO DO SERVIÇO TERCEIROS</td></tr>
<tr class="GR" onclick="GSR(this, event, &#39;7651&#39;)"><td><img src="img/ico_pendenciaconcluida.png"></td>
<td>14/09/2026 15:00</td><td>(MEH) AGUARDANDO SETOR COMPRAS</td></tr>
<tr class="GR" onclick="GSR(this, event, &#39;9001&#39;)"><td><img src="img/ico_material.png"></td><td>PEÇA</td></tr></table>"""

HTML_PENDENCIA = """<input name="cbopendencia" type="text" value="(MEH) AGUARDANDO EXECU&#199;&#195;O DO SERVI&#199;O TERCEIROS" id="cbopendencia" />
<input name="txtdatapendenciaabertura" type="text" value="15/10/2026" id="txtdatapendenciaabertura" />
<input name="txthorapendenciaabertura" type="text" value="15:37" id="txthorapendenciaabertura" />
<input name="txtdataprazo" type="text" value="26/10/2026" id="txtdataprazo" />
<input name="txthoraprazo" type="text" value="15:37" id="txthoraprazo" />"""


def test_pendencias_abertas_da_os_ignora_concluidas_e_outras_atividades():
    assert pendencias_abertas_da_os(HTML_OS) == ["8114"]


def test_ler_pendencia():
    assert ler_pendencia(HTML_PENDENCIA) == {"pendencia": "(MEH) AGUARDANDO EXECUÇÃO DO SERVIÇO TERCEIROS",
                                             "abertura": "15/10/2026 15:37", "prazo": "26/10/2026 15:37"}


def test_vence_primeiro():
    p = [{"pendencia": "NF", "prazo": "30/10/2026 10:00"}, {"pendencia": "TERCEIROS", "prazo": "29/10/2026 10:29"},
         {"pendencia": "SEM PRAZO", "prazo": ""}]
    assert vence_primeiro(p)["pendencia"] == "TERCEIROS"
    assert vence_primeiro([]) is None


def test_montar_tabela_usa_a_pendencia_que_vence_primeiro():
    prazos = {"202602509": {"pendencia": "(MEH) AGUARDANDO PEÇA", "abertura": "21/10/2026 07:17", "prazo": "22/10/2026 07:17"}}
    l = montar_tabela([LINHA + ["1234"]], {"202602509": "(MEH) FECHAR OS"}, {}, prazos).iloc[0]
    assert l.ultima_pendencia == "(MEH) AGUARDANDO PEÇA"
    assert (l.pendencia_abertura, l.pendencia_prazo) == ("21/10/2026 07:17", "22/10/2026 07:17")
