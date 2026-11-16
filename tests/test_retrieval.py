"""Busca híbrida e pipeline completo da Fase 3 com Neo4j e LLM falsos (não gastam API)."""
from src.graphrag import responder
from src.grounding import Afirmacao, Resposta
from src.retrieval import lucene_query, ordenar_fatos, q_multihop, radicais, recuperar, rrf
from src.text2cypher import ConsultaCypher


def test_rrf_premia_quem_aparece_nas_duas_listas():
    fundido = [i for i, _ in rrf(["a", "b", "c"], ["c", "d", "a"])]
    assert fundido[:2] == ["a", "c"] and set(fundido) == {"a", "b", "c", "d"}


def test_lucene_escapa_operadores():
    assert lucene_query("Art. 59: recurso (prazo)?") == r"art. 59\: recurso \(prazo\)\?"
    assert lucene_query("   ") is None


def test_radicais_sem_acento_so_palavras_longas():
    assert radicais("Fui acusado de plágio, qual o prazo?") == ["acusa*", "plagi*"]


def test_multihop_valida_saltos():
    assert "*1..2]" in q_multihop(2)
    try:
        q_multihop(5)
        assert False
    except ValueError:
        pass


def test_fatos_mais_proximos_e_concordantes_primeiro():
    fatos = [{"salto": 2, "trecho_ids": ["t1"]}, {"salto": 1, "trecho_ids": ["t9"]},
             {"salto": 1, "trecho_ids": ["t1"]}]
    assert [(f["salto"], f["trecho_ids"][0]) for f in ordenar_fatos(fatos, ["t1"])] == [(1, "t1"), (1, "t9"), (2, "t1")]


# ---------------------------------------------------------------- Neo4j falso
TRECHO = {"id": "codigo_disciplinar:art-59", "ref": "Art. 59", "ano": 2026, "documento_id": "codigo_disciplinar",
          "documento": "Código Disciplinar", "texto": "Da decisão do Reitor, cabe recurso, no prazo de cinco dias úteis, ao Consepe."}
CITADO = {**TRECHO, "id": "codigo_disciplinar:art-58", "ref": "Art. 58", "texto": "O Reitor decide o processo."}
FATO = {"id": "r1", "tipo_origem": "Processo", "origem": "Recurso", "relacao": "DECIDIDO_POR", "tipo_destino": "Orgao",
        "destino": "Consepe", "evidencias": ["ao Consepe"], "trecho_ids": ["codigo_disciplinar:art-59"],
        "anos": [2026], "salto": 1}


class _Rec(dict):
    def data(self):
        return dict(self)


class FakeDriver:
    def __init__(self):
        self.consultas = []

    def session(self, **kw):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, query, **params):
        q = query.text
        self.consultas.append(q)
        if "trecho_embedding_idx" in q or "trecho_texto_ft" in q:
            rows = [{"id": TRECHO["id"]}]
        elif "REFERE_SE_A" in q:
            rows = [{"id": CITADO["id"], "n": 1}]
        elif "entidade_nome_ft" in q or "processo_embedding_idx" in q or "MENCIONADO_EM" in q:
            rows = [{"eid": "4:x:1", "n": 1}]
        elif "relationships(p)" in q:
            rows = [FATO]
        elif "MATCH (t:Trecho) WHERE t.id IN $ids" in q:
            rows = [t for t in (TRECHO, CITADO) if t["id"] in params["ids"]]
        else:                                                    # Cypher gerado pelo "LLM"
            rows = [{"orgao": "Consepe", "embedding": [0.1] * 5}]
        return [_Rec(r) for r in rows]


def test_recuperar_graphrag_junta_busca_citados_e_fatos():
    rec = recuperar(FakeDriver(), "Quem julga o recurso?", [0.0] * 3)
    assert [(t["id"], t["origem"]) for t in rec["trechos"]] == [(TRECHO["id"], "busca"), (CITADO["id"], "citado")]
    assert rec["fatos"] == [FATO]


def test_modo_vetorial_nao_usa_grafo():
    d = FakeDriver()
    rec = recuperar(d, "Quem julga o recurso?", [0.0] * 3, modo="vetorial")
    assert rec["fatos"] == [] and not any("relationships(p)" in q or "trecho_texto_ft" in q for q in d.consultas)


def test_responder_ponta_a_ponta():
    def llm(prompt, schema, system):
        if schema is ConsultaCypher:
            return ConsultaCypher(cypher="MATCH (o:Orgao) RETURN o.nome AS orgao", explicacao="")
        assert "[T1]" in prompt and "[F1]" in prompt and "[C1]" in prompt and "embedding" not in prompt
        return Resposta(afirmacoes=[Afirmacao(texto="Cabe recurso ao Consepe em 5 dias úteis.", fontes=["T1", "F1"]),
                                    Afirmacao(texto="A taxa é de 30 reais.", fontes=["C1"])],
                        suficiente=True, conflitos="")
    r = responder("Quem julga o recurso?", FakeDriver(), gerar=llm, embed=lambda q: [0.0] * 3)
    assert [a["texto"] for a in r["aprovadas"]] == ["Cabe recurso ao Consepe em 5 dias úteis."]
    assert len(r["removidas"]) == 1 and r["cypher"]["linhas"] == [{"orgao": "Consepe"}]
