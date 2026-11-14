"""Quebra os documentos em chunks que respeitam a ESTRUTURA (artigo / página).

Por que não tamanho fixo? Porque na resposta final queremos citar "Art. 14, §2º do Código
Disciplinar" e não "trecho 37". O chunk é a unidade de proveniência do grafo.
"""
import re
from dataclasses import dataclass, asdict

from .config import doc_path
from .loader import read_pages

# "Art. 1º", "Art. 10." , "Art. 7º-A" (pegamos só o número)
ART_RE = re.compile(r"^Art\.\s*(\d+)\s*[º°o]?\s*\.?(?:\s|$)")
TITULO_RE = re.compile(r"^(TÍTULO|CAPÍTULO)\s+([IVXLC]+)\b\s*[–-]?\s*(.*)$")
SECAO_RE = re.compile(r"^(Seção)\s+([IVXLC]+)\b\s*[–-]?\s*(.*)$")
ANEXO_RE = re.compile(r"^ANEXO\b.*", re.I)
# Termo do glossário: "Acareação. Ato de colocar..." (só usado dentro do ANEXO)
TERMO_RE = re.compile(r"^([A-ZÁÉÍÓÚÂÊÔÃÕÇ][\w\sáéíóúâêôãõçÁÉÍÓÚÂÊÔÃÕÇ\-()/]{2,60}?)\.\s+\S")


@dataclass
class Chunk:
    id: str            # estável: rodar o pipeline 2x não pode duplicar nada (MERGE usa isto)
    doc_id: str
    ref: str           # "Art. 14" | "p. 31" | "Glossário: Acareação"
    section_path: str  # "TÍTULO II > CAPÍTULO III" (vazio se não houver)
    page: int
    text: str

    def to_dict(self):
        return asdict(self)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def chunk_legal(doc: dict, pages: list[tuple[int, str]]) -> list[Chunk]:
    """Um chunk por artigo (caput + parágrafos + incisos). Glossário: um chunk por termo.

    Máquina de estados simples sobre a lista de linhas:
      cabeçalho (TÍTULO/CAPÍTULO/Seção) -> atualiza `path` e fecha o chunk atual
      "Art. N"                           -> abre um chunk novo
      qualquer outra linha               -> pertence ao chunk aberto
    """
    doc_id = doc["id"]
    stream = [(p, ln) for p, t in pages for ln in t.splitlines()]
    chunks: list[Chunk] = []
    levels = ("titulo", "capitulo", "secao", "sub")
    path = dict.fromkeys(levels, "")
    cur = {"ref": "Preâmbulo", "page": stream[0][0], "path": "", "lines": []}
    state = {"last_n": 0, "annex": False, "label_key": None, "can_extend": False}

    def section_path() -> str:
        return " > ".join(v for v in path.values() if v)

    def flush():
        text = "\n".join(cur["lines"]).strip()
        if text and cur["ref"]:
            cid, k = f"{doc_id}:{_slug(cur['ref'])}", 2
            while any(c.id == cid for c in chunks):       # garante IDs únicos
                cid, k = f"{doc_id}:{_slug(cur['ref'])}-{k}", k + 1
            chunks.append(Chunk(cid, doc_id, cur["ref"], cur["path"], cur["page"], text))
        cur["lines"] = []

    def start(ref, page):
        flush()
        cur.update(ref=ref, page=page, path=section_path())

    def set_level(key, label):
        flush()
        cur["ref"] = None                                  # nada acumula até o próximo Art.
        for k in levels[levels.index(key):]:
            path[k] = ""                                   # nível novo zera os de baixo
        path[key] = label
        state["label_key"] = key if label.count("(") == 0 else None
        state["can_extend"] = key  # título pode quebrar em 2 linhas MAIÚSCULAS

    for idx, (page_no, line) in enumerate(stream):
        nxt = stream[idx + 1][1] if idx + 1 < len(stream) else ""
        line = line.strip()

        # 1) continuação do nome de um TÍTULO/CAPÍTULO/Seção
        if state["label_key"] and not ART_RE.match(line):
            path[state["label_key"]] += f" ({line})"
            state["label_key"] = None
            continue
        ext = state["can_extend"]
        state["label_key"] = None
        state["can_extend"] = False
        if ext and line.isupper() and not ART_RE.match(line) and not TITULO_RE.match(line):
            path[ext] = path[ext].rstrip(")") + f" {line})"
            continue

        # 2) cabeçalhos estruturais
        m = TITULO_RE.match(line) or SECAO_RE.match(line)
        if m and not state["annex"]:
            kind = m.group(1).upper()
            key = {"TÍTULO": "titulo", "CAPÍTULO": "capitulo", "SEÇÃO": "secao"}[kind]
            label = f"{m.group(1)} {m.group(2)}" + (f" ({m.group(3).strip()})" if m.group(3) else "")
            set_level(key, label)
            continue

        # 3) subtítulo solto entre artigos ("Do Destrancamento"): curto, sem pontuação final,
        #    e seguido por "Art."
        if (not state["annex"] and re.match(r"^D[oa]s?\s", line) and len(line) < 70
                and not line.endswith((".", ";", ":", ",")) and ART_RE.match(nxt)):
            set_level("sub", line)
            continue

        # 4) anexo (glossário)
        if ANEXO_RE.match(line):
            flush()
            state["annex"] = True
            path.update(titulo=line, capitulo="", secao="", sub="")
            cur.update(ref="Anexo", page=page_no, path=section_path(), lines=[])
            continue

        # 5) início de artigo (só aceita numeração crescente: evita falso positivo)
        m_art = ART_RE.match(line)
        if m_art and not state["annex"] and int(m_art.group(1)) > state["last_n"]:
            state["last_n"] = int(m_art.group(1))
            start(f"Art. {state['last_n']}", page_no)
            cur["lines"].append(line)
            continue

        # 6) termo do glossário
        if state["annex"]:
            m_t = TERMO_RE.match(line)
            if m_t:
                start(f"Glossário: {m_t.group(1).strip()}", page_no)

        if cur["ref"] is None:                             # texto solto após um cabeçalho
            start(f"Trecho p.{page_no}", page_no)
        cur["lines"].append(line)
    flush()
    return chunks


