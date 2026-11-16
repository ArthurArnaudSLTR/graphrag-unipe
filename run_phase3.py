"""Orquestrador da Fase 3 (Algoritmos e GraphRAG/KAG). Requer a Fase 2 concluída (grafo no Neo4j).

    python run_phase3.py ask "Quem julga o recurso contra uma penalidade?"   # pergunta completa
    python run_phase3.py ask "..." --modo vetorial                           #   só busca vetorial (linha de base)
    python run_phase3.py ask "..." --sem-cypher --contexto                   #   sem Text2Cypher, mostrando o contexto
    python run_phase3.py cypher "Quais penalidades cabem para plágio?"       # só a geração de Cypher
    python run_phase3.py eval                                                # perguntas de docs/perguntas_teste.md
    python run_phase3.py eval --ids 5,7,12 --modos vetorial,hibrido,graphrag
    python run_phase3.py metrics                                             # notas do CSV preenchido, por modo
"""
import argparse
import csv
import json
import re

from src.config import ROOT, PROCESSED_DIR
from src.retrieval import MODOS

FONTES_DOC = {"manual": "manual_aluno", "código": "codigo_disciplinar", "codigo": "codigo_disciplinar",
              "regulamento": "regulamento_aproveitamento"}
CSV_EVAL = PROCESSED_DIR / "phase3_eval.csv"


def carregar_perguntas() -> list[dict]:
    """Lê a tabela de docs/perguntas_teste.md: # | Pergunta | Saltos | Fontes | Resposta esperada."""
    out = []
    for linha in (ROOT / "docs" / "perguntas_teste.md").read_text(encoding="utf-8").splitlines():
        cols = [c.strip() for c in linha.strip().strip("|").split("|")]
        if len(cols) >= 5 and cols[0].isdigit():
            docs = sorted({d for k, d in FONTES_DOC.items() if k in cols[3].lower()})
            out.append({"id": int(cols[0]), "pergunta": cols[1], "saltos": cols[2], "fontes": cols[3],
                        "docs_esperados": docs, "esperada": cols[4]})
    return out


def imprimir(r: dict, contexto: bool = False):
    print(f"\n=== [{r['modo']}] {r['pergunta']}\n")
    print(r["resposta"])
    if r["removidas"]:
        print("\nAfirmações descartadas pela verificação:")
        for a in r["removidas"]:
            print(f"  x {a['texto']}  ({a['motivo']})")
    print("\nFontes:")
    for t in r["trechos"]:
        print(f"  {t['fonte']}: {t['documento_id']} ({t['ano']}) {t['ref']}  [{t['origem']}]")
    if r["n_fatos"]:
        print(f"  + {r['n_fatos']} fatos do grafo (multi-hop)")
    if r["cypher"]["cypher"]:
        print(f"\nCypher gerado ({len(r['cypher']['linhas'])} linhas):\n{r['cypher']['cypher']}")
    if r["cypher"]["erro"]:
        print(f"  (Cypher descartado: {r['cypher']['erro']})")
    print(f"\ntaxa de suporte: {r['taxa_suporte']} | tempo: {r['tempo_s']}")
    if contexto:
        print(f"\n--- CONTEXTO ENVIADO AO LLM ---\n{r['contexto']}")


def cmd_ask(a):
    from src.graphrag import responder
    from src.neo4j_io import get_driver
    driver = get_driver()
    try:
        imprimir(responder(a.pergunta, driver, modo=a.modo, usar_cypher=not a.sem_cypher,
                           k=a.k, saltos=a.saltos), a.contexto)
    finally:
        driver.close()


def cmd_cypher(a):
    from src.neo4j_io import get_driver
    from src.text2cypher import gerar_cypher
    driver = get_driver()
    try:
        r = gerar_cypher(a.pergunta, driver)
    finally:
        driver.close()
    print(r["cypher"] or "(o LLM achou que a pergunta não se responde por consulta estruturada)")
    print(f"\n{r['explicacao']}")
    if r["erro"]:
        print(f"\nERRO: {r['erro']}")
    for linha in r["linhas"]:
        print(" ", json.dumps(linha, ensure_ascii=False, default=str))


