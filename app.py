"""Fase 4 - interface gráfica do GraphRAG UNIPÊ (Streamlit).

    streamlit run app.py

Abas:
  Perguntar  - pergunta em linguagem natural, escolha do modo (vetorial / híbrido / graphrag),
               resposta verificada com fontes, afirmações descartadas, Cypher gerado e contexto.
  Explorar   - busca uma entidade do grafo e mostra a vizinhança (até 2 saltos) em diagrama e tabela.
  Grafo      - contagens de nós e relações no Neo4j.
  Avaliação  - resultados de `run_phase3.py eval` (data/processed/phase3_eval.json), por modo.
"""
import json

import streamlit as st

from src.config import PROCESSED_DIR
from src.retrieval import MODOS

st.set_page_config(page_title="GraphRAG UNIPÊ", page_icon="🎓", layout="wide")

NOMES_DOC = {"codigo_disciplinar": "Código Disciplinar", "manual_aluno": "Manual do Aluno",
             "regulamento_aproveitamento": "Regulamento de Aproveitamento de Estudos"}
DESC_MODO = {"vetorial": "só busca vetorial nos trechos (linha de base)",
             "hibrido": "vetor + fulltext (RRF), sem grafo",
             "graphrag": "híbrido + entidades do grafo + multi-hop + Text2Cypher"}
CORES = {"Processo": "#4C78A8", "Regra": "#72B7B2", "Infracao": "#E45756", "Penalidade": "#F58518",
         "Orgao": "#54A24B", "Prazo": "#B279A2", "DocumentoExigido": "#9D755D"}
EXEMPLOS = ["Quem julga o recurso contra uma penalidade disciplinar?",
            "Quantas horas posso faltar numa disciplina de 60h/a?",
            "Se eu for suspenso por 10 dias, isso conta como falta?",
            "Quais documentos preciso para pedir aproveitamento de estudos?"]


# ---------------------------------------------------------------- recursos (cache da sessão)
@st.cache_resource(show_spinner="Conectando ao Neo4j...")
def driver():
    from src.neo4j_io import get_driver
    return get_driver()


@st.cache_data
def textos_trechos() -> dict[str, dict]:
    p = PROCESSED_DIR / "chunks.json"
    return {c["id"]: c for c in json.loads(p.read_text(encoding="utf-8"))} if p.exists() else {}


def consulta(cypher: str, **params) -> list[dict]:
    from src.config import NEO4J_DATABASE
    return [r.data() for r in driver().execute_query(cypher, database_=NEO4J_DATABASE, **params).records]


# ---------------------------------------------------------------- aba: perguntar
def aba_perguntar():
    with st.sidebar:
        st.header("Configuração")
        modo = st.radio("Modo de recuperação", MODOS, index=MODOS.index("graphrag"),
                        format_func=lambda m: m, captions=[DESC_MODO[m] for m in MODOS])
        usar_cypher = st.toggle("Gerar Cypher com o LLM (Text2Cypher)", value=True,
                                disabled=modo != "graphrag")
        k = st.slider("Trechos recuperados (k)", 3, 15, 8)
        saltos = st.slider("Saltos no grafo (multi-hop)", 1, 3, 2, disabled=modo != "graphrag")
        st.caption("Cada pergunta consome 2 a 3 chamadas ao Gemini.")

    st.subheader("Pergunte sobre as normas acadêmicas do UNIPÊ")
    st.caption("Base: " + ", ".join(NOMES_DOC.values()) + ".")
    cols = st.columns(len(EXEMPLOS))
    for c, ex in zip(cols, EXEMPLOS):
        if c.button(ex, width="stretch"):
            st.session_state["pergunta"] = ex

    with st.form("form_pergunta"):
        pergunta = st.text_area("Pergunta", key="pergunta", height=80,
                                placeholder="Ex.: Qual o prazo para recorrer de uma suspensão?")
        enviar = st.form_submit_button("Responder", type="primary")
    if enviar and pergunta.strip():
        from src.graphrag import responder
        with st.spinner(f"Recuperando e gerando a resposta (modo {modo})..."):
            try:
                st.session_state["resultado"] = responder(pergunta.strip(), driver(), modo=modo,
                                                          usar_cypher=usar_cypher, k=k, saltos=saltos)
            except Exception as exc:
                st.session_state.pop("resultado", None)
                st.error(f"Falha ao responder: {exc}")
    if "resultado" in st.session_state:
        mostrar_resultado(st.session_state["resultado"])


