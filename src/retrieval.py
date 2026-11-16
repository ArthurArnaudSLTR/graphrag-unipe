"""Fase 3 - recuperação: busca híbrida (vetor + fulltext, fundidos por RRF) e travessia multi-hop.

Fluxo de uma pergunta:
  1. Busca híbrida nos Trechos: índice vetorial (sentido) + fulltext (palavras exatas: "Consepe",
     "monitoria"). As duas listas são fundidas por Reciprocal Rank Fusion, que usa só a POSIÇÃO de
     cada item: os scores de cosseno e do Lucene não são comparáveis entre si.
  2. Entidades-semente: fulltext em nomes/aliases + vetor dos Processos + entidades MENCIONADO_EM
     nos melhores trechos (de novo fundidas por RRF).
  3. Multi-hop: a partir das sementes, segue as relações semânticas até N saltos. Cada aresta volta
     com a evidência literal e os trechos de origem. Também segue REFERE_SE_A (artigo que cita artigo).

Modos (para a comparação do artigo):
  "vetorial"  só o índice vetorial dos trechos (RAG clássico, linha de base)
  "hibrido"   vetor + fulltext, sem grafo
  "graphrag"  híbrido + sementes + multi-hop + trechos citados          <- padrão
"""
import re
import unicodedata
from collections import Counter

from neo4j import READ_ACCESS, Query
from neo4j.exceptions import ClientError

from .config import NEO4J_DATABASE
from .extraction import REL_TYPES

MODOS = ("vetorial", "hibrido", "graphrag")
RRF_K = 60                      # constante do artigo original do RRF (Cormack et al., 2009)
SEM_RELS = "|".join(REL_TYPES)  # lista fechada: entra na string da query com segurança
_LUCENE_ESPECIAIS = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')


def rrf(*rankings, k=RRF_K):
    """Reciprocal Rank Fusion: score(d) = soma de 1/(k + posição) em cada lista.
    Recebe listas de ids (melhor primeiro) e devolve [(id, score)] do maior para o menor."""
    scores: dict = {}
    for ranking in rankings:
        for pos, item in enumerate(ranking, 1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + pos)
    return sorted(scores.items(), key=lambda x: -x[1])


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def lucene_query(pergunta: str) -> str | None:
    """Texto livre -> consulta Lucene segura (escapa operadores; minúsculas desarmam AND/OR/NOT)."""
    q = _LUCENE_ESPECIAIS.sub(r"\\\1", pergunta.lower()).strip()
    return q or None


def radicais(pergunta: str) -> list[str]:
    """Radical de cada palavra longa ("plágio" -> plagi*). O stemmer do analisador 'portuguese' junta
    plural e singular, mas não substantivo e verbo ("plágio" x "plagiar"). Vai sem acento porque o
    índice guarda os termos sem acento e o curinga não passa pelo analisador."""
    palavras = re.findall(r"[^\W\d_]{6,}", _sem_acento(pergunta.lower()))
    return list(dict.fromkeys(f"{p[:max(5, len(p) - 3)]}*" for p in palavras))


def read(driver, query, timeout: float | None = None, **params) -> list[dict]:
    """Consulta só de leitura: a sessão é READ, então o servidor recusa qualquer escrita."""
    with driver.session(database=NEO4J_DATABASE, default_access_mode=READ_ACCESS) as s:
        return [r.data() for r in s.run(Query(query, timeout=timeout), **params)]


def _read_ft(driver, query, **params) -> list[dict]:
    """Fulltext tolera falha (consulta Lucene estranha): sem resultado em vez de derrubar a pergunta."""
    try:
        return read(driver, query, **params)
    except ClientError:
        return []


# ---------------------------------------------------------------- consultas
Q_VETOR_TRECHO = """CALL db.index.vector.queryNodes('trecho_embedding_idx', $k, $vec) YIELD node, score
RETURN node.id AS id"""

Q_FT_TRECHO = """CALL db.index.fulltext.queryNodes('trecho_texto_ft', $q, {limit: $k}) YIELD node, score
RETURN node.id AS id"""

Q_TRECHOS = """MATCH (t:Trecho) WHERE t.id IN $ids
OPTIONAL MATCH (t)-[:PARTE_DE]->(d:Documento)
RETURN t.id AS id, t.ref AS ref, t.texto AS texto, t.ano AS ano, t.documento_id AS documento_id,
       coalesce(d.titulo, t.documento_id) AS documento"""

Q_CITADOS = """MATCH (t:Trecho)-[:REFERE_SE_A]->(c:Trecho) WHERE t.id IN $ids AND NOT c.id IN $ids
RETURN c.id AS id, count(*) AS n ORDER BY n DESC LIMIT $k"""

Q_SEMENTES_FT = """CALL db.index.fulltext.queryNodes('entidade_nome_ft', $q, {limit: $k}) YIELD node, score
RETURN elementId(node) AS eid"""

Q_SEMENTES_VETOR = """CALL db.index.vector.queryNodes('processo_embedding_idx', $k, $vec) YIELD node, score
RETURN elementId(node) AS eid"""

Q_SEMENTES_TRECHO = """MATCH (e)-[:MENCIONADO_EM]->(t:Trecho) WHERE t.id IN $ids
RETURN elementId(e) AS eid, count(t) AS n ORDER BY n DESC LIMIT $k"""


