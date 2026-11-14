from src.chunking import Chunk
from src.crossrefs import find_crossrefs


def C(n, texto):
    return Chunk(f"d:art-{n}", "d", f"Art. {n}", "", 1, texto)


def test_referencia_interna_vira_aresta():
    cs = [C(1, "Art. 1. x"), C(2, "Art. 2. Observado o art. 1º, aplica-se.")]
    assert [(r["origem"], r["destino"]) for r in find_crossrefs(cs, {"d"})] == [("d:art-2", "d:art-1")]


def test_ignora_norma_externa_e_autorreferencia():
    cs = [C(1, "Art. 1. x"), C(2, "Art. 2. Nos termos do art. 1º da Lei nº 9.394 e do art. 2."),
          C(3, "Art. 3. Conforme art. 7º, II, da LGPD.")]
    assert find_crossrefs(cs, {"d"}) == []


def test_intervalos_e_listas():
    cs = [C(i, f"Art. {i}. x") for i in range(1, 7)]
    cs[5] = C(6, "Art. 6. Ver arts. 2º a 4º e art. 1.")
    destinos = {r["destino"] for r in find_crossrefs(cs, {"d"})}
    assert destinos == {"d:art-1", "d:art-2", "d:art-3", "d:art-4"}


def test_codigo_real_tem_referencias_e_todas_internas():
    from src.config import DOCS, RAW_DIR
    from src.chunking import chunk_document
    cs = [c for d in DOCS for c in chunk_document(d, RAW_DIR)]
    refs = find_crossrefs(cs, {d["id"] for d in DOCS if d["kind"] == "legal"})
    assert refs and all(r["origem"].split(":")[0] == r["destino"].split(":")[0] for r in refs)
    assert not any(r["origem"] == "codigo_disciplinar:art-65" for r in refs)   # art. 7º/11 da LGPD é externo