def mostrar_resultado(r: dict):
    st.divider()
    st.markdown(f"#### Resposta  ·  modo `{r['modo']}`")
    if r["aprovadas"]:
        for a in r["aprovadas"]:
            st.markdown(f"- {a['texto']}  `{' '.join(a['fontes'])}`")
    else:
        st.info(r["resposta"])
    if r["conflitos"]:
        st.warning(f"**Divergência entre fontes:** {r['conflitos']}")
    if r["aprovadas"] and not r["suficiente"]:
        st.caption("⚠️ O contexto não bastou para responder a pergunta inteira.")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Taxa de suporte", "—" if r["taxa_suporte"] is None else f"{r['taxa_suporte']:.0%}")
    m2.metric("Afirmações descartadas", len(r["removidas"]))
    m3.metric("Fatos do grafo", r["n_fatos"])
    m4.metric("Tempo total", f"{r['tempo_s']['total']} s")

    if r["removidas"]:
        with st.expander(f"Afirmações descartadas pela verificação ({len(r['removidas'])})"):
            for a in r["removidas"]:
                st.markdown(f"- ~~{a['texto']}~~  \n  _{a['motivo']}_")

    textos = textos_trechos()
    with st.expander(f"Fontes ({len(r['trechos'])} trechos)", expanded=True):
        for t in r["trechos"]:
            doc = NOMES_DOC.get(t["documento_id"], t["documento_id"])
            st.markdown(f"**[{t['fonte']}] {doc} ({t['ano']}) — {t['ref']}**  ·  _{t['origem']}_")
            txt = textos.get(t["id"], {}).get("text", "")
            if txt:
                st.caption(txt[:600] + ("…" if len(txt) > 600 else ""))

    cy = r["cypher"]
    if cy["cypher"] or cy["erro"]:
        with st.expander(f"Cypher gerado ({len(cy['linhas'])} linhas)"):
            if cy["cypher"]:
                st.code(cy["cypher"], language="cypher")
            if cy["explicacao"]:
                st.caption(cy["explicacao"])
            if cy["erro"]:
                st.error(f"Cypher descartado: {cy['erro']}")
            if cy["linhas"]:
                st.dataframe(cy["linhas"], width="stretch")
    with st.expander("Contexto enviado ao LLM"):
        st.text(r["contexto"] or "(vazio)")
    with st.expander("Tempos por etapa (s)"):
        st.json(r["tempo_s"])


# ---------------------------------------------------------------- aba: explorar
Q_BUSCA = """MATCH (e) WHERE e.fonte = 'fase2' AND e.nome IS NOT NULL AND NOT e:Trecho
  AND toLower(e.nome) CONTAINS toLower($q)
RETURN e.id AS id, e.nome AS nome, labels(e)[0] AS tipo ORDER BY size(e.nome) LIMIT 30"""
Q_VIZINHOS = """MATCH p = (e {id: $id})-[:PUNIVEL_COM|AFETA|REQUER|EXIGE|TEM_PRAZO|DECIDIDO_POR|PRECEDE*1..2]-()
UNWIND relationships(p) AS r
WITH DISTINCT r, startNode(r) AS a, endNode(r) AS b
RETURN a.id AS a_id, coalesce(a.nome, a.descricao) AS a_nome, labels(a)[0] AS a_tipo, type(r) AS rel,
       b.id AS b_id, coalesce(b.nome, b.descricao) AS b_nome, labels(b)[0] AS b_tipo LIMIT 80"""
Q_MENCOES = """MATCH (e {id: $id})-[:MENCIONADO_EM]->(t:Trecho)
RETURN t.id AS id, t.documento_id AS documento, t.secao AS secao ORDER BY t.documento_id, t.id"""


def _dot(id_central: str, arestas: list[dict]) -> str:
    def esc(s):
        return str(s or "?").replace('"', "'")
    nos, linhas = {}, []
    for a in arestas:
        nos[a["a_id"]] = (a["a_nome"], a["a_tipo"])
        nos[a["b_id"]] = (a["b_nome"], a["b_tipo"])
        linhas.append(f'"{esc(a["a_id"])}" -> "{esc(a["b_id"])}" [label="{a["rel"]}"];')
    defs = []
    for nid, (nome, tipo) in nos.items():
        borda = ', penwidth=3' if nid == id_central else ''
        defs.append(f'"{esc(nid)}" [label="{esc(nome)}\\n({tipo})", style=filled, fillcolor="{CORES.get(tipo, "#BBBBBB")}", '
                    f'fontcolor=white{borda}];')
    return ("digraph G { rankdir=LR; bgcolor=transparent; node [shape=box, style=rounded, fontname=Helvetica, fontsize=10]; "
            "edge [fontname=Helvetica, fontsize=8, color=gray50, fontcolor=gray40]; " + " ".join(defs + linhas) + " }")