def q_multihop(saltos: int) -> str:
    """Arestas semânticas a até `saltos` das sementes. O nº de saltos não aceita parâmetro em
    Cypher, por isso é validado aqui e entra na string."""
    if saltos not in (1, 2, 3):
        raise ValueError("saltos deve ser 1, 2 ou 3")
    return f"""MATCH (s) WHERE elementId(s) IN $eids
MATCH p = (s)-[:{SEM_RELS}*1..{saltos}]-()
WITH p LIMIT 3000
UNWIND range(0, length(p) - 1) AS i
WITH relationships(p)[i] AS r, i + 1 AS salto
WITH r, min(salto) AS salto
WITH r, salto, startNode(r) AS a, endNode(r) AS b
RETURN elementId(r) AS id, labels(a)[0] AS tipo_origem, coalesce(a.nome, a.id) AS origem,
       type(r) AS relacao, labels(b)[0] AS tipo_destino, coalesce(b.nome, b.id) AS destino,
       coalesce(r.evidencias, []) AS evidencias, coalesce(r.trecho_ids, []) AS trecho_ids,
       coalesce(r.anos, []) AS anos, salto
LIMIT $limite"""


# ---------------------------------------------------------------- recuperação
def ordenar_fatos(fatos: list[dict], trechos_busca: list[str]) -> list[dict]:
    """Mais perto da semente primeiro; no empate, o fato que vem dos trechos achados pela busca
    (concorda com o texto) e depois o mais citado no corpus."""
    busca = set(trechos_busca)
    return sorted(fatos, key=lambda f: (f["salto"], -len(busca & set(f["trecho_ids"])),
                                        -len(f["trecho_ids"])))


def recuperar(driver, pergunta: str, qvec: list[float], modo: str = "graphrag", k: int = 8,
              saltos: int = 2, max_fatos: int = 30, max_sementes: int = 8) -> dict:
    """Devolve {'trechos': [...], 'fatos': [...], 'meta': {trecho_id: {...}}, 'debug': {...}}."""
    if modo not in MODOS:
        raise ValueError(f"modo desconhecido: {modo}")
    debug: dict = {}
    vec_ids = [r["id"] for r in read(driver, Q_VETOR_TRECHO, k=k * 2, vec=qvec)]
    debug["vetor"] = vec_ids
    if modo == "vetorial":
        ids = vec_ids[:k]
        origem = {i: "busca" for i in ids}
        fatos: list[dict] = []
    else:
        q = lucene_query(pergunta)
        ft_ids = [r["id"] for r in _read_ft(driver, Q_FT_TRECHO, q=q, k=k * 2)] if q else []
        debug["fulltext"] = ft_ids
        # radical raro (aparece em poucos trechos) vira uma lista própria no RRF, como um IDF:
        # "plagi*" acha o artigo certo; "prazo*" casa com metade do corpus e não ajuda
        listas_radicais = {}
        for rad in radicais(pergunta):
            achados = [r["id"] for r in _read_ft(driver, Q_FT_TRECHO, q=rad, k=k * 2)]
            if 0 < len(achados) < k * 2:
                listas_radicais[rad] = achados
        debug["radicais_raros"] = list(listas_radicais)
        ids = [i for i, _ in rrf(vec_ids, ft_ids, *listas_radicais.values())][:k]
        if radicais(pergunta):
            q = " ".join([q or ""] + radicais(pergunta)).strip()     # sementes também pelo radical
        origem = {i: "busca" for i in ids}
        fatos = []
        if modo == "graphrag":
            # artigos citados pelos trechos achados ("nos termos do art. 15")
            for r in read(driver, Q_CITADOS, ids=ids, k=3):
                origem.setdefault(r["id"], "citado")
            # sementes do multi-hop
            ft_e = [r["eid"] for r in _read_ft(driver, Q_SEMENTES_FT, q=q, k=max_sementes)] if q else []
            vec_e = [r["eid"] for r in read(driver, Q_SEMENTES_VETOR, k=3, vec=qvec)]
            tr_e = [r["eid"] for r in read(driver, Q_SEMENTES_TRECHO, ids=ids, k=max_sementes)]
            sementes = [e for e, _ in rrf(ft_e, vec_e, tr_e)][:max_sementes]
            debug["sementes"] = len(sementes)
            if sementes:
                brutos = read(driver, q_multihop(saltos), eids=sementes, limite=max_fatos * 6)
                fatos = ordenar_fatos(brutos, ids)[:max_fatos]
            # trechos de onde saíram os fatos e que a busca não trouxe (fecha a evidência)
            extra = Counter(t for f in fatos for t in f["trecho_ids"] if t not in origem)
            for t, _ in extra.most_common(3):
                origem[t] = "fato"
    todos = list(origem)
    por_id = {r["id"]: r for r in read(driver, Q_TRECHOS, ids=todos)}
    trechos = [{**por_id[i], "origem": origem[i]} for i in todos if i in por_id]
    # rótulos legíveis das fontes de cada fato (sem trazer o texto inteiro)
    ids_fatos = sorted({t for f in fatos for t in f["trecho_ids"]} - set(por_id))
    meta = {i: {k_: v for k_, v in r.items() if k_ != "texto"} for i, r in por_id.items()}
    if ids_fatos:
        meta.update({r["id"]: {k_: v for k_, v in r.items() if k_ != "texto"}
                     for r in read(driver, Q_TRECHOS, ids=ids_fatos)})
    return {"trechos": trechos, "fatos": fatos, "meta": meta, "debug": debug}
