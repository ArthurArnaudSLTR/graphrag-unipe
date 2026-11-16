"""Fase 3 - geração da resposta com redução de alucinações.

Três travas:
  1. Contexto numerado: [T1] trechos (com documento e ano), [F1] fatos do grafo (com evidência literal),
     [C1] linhas do Cypher. O LLM só pode usar isso e precisa citar a fonte de CADA afirmação.
  2. Saída estruturada em afirmações (texto + fontes), não um texto livre.
  3. Verificação determinística depois do LLM: descarta afirmação sem fonte válida e afirmação com
     número (prazo, percentual, nota, artigo) que não aparece nas fontes citadas. A resposta final é
     montada SÓ com as afirmações aprovadas; as descartadas ficam registradas para a avaliação.
"""
import json
import re

from pydantic import BaseModel

SEM_RESPOSTA = ("Não encontrei base suficiente nos documentos indexados (Código Disciplinar, Manual do "
                "Aluno 2026 e Regulamento de Aproveitamento de Estudos) para responder com segurança.")


class Afirmacao(BaseModel):
    texto: str
    fontes: list[str]      # ids do contexto: "T1", "F3", "C2"


class Resposta(BaseModel):
    afirmacoes: list[Afirmacao]
    suficiente: bool       # o contexto bastou para responder a pergunta inteira?
    conflitos: str         # divergência entre fontes/anos ("" se não houver)


SYSTEM = """Você é o assistente de normas acadêmicas do Centro Universitário UNIPÊ.
Responda à pergunta do aluno usando EXCLUSIVAMENTE o CONTEXTO fornecido.

REGRAS:
1. Divida a resposta em afirmações curtas e objetivas, na ordem em que devem ser lidas.
2. Cada afirmação cita em `fontes` os ids do contexto que a sustentam (ex.: ["T2", "F5"]).
   Afirmação sem fonte no contexto NÃO pode ser escrita.
3. Copie números exatamente como estão nas fontes (prazos, percentuais, notas, artigos).
4. Nada de conhecimento externo, suposição ou conselho que não esteja no contexto.
5. Se o contexto não basta para a pergunta inteira, responda só o que tiver base e marque suficiente=false.
   Se não houver nada, devolva afirmacoes=[] e suficiente=false.
6. Se fontes de documentos ou anos diferentes divergirem, descreva a divergência em `conflitos`,
   citando os dois documentos e anos, e não escolha um lado.
7. Escreva em português, falando diretamente com o aluno."""


# ---------------------------------------------------------------- contexto
def rotulo_trecho(m: dict) -> str:
    return f"{m.get('documento') or m.get('documento_id')} ({m.get('ano')}), {m.get('ref')}"


def montar_contexto(trechos: list[dict], fatos: list[dict], linhas_cypher: list[dict],
                    meta: dict | None = None, max_chars_trecho: int = 1800) -> tuple[str, dict[str, str]]:
    """Devolve (texto do contexto para o prompt, {id_fonte: texto usado na verificação})."""
    meta = meta or {}
    blocos, fontes = [], {}
    for i, t in enumerate(trechos, 1):
        fid, cab = f"T{i}", rotulo_trecho(t)
        texto = t["texto"] if len(t["texto"]) <= max_chars_trecho else t["texto"][:max_chars_trecho] + "..."
        blocos.append(f"[{fid}] {cab}\n{texto}")
        fontes[fid] = f"{cab}\n{texto}"
    for i, f in enumerate(fatos, 1):
        fid = f"F{i}"
        refs = "; ".join(rotulo_trecho(meta[t]) if t in meta else t for t in f["trecho_ids"][:3])
        ev = " | ".join(f'"{e}"' for e in f["evidencias"][:2])
        linha = (f"({f['tipo_origem']}) {f['origem']} -{f['relacao']}-> ({f['tipo_destino']}) {f['destino']}"
                 f" | evidência: {ev} | fonte: {refs}")
        blocos.append(f"[{fid}] {linha}")
        fontes[fid] = linha
    for i, r in enumerate(linhas_cypher, 1):
        fid, linha = f"C{i}", json.dumps(r, ensure_ascii=False, default=str)
        blocos.append(f"[{fid}] {linha}")
        fontes[fid] = linha
    return "\n\n".join(blocos), fontes


