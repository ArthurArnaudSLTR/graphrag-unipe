"""Orquestrador da Fase 2 (Ingestão e Pipeline NLP/LLM).

    python run_phase2.py chunk                          # 1. PDFs -> trechos (grátis, sem API)
    python run_phase2.py extract --limit 5              # 2. NER/RE com Gemini (teste com poucos!)
    python run_phase2.py extract --prompt baseline      #    prompt SEM exemplos (para comparar)
    python run_phase2.py review --n 40                  # 3. amostra para conferência manual (CSV)
    python run_phase2.py metrics                        #    precisão/cobertura a partir do CSV preenchido
    python run_phase2.py extract                        # 4. extração completa (cache: pode interromper e retomar)
    python run_phase2.py resolve [--llm-judge]          # 5. resolução de entidades + relatório antes/depois
    python run_phase2.py embed                          # 6. embeddings dos trechos e dos Processos
    python run_phase2.py ingest [--reset]               # 7. schema + dados no Neo4j
    python run_phase2.py stats                          # 8. contagens e checagem de duplicatas
"""
import argparse
import csv
import json
import random

from src.config import DOCS, RAW_DIR, PROCESSED_DIR, EMBED_DIM
from src.chunking import chunk_document
from src.extraction import PROMPT_VERSIONS

TITULOS = {d["id"]: d["titulo"] for d in DOCS}
ANOS = {d["id"]: d["ano"] for d in DOCS}


def load_chunks(doc_filter=None):
    return [c for d in DOCS if not doc_filter or d["id"] == doc_filter
            for c in chunk_document(d, RAW_DIR)]


def load_cached_extractions(chunks, version="fewshot"):
    from src.extraction import Extraction, _cache_path
    out = {}
    for c in chunks:
        p = _cache_path(c.id, version)
        if p.exists():
            out[c.id] = Extraction.model_validate_json(p.read_text(encoding="utf-8"))
    return out


def dump(name, obj):
    (PROCESSED_DIR / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def load(name):
    p = PROCESSED_DIR / name
    if not p.exists():
        raise SystemExit(f"Falta data/processed/{name}. Rode os passos anteriores (veja --help).")
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- comandos
def cmd_chunk(a):
    chunks = load_chunks()
    dump("chunks.json", [c.to_dict() for c in chunks])
    for d in DOCS:
        print(f"{d['id']:<30} {sum(c.doc_id == d['id'] for c in chunks):>4} trechos")
    print(f"TOTAL {len(chunks)} trechos -> data/processed/chunks.json")


def cmd_extract(a):
    from src.extraction import extract_all
    chunks = load_chunks(a.doc)
    res = extract_all(chunks, TITULOS, a.limit, a.prompt)
    print(f"\n{len(res)} trechos com extração (cache em data/processed/extractions/{a.prompt}/)")


def cmd_review(a):
    chunks = load_chunks()
    ext = load_cached_extractions(chunks, a.prompt)
    if not ext:
        raise SystemExit("Nenhuma extração em cache. Rode 'extract' antes.")
    by_id = {c.id: c for c in chunks}
    ids = sorted(ext)
    random.Random(7).shuffle(ids)           # semente fixa: o grupo todo revisa a mesma amostra
    path = PROCESSED_DIR / f"review_sample_{a.prompt}.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["trecho", "texto (início)", "entidades", "relacoes", "ent_total", "ent_corretas",
                    "ent_omitidas", "rel_total", "rel_corretas", "rel_omitidas", "erro encontrado"])
        for cid in ids[:a.n]:
            e = ext[cid]
            nome = {x.id: x.name for x in e.entities}
            w.writerow([f"{by_id[cid].ref} ({cid})", by_id[cid].text[:400].replace("\n", " "),
                        " | ".join(f"{x.name} [{x.type}]" for x in e.entities),
                        " | ".join(f"{nome[r.source]} -{r.type}-> {nome[r.target]}" for r in e.relations),
                        len(e.entities), "", "", len(e.relations), "", "", ""])
    print(f"Amostra de {min(a.n, len(ids))} trechos em {path}\n"
          "Preencham ent_corretas / ent_omitidas / rel_corretas / rel_omitidas (números) e rodem 'metrics'.")


