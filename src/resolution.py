"""Resolução de entidades: "Coordenadoria de curso" e "Coordenação do curso" são o MESMO nó.

Estratégia em camadas, da mais barata e segura à mais cara e arriscada:
  Camada 1  normalização + dicionário de aliases + prazos canônicos     (determinística)
  Camada 2  similaridade de strings (rapidfuzz) + política `fuzzy_decision`  (determinística)
  Camada 3  LLM como juiz nos pares duvidosos (opcional, `resolve --llm-judge`)

Regra de ouro: só compare entidades do MESMO tipo. Errar para menos (duplicata que sobrou) é
melhor que errar para mais (dois conceitos diferentes fundidos): o segundo erro é invisível.

Convenções da Fase 1 (docs/modelo.md): `id` canônico = minúsculo, sem acento, com "_"
(ex.: trancamento_de_matricula); `nome` = forma legível; `aliases` = variantes encontradas.
"""
import json
import re
import unicodedata
from collections import Counter, defaultdict

from rapidfuzz import fuzz, process

from .config import PROCESSED_DIR
from .extraction import Extraction, GRAVIDADES


# ================================================================ números por extenso e prazos
_UNIDADES = {"zero": 0, "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5,
             "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12,
             "treze": 13, "quatorze": 14, "catorze": 14, "quinze": 15, "dezesseis": 16,
             "dezessete": 17, "dezoito": 18, "dezenove": 19}
_DEZENAS = {"vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50, "sessenta": 60,
            "setenta": 70, "oitenta": 80, "noventa": 90}
_CENTENAS = {"cem": 100, "cento": 100, "duzentos": 200, "trezentos": 300, "quatrocentos": 400,
             "quinhentos": 500}
_NUM_WORDS = {**_UNIDADES, **_DEZENAS, **_CENTENAS}