def montar_prompt(pergunta: str, contexto: str) -> str:
    return f"CONTEXTO:\n{contexto or '(vazio)'}\n\nPERGUNTA DO ALUNO: {pergunta}"


# ---------------------------------------------------------------- verificação de números
_UNIDADES = ["zero", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez",
             "onze", "doze", "treze", "quatorze", "quinze", "dezesseis", "dezessete", "dezoito", "dezenove"]
_DEZENAS = {20: "vinte", 30: "trinta", 40: "quarenta", 50: "cinquenta", 60: "sessenta",
            70: "setenta", 80: "oitenta", 90: "noventa"}
_ORDINAIS = ["primeir", "segund", "terceir", "quart", "quint", "sext", "sétim", "oitav", "non", "décim"]


def _extenso(n: int) -> list[str]:
    if n < 20:
        base = [_UNIDADES[n]] + (["uma"] if n == 1 else ["duas"] if n == 2 else ["catorze"] if n == 14 else [])
        return base
    if n == 100:
        return ["cem"]
    d, u = divmod(n, 10)
    return [_DEZENAS[d * 10]] if u == 0 else [f"{_DEZENAS[d * 10]} e {w}" for w in _extenso(u)]


_POR_EXTENSO = sorted(((w, n) for n in range(0, 101) for w in _extenso(n)), key=lambda x: -len(x[0]))
_RE_EXTENSO = re.compile(r"\b(" + "|".join(re.escape(w) for w, _ in _POR_EXTENSO) + r")\b")
_RE_ORDINAL = re.compile(r"\b(" + "|".join(_ORDINAIS) + r")[oa]s?\b")
_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _norm_num(s: str) -> str:
    x = float(s.replace(",", "."))
    return str(int(x)) if x.is_integer() else str(x)


def numeros(texto: str, extenso: bool = False) -> set[str]:
    """Números do texto, normalizados ("6,0" = "6"). Com extenso=True também lê "cinco", "setenta e
    cinco", "segunda" (as fontes jurídicas escrevem assim; a resposta costuma usar algarismos)."""
    nums = {_norm_num(m) for m in _NUM.findall(texto)}
    if extenso:
        baixo = texto.lower()
        mapa = dict(_POR_EXTENSO)
        nums |= {str(mapa[m]) for m in _RE_EXTENSO.findall(baixo)}
        nums |= {str(_ORDINAIS.index(m) + 1) for m in _RE_ORDINAL.findall(baixo)}
    return nums


# ---------------------------------------------------------------- verificação da resposta
def _id_fonte(s: str) -> str:
    return s.strip().strip("[]").strip().upper()


def verificar(resp: Resposta, fontes: dict[str, str]) -> dict:
    """Aplica as travas determinísticas e monta a resposta final só com o que tem base."""
    aprovadas, removidas = [], []
    for a in resp.afirmacoes:
        citadas = [_id_fonte(f) for f in a.fontes]
        validas = list(dict.fromkeys(f for f in citadas if f in fontes))
        if not validas:
            removidas.append({"texto": a.texto, "fontes": a.fontes, "motivo": "sem fonte válida no contexto"})
            continue
        base = numeros(" ".join(fontes[f] for f in validas), extenso=True)
        faltam = sorted(numeros(a.texto) - base)
        if faltam:
            removidas.append({"texto": a.texto, "fontes": validas,
                              "motivo": f"número(s) {', '.join(faltam)} não aparece(m) nas fontes citadas"})
            continue
        aprovadas.append({"texto": a.texto, "fontes": validas})
    total = len(resp.afirmacoes)
    if aprovadas:
        linhas = [f"- {a['texto']} [{', '.join(a['fontes'])}]" for a in aprovadas]
        if resp.conflitos.strip():
            linhas.append(f"\nAtenção, as fontes divergem: {resp.conflitos.strip()}")
        if not resp.suficiente or removidas:
            linhas.append("\n(Resposta parcial: parte da pergunta não teve base suficiente nos documentos.)")
        texto = "\n".join(linhas)
    else:
        texto = SEM_RESPOSTA
    return {"resposta": texto, "aprovadas": aprovadas, "removidas": removidas,
            "suficiente": resp.suficiente and not removidas and bool(aprovadas),
            "conflitos": resp.conflitos.strip(),
            "taxa_suporte": round(len(aprovadas) / total, 3) if total else None}
