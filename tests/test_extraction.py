from src.extraction import (Extraction, Entity, Relation, clean_extraction, build_system_prompt,
                            extract_all, NODE_TYPES, REL_TYPES)
from src.chunking import Chunk
import src.extraction as ex_mod


def E(i, n, t, g=""):
    return Entity(id=i, name=n, type=t, description="", gravidade=g)


def test_vocabulario_igual_ao_modelo_da_fase1():
    assert set(NODE_TYPES) == {"Processo", "Regra", "Infracao", "Penalidade", "Orgao", "Prazo", "DocumentoExigido"}
    assert set(REL_TYPES) == {"PUNIVEL_COM", "AFETA", "REQUER", "EXIGE", "TEM_PRAZO", "DECIDIDO_POR", "PRECEDE"}


def test_prompts_baseline_e_fewshot():
    base, few = build_system_prompt("baseline"), build_system_prompt("fewshot")
    assert "Exemplo 4" in few and "Exemplo 4" not in base
    assert "PUNIVEL_COM" in base and "Processo" in base


def test_clean_descarta_relacao_com_tipos_invalidos():
    e = Extraction(entities=[E("e1", "Suspensão", "Penalidade"), E("e2", "Plágio", "Infracao")],
                   relations=[Relation(source="e1", target="e2", type="PUNIVEL_COM", evidence="x")])  # direção invertida
    assert clean_extraction(e).relations == []


def test_clean_remove_entidade_solta_e_id_inexistente():
    e = Extraction(entities=[E("e1", "Plágio", "Infracao"), E("e2", "Suspensão", "Penalidade"),
                             E("e3", "Reitor", "Orgao")],
                   relations=[Relation(source="e1", target="e2", type="PUNIVEL_COM", evidence="x"),
                              Relation(source="e1", target="e9", type="PUNIVEL_COM", evidence="x")])
    out = clean_extraction(e)
    assert {x.name for x in out.entities} == {"Plágio", "Suspensão"} and len(out.relations) == 1


def test_gravidade_so_para_infracao():
    e = Extraction(entities=[E("e1", "Plágio", "Infracao", "gravissima"), E("e2", "Suspensão", "Penalidade", "grave")],
                   relations=[Relation(source="e1", target="e2", type="PUNIVEL_COM", evidence="x")])
    out = {x.name: x.gravidade for x in clean_extraction(e).entities}
    assert out == {"Plágio": "gravissima", "Suspensão": ""}


def test_extract_all_usa_cache_e_nao_derruba_o_lote(tmp_path, monkeypatch):
    monkeypatch.setattr(ex_mod, "PROCESSED_DIR", tmp_path)
    chunks = [Chunk(f"d:art-{i}", "d", f"Art. {i}", "", 1, "texto") for i in range(3)]
    chamadas = []

    def falso(ch, titulo, versao):
        chamadas.append(ch.id)
        if ch.id == "d:art-1":
            raise RuntimeError("429")
        return Extraction(entities=[E("e1", "Plágio", "Infracao"), E("e2", "Suspensão", "Penalidade")],
                          relations=[Relation(source="e1", target="e2", type="PUNIVEL_COM", evidence="x")])

    r1 = extract_all(chunks, {"d": "Doc"}, extractor=falso)
    assert set(r1) == {"d:art-0", "d:art-2"}                     # a falha do meio não derrubou o lote
    r2 = extract_all(chunks, {"d": "Doc"}, extractor=falso)
    assert chamadas.count("d:art-0") == 1                        # segundo rodar usou o cache
    assert len(r2) == 2
