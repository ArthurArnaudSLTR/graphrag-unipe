"""Fase 3 - geração de Cypher pelo LLM (Text2Cypher), com travas de segurança.

O LLM recebe o schema do grafo e escreve UMA consulta de leitura. Antes de executar:
  * bloqueia palavras de escrita/administração (CREATE, MERGE, DELETE, SET, CALL...);
  * aceita só rótulos e relações que existem no modelo (pega "alucinação de schema");
  * força LIMIT e executa numa sessão READ com timeout (o próprio servidor recusa escrita).
Se a consulta falhar, o erro volta ao LLM UMA vez para correção (custa 1 chamada a mais).
"""
import re

from neo4j.exceptions import Neo4jError
from pydantic import BaseModel

from .extraction import NODE_TYPES, REL_TYPES
from .neo4j_io import PROPS
from .retrieval import read

MAX_LINHAS = 25
TIMEOUT_S = 10
ROTULOS = set(NODE_TYPES) | {"Trecho", "Documento"}
RELACOES = set(REL_TYPES) | {"PARTE_DE", "MENCIONADO_EM", "REFERE_SE_A"}

_PROIBIDAS = re.compile(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD|FOREACH|CALL|USE|"
                        r"GRANT|DENY|REVOKE|ALTER|RENAME|START|STOP|TERMINATE|SHOW|IMPORT)\b", re.I)
_STRINGS = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
_COMENTARIOS = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_NO = re.compile(r"\(\s*\w*\s*((?::\s*`?\w+`?\s*)+)")                  # (n:Rotulo) ou (:A:B)
_REL = re.compile(r"\[\s*\w*\s*:\s*(`?\w+`?(?:\s*\|\s*:?\s*`?\w+`?)*)")  # [r:TIPO|OUTRO*1..2]
_LIMIT = re.compile(r"\bLIMIT\s+(\d+)", re.I)


class CypherInvalido(ValueError):
    pass


class ConsultaCypher(BaseModel):
    cypher: str        # "" quando a pergunta não se responde com uma consulta estruturada
    explicacao: str


def _schema_texto() -> str:
    nos = [f"  (:{t} {{id, {', '.join(p for p in PROPS[t] if p != 'embedding')}}})  -- {d.split('(')[0].strip()}"
           for t, d in NODE_TYPES.items()]
    nos += ["  (:Trecho {id, documento_id, ref, secao, pagina, texto, ano})  -- artigo ou página de um documento",
            "  (:Documento {id, titulo, tipo, numero, data, ano})"]
    rels = [f"  ({'|'.join(sorted(s))})-[:{k}]->({'|'.join(sorted(t))})  -- {d}" for k, (s, t, d) in REL_TYPES.items()]
    rels += ["  (qualquer entidade)-[:MENCIONADO_EM]->(:Trecho)",
             "  (:Trecho)-[:PARTE_DE]->(:Documento)",
             "  (:Trecho)-[:REFERE_SE_A {citacao}]->(:Trecho)  -- artigo que cita outro artigo"]
    return "NÓS:\n" + "\n".join(nos) + "\n\nRELAÇÕES:\n" + "\n".join(rels) + (
        "\n\nAs relações semânticas têm as propriedades trecho_ids, anos e evidencias (listas alinhadas)."
        "\nDocumento.id: codigo_disciplinar, manual_aluno, regulamento_aproveitamento."
        "\nInfracao.gravidade: leve, media, grave, gravissima. Prazo: valor (número), unidade, tipo_dia (uteis|corridos).")