def cmd_eval(a):
    from src.gemini_client import embed_query
    from src.graphrag import responder
    from src.neo4j_io import get_driver
    perguntas = carregar_perguntas()
    if a.ids:
        ids = {int(x) for x in a.ids.split(",")}
        perguntas = [p for p in perguntas if p["id"] in ids]
    modos = [m.strip() for m in a.modos.split(",")]
    if set(modos) - set(MODOS):
        raise SystemExit(f"modos válidos: {', '.join(MODOS)}")
    path = PROCESSED_DIR / "phase3_eval.json"
    resultados = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    driver = get_driver()
    try:
        for p in perguntas:
            qvec = None
            for modo in modos:
                chave = f"{p['id']}:{modo}"
                if chave in resultados and not a.refazer:      # cache: pode interromper e retomar
                    continue
                qvec = qvec or embed_query(p["pergunta"])
                try:
                    r = responder(p["pergunta"], driver, modo=modo, usar_cypher=not a.sem_cypher, qvec=qvec)
                except Exception as exc:                        # não derruba a avaliação inteira
                    print(f"  [{chave}] FALHOU: {exc}")
                    continue
                docs = {t["documento_id"] for t in r["trechos"]}
                r["cobertura_docs"] = (round(len(docs & set(p["docs_esperados"])) / len(p["docs_esperados"]), 2)
                                       if p["docs_esperados"] else None)
                resultados[chave] = {**r, "id": p["id"], "esperada": p["esperada"]}
                path.write_text(json.dumps(resultados, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"  [{chave}] suporte={r['taxa_suporte']} docs={r['cobertura_docs']} {r['tempo_s']['total']}s")
    finally:
        driver.close()

    with open(CSV_EVAL, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["id", "modo", "pergunta", "esperada", "resposta", "taxa_suporte", "cobertura_docs",
                    "descartadas", "correta (0/1)", "completa (0/1)", "alucinacao (0/1)", "observacao"])
        for chave in sorted(resultados, key=lambda c: (int(c.split(":")[0]), c.split(":")[1])):
            r = resultados[chave]
            w.writerow([r["id"], r["modo"], r["pergunta"], r["esperada"], r["resposta"], r["taxa_suporte"],
                        r["cobertura_docs"], len(r["removidas"]), "", "", "", ""])
    resumo(resultados)
    print(f"\nResultados em {path}\nPlanilha para nota manual em {CSV_EVAL} (preencham as colunas 0/1 e rodem 'metrics').")


def resumo(resultados: dict):
    print("\nmodo        perguntas  suporte médio  cobertura de docs  tempo médio (s)")
    for modo in MODOS:
        rs = [r for r in resultados.values() if r["modo"] == modo]
        if not rs:
            continue

        def media(xs):
            xs = [x for x in xs if x is not None]
            return f"{sum(xs) / len(xs):.2f}" if xs else "n/a"
        print(f"{modo:<11} {len(rs):>9}  {media([r['taxa_suporte'] for r in rs]):>13}  "
              f"{media([r['cobertura_docs'] for r in rs]):>17}  {media([r['tempo_s']['total'] for r in rs]):>15}")


def cmd_metrics(a):
    if not CSV_EVAL.exists():
        raise SystemExit(f"Falta {CSV_EVAL}. Rode 'eval' e preencha o CSV.")
    por_modo: dict[str, dict] = {}
    with open(CSV_EVAL, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter=";"):
            notas = [row["correta (0/1)"].strip(), row["completa (0/1)"].strip(), row["alucinacao (0/1)"].strip()]
            if not all(re.fullmatch(r"[01]", n) for n in notas):
                continue                                         # linha ainda não avaliada
            m = por_modo.setdefault(row["modo"], {"n": 0, "correta": 0, "completa": 0, "alucinacao": 0})
            m["n"] += 1
            for k, v in zip(("correta", "completa", "alucinacao"), notas):
                m[k] += int(v)
    if not por_modo:
        raise SystemExit("Nenhuma linha avaliada ainda.")
    print("modo        avaliadas  corretas  completas  com alucinação")
    for modo, m in por_modo.items():
        print(f"{modo:<11} {m['n']:>9}  {100 * m['correta'] / m['n']:>7.1f}%  {100 * m['completa'] / m['n']:>8.1f}%"
              f"  {100 * m['alucinacao'] / m['n']:>13.1f}%")
    print("Comparem vetorial x hibrido x graphrag: é a tabela principal do artigo na Fase 3.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in [("ask", cmd_ask), ("cypher", cmd_cypher), ("eval", cmd_eval), ("metrics", cmd_metrics)]:
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        if name in ("ask", "cypher"):
            p.add_argument("pergunta")
        if name == "ask":
            p.add_argument("--modo", choices=MODOS, default="graphrag")
            p.add_argument("--k", type=int, default=8, help="trechos vindos da busca")
            p.add_argument("--saltos", type=int, choices=(1, 2, 3), default=2, help="profundidade do multi-hop")
            p.add_argument("--contexto", action="store_true", help="mostra o contexto enviado ao LLM")
        if name in ("ask", "eval"):
            p.add_argument("--sem-cypher", action="store_true", help="pula o Text2Cypher (economiza 1-2 chamadas)")
        if name == "eval":
            p.add_argument("--ids", default=None, help="ex.: 1,5,7 (padrão: todas)")
            p.add_argument("--modos", default="vetorial,graphrag", help=f"entre {', '.join(MODOS)}")
            p.add_argument("--refazer", action="store_true", help="ignora o cache de resultados")
    args = ap.parse_args()
    args.fn(args)
