"""Tudo que escreve no Neo4j, seguindo o modelo LPG da Fase 1 (docs/modelo.md).

Princípios:
  * MERGE (nunca CREATE): rodar duas vezes não duplica nada.
  * Rótulos e tipos de relação NÃO aceitam parâmetro em Cypher; por isso entram na string da
    query, mas SOMENTE a partir das listas fechadas NODE_TYPES/REL_TYPES, nunca de texto do LLM.
  * Tudo que o pipeline cria recebe `fonte: 'fase2'`; `reset_pipeline` apaga só isso (o schema e
    qualquer dado de teste da Fase 1 ficam intactos).
"""
from neo4j import GraphDatabase

from .config import (ROOT, NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE, EMBED_DIM)
from .extraction import NODE_TYPES, REL_TYPES

FONTE = "fase2"

# propriedades que cada rótulo aceita (modelo da Fase 1). Só estas são gravadas.
PROPS = {
    "Processo": ["nome", "aliases", "descricao", "embedding"],
    "Regra": ["nome", "aliases", "descricao"],
    "Infracao": ["nome", "aliases", "gravidade"],
    "Penalidade": ["nome", "aliases", "descricao"],
    "Orgao": ["nome", "aliases"],
    "Prazo": ["nome", "valor", "unidade", "tipo_dia", "descricao"],
    "DocumentoExigido": ["nome", "aliases"],
}


def get_driver():
    d = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    d.verify_connectivity()
    return d


def _run(driver, query, **params):
    return driver.execute_query(query, database_=NEO4J_DATABASE, **params).records


def _batched(driver, query, rows, size=300):
    for i in range(0, len(rows), size):
        _run(driver, query, rows=rows[i:i + size])


# ---------------------------------------------------------------- schema (Fase 1)
def apply_schema(driver):
    """Aplica neo4j/schema.cypher (constraints, índices range/fulltext/vetorial). Idempotente."""
    texto = (ROOT / "neo4j" / "schema.cypher").read_text(encoding="utf-8")
    texto = texto.replace("__EMBEDDING_DIM__", str(int(EMBED_DIM)))
    linhas = [l for l in texto.splitlines() if not l.strip().startswith("//")]
    comandos = [c.strip() for c in "\n".join(linhas).split(";") if c.strip()]
    for c in comandos:
        _run(driver, c)
    return len(comandos)


def reset_pipeline(driver):
    """Apaga só o que a Fase 2 criou (fonte='fase2'). Use antes de reingerir após mudar a resolução."""
    _run(driver, "MATCH (n {fonte: $f}) DETACH DELETE n", f=FONTE)


# ---------------------------------------------------------------- dados
def ingest_documents(driver, docs: list[dict]):
    rows = [{"id": d["id"], "titulo": d["titulo"], "tipo": d["tipo"], "numero": d["numero"],
             "data": d["data"], "ano": d["ano"], "arquivo": d["file"]} for d in docs]
    _batched(driver, f"""
        UNWIND $rows AS r
        MERGE (d:Documento {{id: r.id}})
        SET d.titulo = r.titulo, d.tipo = r.tipo, d.numero = r.numero, d.data = r.data,
            d.ano = r.ano, d.arquivo = r.arquivo, d.fonte = '{FONTE}'""", rows)


def ingest_trechos(driver, trechos: list[dict]):
    """trechos: dicts com id, documento_id, ref, secao, pagina, texto, ano, embedding."""
    _batched(driver, f"""
        UNWIND $rows AS r
        MERGE (t:Trecho {{id: r.id}})
        SET t.documento_id = r.documento_id, t.ref = r.ref, t.secao = r.secao, t.pagina = r.pagina,
            t.texto = r.texto, t.ano = r.ano, t.embedding = r.embedding, t.fonte = '{FONTE}'
        WITH t, r
        MATCH (d:Documento {{id: r.documento_id}})
        MERGE (t)-[:PARTE_DE]->(d)""", trechos, size=50)


def ingest_entities(driver, entities: list[dict]):
    """Um UNWIND por rótulo. Cria o nó e a relação (e)-[:MENCIONADO_EM]->(:Trecho) (rastreabilidade)."""
    for type_ in NODE_TYPES:
        rows = []
        for e in entities:
            if e["type"] != type_:
                continue
            props = {k: e.get(k) for k in PROPS[type_] if k in e}
            if type_ == "Prazo":
                props["descricao"] = e.get("nome")          # modelo da Fase 1: Prazo tem `descricao`
            rows.append({"id": e["id"], "props": props, "trecho_ids": e["trecho_ids"]})
        if not rows:
            continue
        _batched(driver, f"""
            UNWIND $rows AS r
            MERGE (e:`{type_}` {{id: r.id}})
            SET e += r.props, e.fonte = '{FONTE}'
            WITH e, r
            UNWIND r.trecho_ids AS tid
            MATCH (t:Trecho {{id: tid}})
            MERGE (e)-[:MENCIONADO_EM]->(t)""", rows, size=100)