SYSTEM = f"""Você traduz perguntas sobre normas acadêmicas do UNIPÊ em UMA consulta Cypher de LEITURA (Neo4j 5).

{_schema_texto()}

REGRAS:
1. Use SOMENTE os rótulos, relações e propriedades acima. Nunca invente outros.
2. Só leitura: MATCH, OPTIONAL MATCH, WHERE, WITH, RETURN, ORDER BY, LIMIT, UNWIND. Nada de CALL.
3. Para achar por nome, compare sem caixa: toLower(n.nome) CONTAINS 'monitoria' (termos curtos, sem acento
   quando houver dúvida; também procure em n.aliases). Nunca compare `id` com texto da pergunta.
4. Devolva propriedades (n.nome, r.evidencias, t.ref), nunca nós inteiros, e NUNCA a propriedade embedding.
5. Sempre inclua de onde veio a informação: r.trecho_ids ou t.ref/t.documento_id.
6. Termine com LIMIT {MAX_LINHAS} ou menos.
7. Se a pergunta não se responde pela estrutura do grafo (ex.: pede explicação longa), devolva cypher "".

EXEMPLOS
Pergunta: Quais penalidades cabem para plágio?
cypher: MATCH (i:Infracao)-[r:PUNIVEL_COM]->(p:Penalidade) WHERE toLower(i.nome) CONTAINS 'plágio' OR any(a IN coalesce(i.aliases, []) WHERE toLower(a) CONTAINS 'plagio') RETURN i.nome AS infracao, i.gravidade AS gravidade, p.nome AS penalidade, r.evidencias AS evidencias, r.trecho_ids AS fontes LIMIT 25

Pergunta: Quem decide o recurso e em quanto tempo?
cypher: MATCH (p:Processo) WHERE toLower(p.nome) CONTAINS 'recurso' OPTIONAL MATCH (p)-[d:DECIDIDO_POR]->(o:Orgao) OPTIONAL MATCH (p)-[t:TEM_PRAZO]->(z:Prazo) RETURN p.nome AS processo, o.nome AS orgao, z.nome AS prazo, d.trecho_ids AS fonte_orgao, t.trecho_ids AS fonte_prazo LIMIT 25

Pergunta: O que uma penalidade grave afeta e o que esses programas exigem?
cypher: MATCH (pen:Penalidade)-[a:AFETA]->(proc:Processo) OPTIONAL MATCH (proc)-[q:REQUER]->(r:Regra) RETURN pen.nome AS penalidade, proc.nome AS processo, collect(DISTINCT r.nome) AS requisitos, a.trecho_ids AS fontes LIMIT 25
"""


def validar_cypher(texto: str) -> str:
    """Devolve a consulta pronta para executar ou levanta CypherInvalido (a mensagem volta ao LLM)."""
    q = re.sub(r"^```(?:cypher)?\s*|\s*```$", "", texto.strip(), flags=re.I).strip().rstrip(";").strip()
    if not q:
        raise CypherInvalido("consulta vazia")
    corpo = _COMENTARIOS.sub(" ", _STRINGS.sub("''", q))       # palavras dentro de strings não contam
    if ";" in corpo:
        raise CypherInvalido("apenas UMA consulta é permitida")
    m = _PROIBIDAS.search(corpo)
    if m:
        raise CypherInvalido(f"palavra não permitida (só leitura): {m.group(1).upper()}")
    if not re.search(r"\bRETURN\b", corpo, re.I):
        raise CypherInvalido("a consulta precisa de RETURN")
    rotulos = {r.strip("` ") for g in _NO.findall(corpo) for r in g.split(":") if r.strip("` ")}
    if rotulos - ROTULOS:
        raise CypherInvalido(f"rótulo inexistente no schema: {', '.join(sorted(rotulos - ROTULOS))}")
    tipos = {t.strip("`: ") for g in _REL.findall(corpo) for t in g.split("|") if t.strip("`: ")}
    if tipos - RELACOES:
        raise CypherInvalido(f"relação inexistente no schema: {', '.join(sorted(tipos - RELACOES))}")
    if _LIMIT.search(corpo):
        q = _LIMIT.sub(lambda m_: f"LIMIT {min(int(m_.group(1)), MAX_LINHAS)}", q)
    else:
        q += f"\nLIMIT {MAX_LINHAS}"
    return q


def _limpar(v):
    """Tira vetores de embedding e encurta textos longos das linhas devolvidas."""
    if isinstance(v, dict):
        return {k: _limpar(x) for k, x in v.items() if k != "embedding"}
    if isinstance(v, list):
        return [_limpar(x) for x in v]
    if isinstance(v, str) and len(v) > 600:
        return v[:600] + "..."
    return v


def executar(driver, cypher: str) -> list[dict]:
    return [_limpar(r) for r in read(driver, cypher, timeout=TIMEOUT_S)][:MAX_LINHAS]


def gerar_cypher(pergunta: str, driver, gerar=None, tentativas: int = 2) -> dict:
    """Pergunta -> {'cypher', 'explicacao', 'linhas', 'erro'}. `gerar` permite injetar um LLM falso."""
    if gerar is None:
        from .gemini_client import generate_json as gerar   # import tardio: testes não exigem API
    prompt, erro, resp = f"PERGUNTA: {pergunta}", None, None
    for _ in range(tentativas):
        resp = gerar(prompt, ConsultaCypher, SYSTEM)
        if not resp.cypher.strip():
            return {"cypher": "", "explicacao": resp.explicacao, "linhas": [], "erro": None}
        try:
            q = validar_cypher(resp.cypher)
            return {"cypher": q, "explicacao": resp.explicacao, "linhas": executar(driver, q), "erro": None}
        except (CypherInvalido, Neo4jError) as exc:
            erro = str(exc)[:400]
            prompt = (f"PERGUNTA: {pergunta}\n\nSua consulta anterior:\n{resp.cypher}\n\n"
                      f"falhou com o erro: {erro}\nCorrija, seguindo as regras.")
    return {"cypher": resp.cypher if resp else "", "explicacao": "", "linhas": [], "erro": erro}
