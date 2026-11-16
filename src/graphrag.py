"""Fase 3 - orquestração de uma pergunta: recuperação -> Text2Cypher -> resposta verificada.

Chamadas à API por pergunta (importa para a cota do plano gratuito):
  1 embedding da pergunta + 1 LLM do Cypher (+1 se precisar corrigir) + 1 LLM da resposta.
  O modo "vetorial"/"hibrido" e a opção usar_cypher=False pulam o Cypher.
"""
import time

from .grounding import SYSTEM, Resposta, montar_contexto, montar_prompt, verificar
from .retrieval import recuperar
from .text2cypher import gerar_cypher


def responder(pergunta: str, driver, modo: str = "graphrag", usar_cypher: bool = True, k: int = 8,
              saltos: int = 2, qvec: list[float] | None = None, gerar=None, embed=None) -> dict:
    """`gerar`/`embed` permitem injetar funções falsas nos testes; `qvec` reaproveita o vetor da
    pergunta quando a mesma pergunta roda em vários modos (avaliação)."""
    if gerar is None or (embed is None and qvec is None):
        from .gemini_client import embed_query, generate_json    # import tardio: testes não exigem API
        gerar, embed = gerar or generate_json, embed or embed_query
    t0 = time.perf_counter()
    qvec = qvec if qvec is not None else embed(pergunta)
    rec = recuperar(driver, pergunta, qvec, modo=modo, k=k, saltos=saltos)
    t1 = time.perf_counter()

    cy = {"cypher": "", "explicacao": "", "linhas": [], "erro": None}
    if usar_cypher and modo == "graphrag":
        cy = gerar_cypher(pergunta, driver, gerar=gerar)
    t2 = time.perf_counter()

    contexto, fontes = montar_contexto(rec["trechos"], rec["fatos"], cy["linhas"], rec["meta"])
    resp = gerar(montar_prompt(pergunta, contexto), Resposta, SYSTEM) if fontes else \
        Resposta(afirmacoes=[], suficiente=False, conflitos="")
    out = verificar(resp, fontes)
    t3 = time.perf_counter()

    return {**out, "pergunta": pergunta, "modo": modo,
            "trechos": [{"fonte": f"T{i}", "id": t["id"], "ref": t["ref"], "documento_id": t["documento_id"],
                         "ano": t["ano"], "origem": t["origem"]} for i, t in enumerate(rec["trechos"], 1)],
            "n_fatos": len(rec["fatos"]), "cypher": cy, "contexto": contexto, "debug": rec["debug"],
            "tempo_s": {"recuperacao": round(t1 - t0, 2), "cypher": round(t2 - t1, 2),
                        "resposta": round(t3 - t2, 2), "total": round(t3 - t0, 2)}}