def _fold(s: str) -> str:
    """minúsculo, sem acento ("ª"/"º" viram "a"/"o"), espaços colapsados."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s).strip()


def words_to_int(tokens: list[str]) -> int | None:
    """['quarenta','e','cinco'] -> 45. Devolve None se algum token não for número."""
    total, seen = 0, False
    for t in tokens:
        if t == "e":
            continue
        if t not in _NUM_WORDS:
            return None
        total += _NUM_WORDS[t]
        seen = True
    return total if seen else None


_NUM_RE = r"(?:\d+|(?:%s)(?:\s+e\s+(?:%s))*)" % ("|".join(_NUM_WORDS), "|".join(_NUM_WORDS))
_UNIT_RE = r"(dias?|semanas?|mes(?:es)?|anos?|horas?|semestres?)"
_PRAZO_RE = re.compile(
    rf"^(?:prazo\s+)?(?:de\s+|ate\s+|em\s+|no\s+maximo\s+|maximo\s+de\s+)?({_NUM_RE})\s+{_UNIT_RE}"
    rf"(?:\s+(uteis|util|corridos|corrido))?$")
_UNIT_PLURAL = {"dia": "dias", "semana": "semanas", "mes": "meses", "ano": "anos",
                "hora": "horas", "semestre": "semestres"}
_UNIT_SING = {v: k for k, v in _UNIT_PLURAL.items()}


def parse_prazo(name: str) -> dict | None:
    """'05 (cinco) dias úteis' / 'cinco dias úteis' / '5 dias úteis' -> {valor:5, unidade:'dias', tipo_dia:'uteis'}.
    Devolve None para o que não é um prazo simples (ex.: 'de 16 a 30 dias', 'semestre seguinte')."""
    s = re.sub(r"\([^)]*\)", " ", _fold(name))       # "05 (cinco) dias" -> "05 dias"
    s = re.sub(r"[.,;:]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    m = _PRAZO_RE.match(s)
    if not m:
        return None
    num, unit, tipo = m.groups()
    valor = int(num) if num.isdigit() else words_to_int(num.split())
    if valor is None:
        return None
    unit = unit if unit in _UNIT_PLURAL.values() else _UNIT_PLURAL.get(unit, unit)
    if unit == "mes":
        unit = "meses"
    tipo_dia = {"uteis": "uteis", "util": "uteis", "corridos": "corridos", "corrido": "corridos"}.get(tipo)
    return {"valor": valor, "unidade": unit, "tipo_dia": tipo_dia}


def prazo_label(p: dict) -> str:
    """{'valor':5,'unidade':'dias','tipo_dia':'uteis'} -> '5 dias úteis'."""
    unit = _UNIT_SING[p["unidade"]] if p["valor"] == 1 else p["unidade"]
    tipo = {"uteis": " útil" if p["valor"] == 1 else " úteis", "corridos": " corridos", None: ""}[p["tipo_dia"]]
    if p["valor"] == 1 and p["tipo_dia"] == "corridos":
        tipo = " corrido"
    return f"{p['valor']} {unit}{tipo}"


# ================================================================ camada 1
_STOP = re.compile(r"\b(o|a|os|as|de|do|da|dos|das)\b")


def normalize(name: str, type_: str | None = None) -> str:
    if type_ == "Prazo":                       # "05 (cinco) dias úteis" -> "5 dias uteis"
        p = parse_prazo(name)
        if p:
            return f"{p['valor']} {p['unidade']}" + (f" {p['tipo_dia']}" if p["tipo_dia"] else "")
    s = _fold(name)
    s = re.sub(r"[^a-z0-9%]+", " ", s)         # pontuação, hífen e parênteses viram espaço
    s = _STOP.sub(" ", s)                      # artigos/preposições não distinguem
    return re.sub(r"\s+", " ", s).strip()


# Chaves e valores JÁ normalizados (sem acento, sem "de/do"), POR TIPO. Cada linha é uma decisão
# de modelagem; as justificativas estão em docs/politica_resolucao.md.
KNOWN_ALIASES: dict[str, dict[str, str]] = {
    "Orgao": {
        "coordenadoria curso": "coordenacao curso",
        "secretaria registros controles academicos": "secretaria controle registro academico",
        "secretaria registro controle academico": "secretaria controle registro academico",
        "consepe": "conselho ensino pesquisa extensao",
        "consu": "conselho universitario",
        "segen": "secretaria geral ensino",
        "snac": "setor nacional analise curricular",
        "proac": "pro reitoria academica",
        "compex": "coordenacao monitoria pesquisa extensao",
        "caa online": "caa on line",
        # Deixados de fora DE PROPÓSITO (decisões do grupo, ver docs/politica_resolucao.md):
        #  "reitoria" x "reitor"  -> órgão x cargo
        #  "coordenador" x "coordenação do curso" -> pessoa x setor
        #  "caa on-line" x "central de atendimento ao aluno" -> o Manual trata como canais distintos
    },
    "Penalidade": {
        "expulsao": "desligamento",            # Manual p.10 diz "expulsão"; o Código só prevê "desligamento"
        "penalidade desligamento": "desligamento",
    },
    "Processo": {
        "pad": "processo administrativo disciplinar",
        "2a chamada": "segunda chamada",
        "af": "avaliacao final",
        "trancamento": "trancamento matricula",
        "revisao retificacao notas": "revisao notas",
    },
}


def canonical_key(name: str, type_: str) -> tuple[str, str]:
    n = normalize(name, type_)
    return type_, KNOWN_ALIASES.get(type_, {}).get(n, n)


# ================================================================ camada 2
_BLOQUEADORES = {"leve", "media", "grave", "gravissima", "gravissimas", "graves", "leves", "medias",
                 "verbal", "escrita", "primeira", "segunda", "terceira", "inicial", "final",
                 "interna", "externa", "obrigatorio", "obrigatoria", "nao"}


def fuzzy_candidates(entities: list[dict], threshold: int = 88) -> list[tuple[str, str, int]]:
    """Pares do mesmo tipo com nomes parecidos que a camada 1 NÃO fundiu: [(id_a, id_b, score)].
    Só SUGERE: quem decide é `fuzzy_decision`."""
    by_type = defaultdict(list)
    for e in entities:
        by_type[e["type"]].append(e)
    pairs = []
    for t, group in by_type.items():
        if t == "Prazo":              # prazos já foram canonizados por valor/unidade
            continue
        names = [normalize(e["nome"], e["type"]) for e in group]
        for i, a in enumerate(names):
            for _, s, j in process.extract(a, names, scorer=fuzz.token_set_ratio,
                                           score_cutoff=threshold, limit=6):
                if j > i:
                    pairs.append((group[i]["id"], group[j]["id"], int(s)))
    return pairs


def _digits(s: str) -> list[str]:
    return sorted(re.findall(r"\d+", s))


def fuzzy_decision(a: dict, b: dict, score: int) -> str:
    """Política de fusão para um par candidato. Devolve:
       "merge"  funde direto (quase idêntico: erro de digitação, plural, ordem das palavras)
       "judge"  duvidoso: só o juiz LLM (camada 3) pode fundir
       "keep"   mantém separado

    Bloqueios (nunca fundem): tipos diferentes; Prazo; números diferentes ("Suspensão" x
    "Suspensão de 16 a 30 dias"); palavras que mudam o sentido (grave/gravíssima, 1ª/2ª, não).
    """
    if a["type"] != b["type"] or a["type"] == "Prazo":
        return "keep"
    na, nb = normalize(a["nome"], a["type"]), normalize(b["nome"], b["type"])
    if _digits(na) != _digits(nb):
        return "keep"
    ta, tb = set(na.split()), set(nb.split())
    if (ta ^ tb) & _BLOQUEADORES:
        return "keep"
    if fuzz.ratio(na, nb) >= 95:
        return "merge"
    return "judge" if score >= 88 else "keep"


def decide_merge(a: dict, b: dict, score: int) -> bool:
    """Atalho booleano: só True quando a política manda fundir sem consultar o LLM."""
    return fuzzy_decision(a, b, score) == "merge"


# ================================================================ camada 3 (juiz LLM)
def _judge_cache_path():
    return PROCESSED_DIR / "judge_cache.json"


def llm_judge(a: dict, b: dict) -> dict:
    """Pergunta ao Gemini se dois nomes são o MESMO conceito normativo. Resultado em cache."""
    from pydantic import BaseModel
    from .gemini_client import generate_json

    class Verdict(BaseModel):
        mesmo_conceito: bool
        motivo: str

    key = "||".join(sorted([a["id"], b["id"]]))
    path = _judge_cache_path()
    cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if key not in cache:
        prompt = (f"Tipo: {a['type']}\nNome A: {a['nome']}\nNome B: {b['nome']}\n"
                  f"Aliases A: {a['aliases']}\nAliases B: {b['aliases']}\n\n"
                  "Em normas acadêmicas, A e B se referem ao MESMO conceito (podem ser trocados em "
                  "qualquer frase sem mudar o sentido)? Se um é mais específico, tem número, grau ou "
                  "condição que o outro não tem, responda false. Na dúvida, false.")
        v = generate_json(prompt, Verdict, "Você decide se duas entidades de um regulamento são a mesma.")
        cache[key] = {"mesmo_conceito": v.mesmo_conceito, "motivo": v.motivo}
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return cache[key]


# ================================================================ orquestração
def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _fold(s)).strip("_")


class _UF:
    """União-busca: junta grupos que as camadas 2/3 mandaram fundir."""
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _build_entities(groups: dict) -> list[dict]:
    """Transforma grupos {chave: {...}} em entidades com id canônico único por tipo."""
    entities, used = [], defaultdict(set)
    for key in sorted(groups, key=lambda k: (k[0], k[1])):       # ordem estável => ids estáveis
        g = groups[key]
        # nome de exibição: prefere a forma que já é a canônica (ex.: "Desligamento" em vez de
        # "Expulsão"), depois a mais frequente, depois a mais curta
        names = sorted(g["names"].items(),
                       key=lambda kv: (normalize(kv[0], g["type"]) != key[1], -kv[1], len(kv[0]), kv[0]))
        nome = names[0][0]
        e = {"type": g["type"], "nome": nome, "aliases": [n for n, _ in names[1:]],
             "descricao": g["descs"].most_common(1)[0][0] if g["descs"] else "",
             "trecho_ids": sorted(g["chunks"]), "chave": key[1]}
        if g["type"] == "Prazo":
            p = g["prazo"]
            if p["valor"] is not None:            # prazo simples: "5 dias úteis"
                e["nome"] = prazo_label(p)
                e["aliases"] = [n for n, _ in names if n != e["nome"]]
            e.update(valor=p["valor"], unidade=p["unidade"], tipo_dia=p["tipo_dia"])
        if g["type"] == "Infracao":
            gr = Counter(g["grav"])
            e["gravidade"] = gr.most_common(1)[0][0] if gr else ""
            e["gravidade_conflito"] = len(gr) > 1
        base = _slug(e["nome"]) or "sem_nome"
        eid, k = base, 2
        while eid in used[e["type"]]:
            eid, k = f"{base}_{k}", k + 1
        used[e["type"]].add(eid)
        e["id"] = eid
        entities.append(e)
    return entities


def resolve(extractions: dict[str, Extraction], use_llm_judge: bool = False, judge=None):
    """extractions: {trecho_id: Extraction}  ->  (entidades, relações, relatório)

    `judge(a, b) -> {"mesmo_conceito": bool, ...}` pode ser injetado nos testes.
    """
    # ---- camada 1: agrupar por (tipo, chave canônica)
    groups: dict[tuple[str, str], dict] = {}
    local_to_key: dict[tuple[str, str], tuple[str, str]] = {}
    mentions = 0
    for trecho_id, ext in extractions.items():
        for e in ext.entities:
            mentions += 1
            key = canonical_key(e.name, e.type)
            g = groups.setdefault(key, {"names": Counter(), "descs": Counter(), "chunks": set(),
                                        "type": e.type, "grav": [], "prazo": None})
            g["names"][e.name] += 1
            if e.description:
                g["descs"][e.description] += 1
            g["chunks"].add(trecho_id)
            if e.type == "Infracao" and e.gravidade in GRAVIDADES:
                g["grav"].append(e.gravidade)
            if e.type == "Prazo" and g["prazo"] is None:
                g["prazo"] = parse_prazo(e.name)
            local_to_key[(trecho_id, e.id)] = key
    # Prazo que não é simples (ex.: "semestre seguinte") vira nó só com texto
    for g in groups.values():
        if g["type"] == "Prazo" and g["prazo"] is None:
            g["prazo"] = {"valor": None, "unidade": None, "tipo_dia": None}

    # ---- camadas 2 e 3 sobre as entidades da camada 1
    pre = _build_entities({k: v for k, v in groups.items() if v["type"] != "Prazo"})
    by_id_key = {(e["type"], e["chave"]): e for e in pre}
    by_id = {e["id"] + "|" + e["type"]: e for e in pre}
    uf = _UF()
    decisoes = []
    pre_flat = [dict(e, id=e["id"] + "|" + e["type"]) for e in pre]    # id único entre tipos p/ candidatos
    for ia, ib, score in fuzzy_candidates(pre_flat):
        a, b = by_id[ia], by_id[ib]
        d = fuzzy_decision(a, b, score)
        fundir, juiz = d == "merge", None
        if d == "judge" and use_llm_judge:
            juiz = (judge or llm_judge)(a, b)
            fundir = bool(juiz["mesmo_conceito"])
        decisoes.append({"a": a["nome"], "b": b["nome"], "tipo": a["type"], "score": score,
                         "decisao": "fundir" if fundir else ("revisar" if d == "judge" else "manter"),
                         "juiz_llm": juiz})
        if fundir:
            uf.union((a["type"], a["chave"]), (b["type"], b["chave"]))

    # ---- aplicar as fusões
    merged_groups: dict[tuple[str, str], dict] = {}
    key_map: dict[tuple[str, str], tuple[str, str]] = {}
    for key, g in groups.items():
        root = uf.find(key) if key in uf.p else key
        key_map[key] = root
        m = merged_groups.setdefault(root, {"names": Counter(), "descs": Counter(), "chunks": set(),
                                            "type": g["type"], "grav": [], "prazo": g["prazo"]})
        m["names"].update(g["names"])
        m["descs"].update(g["descs"])
        m["chunks"] |= g["chunks"]
        m["grav"] += g["grav"]
    entities = _build_entities(merged_groups)
    key_to_id = {(e["type"], e["chave"]): e["id"] for e in entities}
    local_to_global = {loc: key_to_id[key_map[k]] for loc, k in local_to_key.items()}

    # ---- relações (com proveniência: trecho_id e ano do documento vêm na ingestão)
    relations, seen = [], set()
    for trecho_id, ext in extractions.items():
        for r in ext.relations:
            s, t = local_to_global.get((trecho_id, r.source)), local_to_global.get((trecho_id, r.target))
            if not s or not t:
                continue
            stype = next(e.type for e in ext.entities if e.id == r.source)
            ttype = next(e.type for e in ext.entities if e.id == r.target)
            k = (stype, s, r.type, ttype, t, trecho_id)
            if k in seen or (stype == ttype and s == t):
                continue
            seen.add(k)
            relations.append({"source": s, "source_type": stype, "target": t, "target_type": ttype,
                              "type": r.type, "trecho_id": trecho_id, "evidence": r.evidence})

    fundidos = [{"canonico": e["nome"], "tipo": e["type"], "variantes": e["aliases"]}
                for e in entities if e["aliases"]]
    report = {
        "mencoes_brutas": mentions,
        "nos_unicos": len(entities),
        "reducao_pct": round(100 * (1 - len(entities) / mentions), 1) if mentions else 0,
        "relacoes": len(relations),
        "por_tipo": dict(Counter(e["type"] for e in entities)),
        "grupos_fundidos": fundidos,                          # ouro para a seção "antes/depois" do artigo
        "prazos_normalizados": [{"nome": e["nome"], "variantes": e["aliases"]} for e in entities
                                if e["type"] == "Prazo" and e["aliases"]],
        "decisoes_fuzzy": decisoes[:300],                     # revisem à mão os "revisar"
        "conflitos_gravidade": [e["nome"] for e in entities if e.get("gravidade_conflito")],
    }
    return entities, relations, report