def cmd_metrics(a):
    path = PROCESSED_DIR / f"review_sample_{a.prompt}.csv"
    if not path.exists():
        raise SystemExit(f"Falta {path}. Rode 'review' e preencha o CSV.")
    tot = {"ent_total": 0, "ent_corretas": 0, "ent_omitidas": 0,
           "rel_total": 0, "rel_corretas": 0, "rel_omitidas": 0}
    linhas = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter=";"):
            if not (row["ent_corretas"].strip() and row["rel_corretas"].strip()):
                continue                                           # linha ainda não revisada
            linhas += 1
            for k in tot:
                tot[k] += int((row[k] or "0").strip() or 0)
    if not linhas:
        raise SystemExit("Nenhuma linha preenchida ainda.")

    def pct(x, y):
        return f"{100 * x / y:.1f}%" if y else "n/a"
    print(f"Trechos revisados: {linhas}")
    print(f"Entidades  precisão {pct(tot['ent_corretas'], tot['ent_total'])}  "
          f"cobertura {pct(tot['ent_corretas'], tot['ent_corretas'] + tot['ent_omitidas'])}")
    print(f"Relações   precisão {pct(tot['rel_corretas'], tot['rel_total'])}  "
          f"cobertura {pct(tot['rel_corretas'], tot['rel_corretas'] + tot['rel_omitidas'])}")
    print(f"(prompt: {a.prompt}) Rodem para 'baseline' e 'fewshot' e comparem: é a figura do artigo.")


def cmd_resolve(a):
    from src.resolution import resolve
    chunks = load_chunks()
    ext = load_cached_extractions(chunks, a.prompt)
    if not ext:
        raise SystemExit("Nenhuma extração em cache. Rode 'extract' antes.")
    entities, relations, report = resolve(ext, use_llm_judge=a.llm_judge)
    dump("entities.json", entities)
    dump("relations.json", relations)
    dump("resolution_report.json", report)
    print(f"menções brutas: {report['mencoes_brutas']} -> nós únicos: {report['nos_unicos']} "
          f"(redução de {report['reducao_pct']}%) | relações: {report['relacoes']}")
    print(f"por tipo: {report['por_tipo']}")
    print(f"grupos fundidos: {len(report['grupos_fundidos'])} | prazos normalizados: "
          f"{len(report['prazos_normalizados'])} | decisões fuzzy: {len(report['decisoes_fuzzy'])}")
    pend = [d for d in report["decisoes_fuzzy"] if d["decisao"] == "revisar"]
    print(f"pares para revisar à mão (ou com --llm-judge): {len(pend)}")
    print("Leiam data/processed/resolution_report.json: é a base da seção 'antes/depois' do artigo.")


def cmd_embed(a):
    from src.gemini_client import embed_documents
    chunks = load_chunks()
    # --- trechos
    path = PROCESSED_DIR / "embeddings.json"
    cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    todo = [c for c in chunks if c.id not in cache]
    print(f"trechos: {len(cache)} em cache, {len(todo)} a gerar")
    for i in range(0, len(todo), 16):
        lote = todo[i:i + 16]
        vecs = embed_documents([(f"{TITULOS[c.doc_id]} - {c.ref}", c.text)
                                for c in lote])
        cache.update({c.id: v for c, v in zip(lote, vecs)})
        path.write_text(json.dumps(cache), encoding="utf-8")        # salva a cada lote
        print(f"  {len(cache)}/{len(chunks)}")
    # --- nós Processo (índice processo_embedding_idx da Fase 1)
    ents = load("entities.json")
    procs = [e for e in ents if e["type"] == "Processo"]
    p2 = PROCESSED_DIR / "entity_embeddings.json"
    c2 = json.loads(p2.read_text(encoding="utf-8")) if p2.exists() else {}
    todo2 = [e for e in procs if e["id"] not in c2]
    print(f"processos: {len(c2)} em cache, {len(todo2)} a gerar")
    for i in range(0, len(todo2), 16):
        lote = todo2[i:i + 16]
        textos = [(e["nome"], " ".join([e["nome"], e.get("descricao") or "", " ".join(e["aliases"])]).strip())
                  for e in lote]
        c2.update({e["id"]: v for e, v in zip(lote, embed_documents(textos))})
        p2.write_text(json.dumps(c2), encoding="utf-8")
        print(f"  {len(c2)}/{len(procs)}")


