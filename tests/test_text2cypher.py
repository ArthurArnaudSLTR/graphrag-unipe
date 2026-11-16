import pytest

from src.text2cypher import CypherInvalido, ConsultaCypher, gerar_cypher, validar_cypher


def test_aceita_leitura_e_poe_limit():
    q = validar_cypher("MATCH (i:Infracao)-[r:PUNIVEL_COM]->(p:Penalidade) RETURN i.nome, p.nome")
    assert q.endswith("LIMIT 25")


def test_reduz_limit_grande_e_tira_cercas_markdown():
    q = validar_cypher("```cypher\nMATCH (p:Processo) RETURN p.nome LIMIT 5000;\n```")
    assert "LIMIT 25" in q and "5000" not in q and "```" not in q


@pytest.mark.parametrize("q", [
    "MATCH (n) DETACH DELETE n RETURN 1",
    "MATCH (p:Processo) SET p.nome = 'x' RETURN p",
    "CREATE (n:Processo {id: 'x'}) RETURN n",
    "CALL db.index.fulltext.queryNodes('trecho_texto_ft', 'x') YIELD node RETURN node",
    "MATCH (n) RETURN n; MATCH (m) DELETE m",
])
def test_bloqueia_escrita_e_procedimentos(q):
    with pytest.raises(CypherInvalido):
        validar_cypher(q)


def test_palavra_proibida_dentro_de_string_nao_conta():
    validar_cypher("MATCH (p:Processo) WHERE toLower(p.nome) CONTAINS 'set' RETURN p.nome")


def test_bloqueia_rotulo_e_relacao_inventados():
    with pytest.raises(CypherInvalido, match="Aluno"):
        validar_cypher("MATCH (a:Aluno)-[:REQUER]->(r:Regra) RETURN a")
    with pytest.raises(CypherInvalido, match="PUNIDO_POR"):
        validar_cypher("MATCH (i:Infracao)-[:PUNIDO_POR]->(p:Penalidade) RETURN i")


def test_aceita_varias_relacoes_e_tamanho_variavel():
    validar_cypher("MATCH (p:Processo)-[:REQUER|TEM_PRAZO*1..2]-(x) RETURN p.nome, x.nome")


def test_corrige_uma_vez_com_o_erro():
    respostas = iter([ConsultaCypher(cypher="MATCH (a:Aluno) RETURN a", explicacao=""),
                      ConsultaCypher(cypher="", explicacao="não dá")])
    prompts = []

    def llm(prompt, schema, system):
        prompts.append(prompt)
        return next(respostas)
    r = gerar_cypher("pergunta", driver=None, gerar=llm)
    assert len(prompts) == 2 and "Aluno" in prompts[1] and r["linhas"] == [] and r["erro"] is None