def aba_explorar():
    st.subheader("Explorar o grafo de conhecimento")
    q = st.text_input("Buscar entidade pelo nome", placeholder="Ex.: suspensão, recurso, Consepe, frequência")
    if not q.strip():
        st.caption("Digite parte do nome de um processo, regra, infração, penalidade, órgão, prazo ou documento.")
        return
    achados = consulta(Q_BUSCA, q=q.strip())
    if not achados:
        st.info("Nenhuma entidade encontrada.")
        return
    escolha = st.selectbox("Entidade", achados, format_func=lambda e: f"{e['nome']}  ({e['tipo']})")
    arestas = consulta(Q_VIZINHOS, id=escolha["id"])
    if arestas:
        st.graphviz_chart(_dot(escolha["id"], arestas), width="stretch")
        st.dataframe([{"origem": a["a_nome"], "relação": a["rel"], "destino": a["b_nome"]} for a in arestas],
                     width="stretch", hide_index=True)
    else:
        st.info("Esta entidade não tem relações semânticas no grafo.")
    mencoes = consulta(Q_MENCOES, id=escolha["id"])
    if mencoes:
        textos = textos_trechos()
        with st.expander(f"Trechos onde aparece ({len(mencoes)})"):
            for m in mencoes:
                st.markdown(f"**{NOMES_DOC.get(m['documento'], m['documento'])} — {textos.get(m['id'], {}).get('ref', m['secao'])}**")
                st.caption(textos.get(m["id"], {}).get("text", "")[:500])


# ---------------------------------------------------------------- aba: grafo
def aba_grafo():
    st.subheader("Estado do grafo no Neo4j")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Nós por rótulo**")
        st.dataframe(consulta("MATCH (n) UNWIND labels(n) AS rotulo RETURN rotulo, count(*) AS total ORDER BY total DESC"),
                     width="stretch", hide_index=True)
    with c2:
        st.markdown("**Relações por tipo**")
        st.dataframe(consulta("MATCH ()-[r]->() RETURN type(r) AS tipo, count(*) AS total ORDER BY total DESC"),
                     width="stretch", hide_index=True)
    rel = PROCESSED_DIR / "resolution_report.json"
    if rel.exists():
        r = json.loads(rel.read_text(encoding="utf-8"))
        st.markdown("**Resolução de entidades**")
        a, b, c = st.columns(3)
        a.metric("Menções brutas", r.get("mencoes_brutas"))
        b.metric("Nós únicos", r.get("nos_unicos"))
        if r.get("mencoes_brutas"):
            c.metric("Redução", f"{100 * (1 - r['nos_unicos'] / r['mencoes_brutas']):.1f}%")


# ---------------------------------------------------------------- aba: avaliação
def aba_avaliacao():
    st.subheader("Avaliação: vetorial × híbrido × graphrag")
    p = PROCESSED_DIR / "phase3_eval.json"
    if not p.exists():
        st.info("Ainda não há resultados. Rode `python run_phase3.py eval`.")
        return
    res = list(json.loads(p.read_text(encoding="utf-8")).values())

    def media(xs):
        xs = [x for x in xs if x is not None]
        return round(sum(xs) / len(xs), 2) if xs else None
    resumo = []
    for modo in MODOS:
        rs = [r for r in res if r["modo"] == modo]
        if rs:
            resumo.append({"modo": modo, "perguntas": len(rs),
                           "suporte médio": media([r["taxa_suporte"] for r in rs]),
                           "cobertura de documentos": media([r.get("cobertura_docs") for r in rs]),
                           "descartadas (total)": sum(len(r["removidas"]) for r in rs),
                           "tempo médio (s)": media([r["tempo_s"]["total"] for r in rs])})
    st.dataframe(resumo, width="stretch", hide_index=True)
    st.caption("Métricas automáticas. As notas manuais (correta/completa/alucinação) ficam em "
               "data/processed/phase3_eval.csv e saem em `python run_phase3.py metrics`.")
    for r in sorted(res, key=lambda x: (x["id"], MODOS.index(x["modo"]))):
        with st.expander(f"#{r['id']} [{r['modo']}] {r['pergunta']}"):
            st.markdown(f"**Esperada:** {r['esperada']}")
            st.markdown(f"**Obtida:** {r['resposta']}")
            st.caption(f"suporte={r['taxa_suporte']} · docs={r.get('cobertura_docs')} · "
                       f"descartadas={len(r['removidas'])} · {r['tempo_s']['total']} s")


# ---------------------------------------------------------------- main
st.title("🎓 GraphRAG UNIPÊ")
st.caption("Assistente de normas acadêmicas sobre grafo de conhecimento (Neo4j + Gemini)")
try:
    driver()
except Exception as exc:
    st.error(f"Não foi possível conectar ao Neo4j. Confira o .env. Detalhe: {exc}")
    st.stop()

t1, t2, t3, t4 = st.tabs(["💬 Perguntar", "🔎 Explorar", "📊 Grafo", "🧪 Avaliação"])
with t1:
    aba_perguntar()
with t2:
    aba_explorar()
with t3:
    aba_grafo()
with t4:
    aba_avaliacao()