def cmd_ingest(a):
    from src import neo4j_io as nj
    from src.crossrefs import find_crossrefs
    chunks = load_chunks()
    emb = load("embeddings.json")
    faltam = [c.id for c in chunks if c.id not in emb]
    if faltam:
        raise SystemExit(f"{len(faltam)} trechos sem embedding. Rode 'embed' antes.")
    dim = len(next(iter(emb.values())))
    if dim != EMBED_DIM:
        raise SystemExit(f"Embeddings têm {dim} dimensões, mas EMBEDDING_DIM={EMBED_DIM}. "
                         "Ajuste o .env (e recrie o índice vetorial se ele já existir).")
    trechos = [{"id": c.id, "documento_id": c.doc_id, "ref": c.ref, "secao": c.section_path,
                "pagina": c.page, "texto": c.text, "ano": ANOS[c.doc_id], "embedding": emb[c.id]}
               for c in chunks]
    entities = load("entities.json")
    relations = load("relations.json")
    ent_emb = {}
    p2 = PROCESSED_DIR / "entity_embeddings.json"
    if p2.exists():
        ent_emb = json.loads(p2.read_text(encoding="utf-8"))
    for e in entities:
        if e["type"] == "Processo" and e["id"] in ent_emb:
            e["embedding"] = ent_emb[e["id"]]
    agg = nj.aggregate_relations(relations, {c.id: ANOS[c.doc_id] for c in chunks})
    refs = find_crossrefs(chunks, {d["id"] for d in DOCS if d["kind"] == "legal"})

    driver = nj.get_driver()
    print(f"schema: {nj.apply_schema(driver)} comandos aplicados (IF NOT EXISTS)")
    if a.reset:
        nj.reset_pipeline(driver)
        print("dados anteriores da Fase 2 apagados")
    nj.ingest_documents(driver, DOCS)
    nj.ingest_trechos(driver, trechos)
    nj.ingest_entities(driver, entities)
    nj.ingest_relations(driver, agg)
    nj.ingest_crossrefs(driver, refs)
    print(f"Ingestão concluída: {len(trechos)} trechos, {len(entities)} entidades, "
          f"{len(agg)} relações semânticas, {len(refs)} referências cruzadas. Rode 'stats'.")
    nj.stats(driver)
    driver.close()


def cmd_stats(a):
    from src import neo4j_io as nj
    d = nj.get_driver()
    nj.stats(d)
    d.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in [("chunk", cmd_chunk), ("extract", cmd_extract), ("review", cmd_review),
                     ("metrics", cmd_metrics), ("resolve", cmd_resolve), ("embed", cmd_embed),
                     ("ingest", cmd_ingest), ("stats", cmd_stats)]:
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        if name in ("extract", "review", "metrics", "resolve"):
            p.add_argument("--prompt", choices=PROMPT_VERSIONS, default="fewshot",
                           help="versão do prompt (baseline = sem exemplos)")
        if name == "extract":
            p.add_argument("--limit", type=int, default=None, help="só os N primeiros trechos")
            p.add_argument("--doc", default=None, help="id de um documento (ver config.py)")
        if name == "review":
            p.add_argument("--n", type=int, default=40)
        if name == "resolve":
            p.add_argument("--llm-judge", action="store_true",
                           help="camada 3: Gemini decide os pares duvidosos (gasta API)")
        if name == "ingest":
            p.add_argument("--reset", action="store_true",
                           help="apaga antes o que a Fase 2 criou (use ao refazer a resolução)")
    args = ap.parse_args()
    args.fn(args)