def chunk_manual(doc: dict, pages: list[tuple[int, str]]) -> list[Chunk]:
    """Um chunk por PÁGINA. O Manual tem layout em caixas coloridas e a ordem de leitura
    extraída é imperfeita (às vezes o título vem depois do corpo). Como a unidade é a página
    inteira, a ordem interna não importa: o LLM reorganiza o sentido na extração.
    Proveniência: "Manual do Aluno, p. 31".
    TODO (grupo, opcional): dividir páginas longas por parágrafo, ou usar o Gemini com o PDF
    da página como imagem para transcrever na ordem correta.
    """
    return [Chunk(f"{doc['id']}:p{n}", doc["id"], f"p. {n}", "", n, txt) for n, txt in pages]


def chunk_document(doc: dict, raw_dir) -> list[Chunk]:
    pages = read_pages(doc_path(doc), doc["first_page"], doc.get("last_page"))
    return (chunk_legal if doc["kind"] == "legal" else chunk_manual)(doc, pages)


if __name__ == "__main__":  # inspeção rápida
    from .config import DOCS, RAW_DIR
    for d in DOCS:
        cs = chunk_document(d, RAW_DIR)
        print(f"\n== {d['id']}: {len(cs)} chunks | maior = {max(len(c.text) for c in cs)} chars")
        for c in cs[:3] + cs[-2:]:
            print(f"  [{c.ref:<28}] p.{c.page:<3} {c.section_path[:50]:<50} | {c.text[:50]!r}")
