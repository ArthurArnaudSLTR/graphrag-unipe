from src.extraction import Extraction, Entity, Relation
from src.resolution import (resolve, normalize, parse_prazo, prazo_label, canonical_key,
                            fuzzy_decision, decide_merge, words_to_int)


def E(i, n, t, g=""):
    return Entity(id=i, name=n, type=t, description="", gravidade=g)


def R(s, t, tipo, ev="x"):
    return Relation(source=s, target=t, type=tipo, evidence=ev)


# ---- números e prazos
def test_numeros_por_extenso():
    assert words_to_int("quarenta e cinco".split()) == 45
    assert words_to_int("cento e vinte".split()) == 120
    assert words_to_int(["banana"]) is None


def test_normaliza_prazo():
    assert normalize("05 (cinco) dias úteis", "Prazo") == normalize("5 dias úteis", "Prazo")
    assert normalize("cinco dias úteis", "Prazo") == normalize("5 dias úteis", "Prazo")


def test_prazo_estruturado():
    assert parse_prazo("45 (quarenta e cinco) dias úteis") == {"valor": 45, "unidade": "dias", "tipo_dia": "uteis"}
    assert prazo_label(parse_prazo("1 (um) ano")) == "1 ano"
    assert parse_prazo("de 16 a 30 dias") is None          # intervalo não é prazo simples
    assert parse_prazo("semestre seguinte") is None


def test_prazos_diferentes_nao_fundem():
    assert normalize("5 dias úteis", "Prazo") != normalize("5 dias corridos", "Prazo")
    assert normalize("5 dias úteis", "Prazo") != normalize("10 dias úteis", "Prazo")


# ---- camada 1
def test_funde_aliases_do_mesmo_tipo():
    ex = {"a": Extraction(entities=[E("e1", "Coordenadoria de curso", "Orgao")], relations=[]),
          "b": Extraction(entities=[E("e1", "Coordenação do Curso", "Orgao")], relations=[])}
    ents, _, rep = resolve(ex)
    assert len(ents) == 1 and rep["reducao_pct"] == 50.0


def test_expulsao_e_desligamento():
    assert canonical_key("Expulsão", "Penalidade") == canonical_key("desligamento", "Penalidade")


def test_nunca_funde_tipos_diferentes():
    ex = {"a": Extraction(entities=[E("e1", "Suspensão", "Penalidade"), E("e2", "Suspensão", "Orgao")],
                          relations=[])}
    ents, _, _ = resolve(ex)
    assert len(ents) == 2


# ---- camada 2
def A(nome, t="Penalidade"):
    return {"nome": nome, "type": t, "aliases": [], "id": nome}


def test_suspensao_nao_funde_com_suspensao_16_a_30_dias():
    assert fuzzy_decision(A("Suspensão"), A("Suspensão de 16 a 30 dias"), 100) == "keep"


def test_grave_nao_funde_com_gravissima():
    assert fuzzy_decision(A("Infração grave", "Infracao"), A("Infração gravíssima", "Infracao"), 92) == "keep"


def test_quase_identicos_fundem():
    assert decide_merge(A("Secretaria de Controle e Registro Acadêmico", "Orgao"),
                        A("Secretaria de Controle e Registros Acadêmicos", "Orgao"), 97)


def test_duvidoso_vai_para_o_juiz():
    assert fuzzy_decision(A("Suspensão"), A("Suspensão disciplinar"), 100) == "judge"


def test_juiz_llm_injetado():
    ex = {"a": Extraction(entities=[E("e1", "Suspensão", "Penalidade")], relations=[]),
          "b": Extraction(entities=[E("e1", "Suspensão disciplinar", "Penalidade")], relations=[])}
    sem, _, rep0 = resolve(ex)
    assert len(sem) == 2 and any(d["decisao"] == "revisar" for d in rep0["decisoes_fuzzy"])
    com, _, _ = resolve(ex, use_llm_judge=True, judge=lambda a, b: {"mesmo_conceito": True, "motivo": "t"})
    assert len(com) == 1


# ---- saída
def test_ids_seguem_convencao_da_fase1():
    ex = {"a": Extraction(entities=[E("e1", "Trancamento de Matrícula", "Processo")], relations=[])}
    ents, _, _ = resolve(ex)
    assert ents[0]["id"] == "trancamento_de_matricula"


def test_prazos_viram_no_unico_com_propriedades():
    ex = {"a": Extraction(entities=[E("e1", "Recurso", "Processo"), E("e2", "cinco dias úteis", "Prazo")],
                          relations=[R("e1", "e2", "TEM_PRAZO")]),
          "b": Extraction(entities=[E("e1", "Recurso", "Processo"), E("e2", "05 (cinco) dias úteis", "Prazo")],
                          relations=[R("e1", "e2", "TEM_PRAZO")])}
    ents, rels, _ = resolve(ex)
    pr = [e for e in ents if e["type"] == "Prazo"]
    assert len(pr) == 1 and pr[0]["valor"] == 5 and pr[0]["tipo_dia"] == "uteis" and pr[0]["id"] == "5_dias_uteis"
    assert len(rels) == 2 and {r["trecho_id"] for r in rels} == {"a", "b"}


def test_relacoes_apontam_para_id_global():
    ex = {"a": Extraction(entities=[E("e1", "Infração gravíssima", "Infracao", "gravissima"),
                                    E("e2", "Suspensão", "Penalidade")],
                          relations=[R("e1", "e2", "PUNIVEL_COM")])}
    ents, rels, _ = resolve(ex)
    ids = {e["id"] for e in ents}
    assert rels[0]["source"] in ids and rels[0]["target"] in ids
    assert next(e for e in ents if e["type"] == "Infracao")["gravidade"] == "gravissima"


def test_nome_de_exibicao_prefere_a_forma_canonica():
    ex = {"a": Extraction(entities=[E("e1", "Expulsão", "Penalidade")], relations=[]),
          "b": Extraction(entities=[E("e1", "Expulsão", "Penalidade")], relations=[]),
          "c": Extraction(entities=[E("e1", "Desligamento", "Penalidade")], relations=[])}
    ents, _, _ = resolve(ex)
    assert ents[0]["nome"] == "Desligamento" and ents[0]["id"] == "desligamento" and "Expulsão" in ents[0]["aliases"]