def aggregate_relations(relations: list[dict], ano_por_trecho: dict[str, int]) -> list[dict]:
    """Junta as ocorrências da mesma aresta (origem, tipo, destino) numa só, com listas alinhadas
    trecho_ids / anos / evidencias. Assim a mesma regra citada em 2020 e em 2026 fica rastreável."""
    agg: dict[tuple, dict] = {}
    for r in relations:
        k = (r["source_type"], r["source"], r["type"], r["target_type"], r["target"])
        a = agg.setdefault(k, {"source": r["source"], "source_type": r["source_type"],
                               "target": r["target"], "target_type": r["target_type"],
                               "type": r["type"], "trecho_ids": [], "anos": [], "evidencias": []})
        if r["trecho_id"] not in a["trecho_ids"]:
            a["trecho_ids"].append(r["trecho_id"])
            a["anos"].append(ano_por_trecho.get(r["trecho_id"]))
            a["evidencias"].append(r["evidence"])
    return list(agg.values())


def ingest_relations(driver, aggregated: list[dict]):
    por_forma: dict[tuple, list] = {}
    for r in aggregated:
        por_forma.setdefault((r["type"], r["source_type"], r["target_type"]), []).append(r)
    for (type_, s_t, t_t), rows in por_forma.items():
        assert type_ in REL_TYPES and s_t in NODE_TYPES and t_t in NODE_TYPES   # lista fechada
        _batched(driver, f"""
            UNWIND $rows AS r
            MATCH (a:`{s_t}` {{id: r.source}}), (b:`{t_t}` {{id: r.target}})
            MERGE (a)-[x:`{type_}`]->(b)
            SET x.trecho_ids = r.trecho_ids, x.anos = r.anos, x.evidencias = r.evidencias,
                x.fonte = '{FONTE}'""", rows)


def ingest_crossrefs(driver, refs: list[dict]):
    """(:Trecho)-[:REFERE_SE_A]->(:Trecho): referências 'art. N' entre trechos do mesmo documento."""
    _batched(driver, f"""
        UNWIND $rows AS r
        MATCH (a:Trecho {{id: r.origem}}), (b:Trecho {{id: r.destino}})
        MERGE (a)-[x:REFERE_SE_A]->(b)
        SET x.fonte = '{FONTE}', x.citacao = r.citacao""", refs)


# ---------------------------------------------------------------- conferência
def stats(driver):
    q = {
        "nós por rótulo": "MATCH (n) UNWIND labels(n) AS l RETURN l AS k, count(*) AS n ORDER BY n DESC",
        "relações por tipo": "MATCH ()-[r]->() RETURN type(r) AS k, count(*) AS n ORDER BY n DESC",
        "possíveis duplicatas (mesmo rótulo + mesmo nome)": """MATCH (e) WHERE e.fonte = 'fase2' AND e.nome IS NOT NULL
            WITH labels(e)[0] AS t, toLower(e.nome) AS nome, count(*) AS c WHERE c > 1
            RETURN t + ' / ' + nome AS k, c AS n""",
        "entidades sem nenhuma relação semântica": """MATCH (e) WHERE e.fonte = 'fase2'
            AND any(l IN labels(e) WHERE l IN $tipos)
            AND NOT (e)-[:PUNIVEL_COM|AFETA|REQUER|EXIGE|TEM_PRAZO|DECIDIDO_POR|PRECEDE]-()
            RETURN 'isoladas' AS k, count(e) AS n""",
        "trechos sem embedding": "MATCH (t:Trecho) WHERE t.embedding IS NULL RETURN 'trechos' AS k, count(t) AS n",
        "índices (nome / estado)": "SHOW INDEXES YIELD name, state RETURN name AS k, state AS n",
    }
    for title, query in q.items():
        print(f"\n{title}")
        params = {"tipos": list(NODE_TYPES)} if "$tipos" in query else {}
        for r in _run(driver, query, **params):
            print(f"  {r['k']}: {r['n']}")
