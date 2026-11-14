"""Referências cruzadas entre artigos: "nos termos do art. 15" vira (:Trecho)-[:REFERE_SE_A]->(:Trecho).

É determinístico (regex), não gasta API e ajuda o multi-hop da Fase 3: o Art. 59 cita o Art. 58,
o Art. 14 cita o Art. 13, e assim por diante. Só vale para os documentos de artigos (kind='legal').
"""
import re

# "art. 15", "Art. 7º", "arts. 5º, 6º e 7º", "arts. 5º a 8º"
_ORD = r"(?:[º°]|o(?![a-zà-ú]))?"          # sufixo ordinal colado ao número: 7º, 7°, 7o
_ART = re.compile(rf"\bart(?:igo)?s?\.?\s*(\d+){_ORD}((?:\s*(?:,|e|a|ao)\s+\d+{_ORD})*)", re.I)
_NEXT = re.compile(r"(,|e|a|ao)\s*(\d+)", re.I)
# Norma EXTERNA: o artigo citado não é deste documento ("art. 47 da Lei nº 9.394", "art. 7º, II, da LGPD").
# Olha só até o fim da oração (primeiro ponto, ponto e vírgula ou dois-pontos).
_EXTERNO_DEPOIS = re.compile(
    r"\b(?:da|do|na|no|d[ae]sta)\s+(?:lei|lgpd|decreto|constitui|c[oó]digo\s+(?:civil|penal|de)|clt|ldb|estatuto|"
    r"regimento|resolu[cç][aã]o\s+n|portaria|instru[cç][aã]o|medida\s+provis|eca)", re.I)
_EXTERNO_ANTES = re.compile(r"(lei|decreto|portaria|resolu[cç][aã]o)\s+n?[º°o.]?\s*[\d./-]+\s*,?\s*$", re.I)


def _numeros(m: re.Match) -> list[int]:
    nums = [int(m.group(1))]
    for conj, n in _NEXT.findall(m.group(2) or ""):
        n = int(n)
        if conj.lower() in ("a", "ao") and nums:
            nums.extend(range(nums[-1] + 1, n + 1))     # "5º a 8º" -> 5,6,7,8
        else:
            nums.append(n)
    return nums


def find_crossrefs(chunks: list, docs_legais: set[str]) -> list[dict]:
    """chunks: objetos Chunk. Devolve [{'origem','destino','citacao'}] sem duplicatas."""
    existentes = {c.id for c in chunks}
    out, seen = [], set()
    for c in chunks:
        if c.doc_id not in docs_legais or not c.ref.startswith("Art."):
            continue
        proprio = int(c.ref.split()[1])
        texto = re.sub(r"^Art\.\s*\d+\s*[º°o]?\.?", "", c.text, count=1)    # ignora o cabeçalho do próprio artigo
        for m in _ART.finditer(texto):
            depois = re.split(r"(?<!\bart)(?<!\barts)(?<!§)[.;:]", texto[m.end(): m.end() + 90], maxsplit=1, flags=re.I)[0]
            antes = texto[max(0, m.start() - 40): m.start()]
            if _EXTERNO_DEPOIS.search(depois) or _EXTERNO_ANTES.search(antes):
                continue
            for n in _numeros(m):
                destino = f"{c.doc_id}:art-{n}"
                if n == proprio or destino not in existentes or (c.id, destino) in seen:
                    continue
                seen.add((c.id, destino))
                out.append({"origem": c.id, "destino": destino, "citacao": m.group(0).strip()})
    return out
