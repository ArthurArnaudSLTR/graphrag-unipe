"""Rode com:  pytest -q      Estes testes protegem o chunking contra regressões."""
from src.config import DOCS, RAW_DIR
from src.chunking import chunk_document

D = {d["id"]: d for d in DOCS}


def chunks(doc_id):
    return chunk_document(D[doc_id], RAW_DIR)


def arts(cs):
    return [int(c.ref.split()[1]) for c in cs if c.ref.startswith("Art.")]


def test_ids_unicos():
    for d in DOCS:
        ids = [c.id for c in chunks(d["id"])]
        assert len(ids) == len(set(ids)), d["id"]


def test_codigo_tem_69_artigos_em_sequencia():
    a = arts(chunks("codigo_disciplinar"))
    assert a == list(range(1, 70))


def test_codigo_glossario_por_termo():
    gl = [c for c in chunks("codigo_disciplinar") if c.ref.startswith("Glossário")]
    assert len(gl) >= 40
    assert any("Reincidência" in c.ref for c in gl)


def test_regulamento_tem_24_artigos_e_secoes():
    cs = {c.ref: c for c in chunks("regulamento_aproveitamento")}
    assert arts(list(cs.values())) == list(range(1, 25))
    assert "Diplomado" in cs["Art. 18"].section_path
    assert "CAPÍTULO V" in cs["Art. 22"].section_path


def test_artigo_nao_vaza_cabecalho_nem_rodape():
    for c in chunks("regulamento_aproveitamento") + chunks("codigo_disciplinar"):
        assert "Recredenciada pela Portaria" not in c.text
        assert "www.unipe.edu.br" not in c.text


def test_manual_uma_chunk_por_pagina():
    cs = chunks("manual_aluno")
    assert len(cs) == 33 and cs[0].page == 6 and cs[-1].page == 38
