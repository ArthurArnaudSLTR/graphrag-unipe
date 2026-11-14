"""Lê o PDF página a página e remove cabeçalhos/rodapés repetidos."""
import re
import pymupdf  # PyMuPDF

# Linhas que se repetem em toda página e só atrapalham a extração.
# TODO (grupo): rode `python -m src.loader` e confira se sobrou lixo; acrescente padrões aqui.
NOISE = [
    r"^_{5,}$",
    r"^UNIPÊ - CENTRO UNIVERSITÁRIO DE JOÃO PESSOA$",
    r"^CAMPUS SEDE:.*",
    r".*www\.unipe\.edu\.br.*",
    r"^Recredenciamento Institucional:.*",
    r"^Credenciamento EAD:.*",
    r"^Recredenciada pela Portaria.*",
    r"^BR-230 – KM 22, Água Fria$",
    r"^58053 002$",
    r"^João Pessoa PB Brasil$",
    r"^T F 55 83 2106 9202$",
    r"^Voltar para o Sumário$",
    r"^Documento assinado digitalmente$",
    r"^\d{1,2}$",            # número de página sozinho na linha
]
_NOISE_RE = [re.compile(p) for p in NOISE]


def clean_page(text: str) -> str:
    lines = []
    for raw in text.splitlines():
        line = raw.replace("\t", " ").strip()
        if not line or any(rx.match(line) for rx in _NOISE_RE):
            continue
        lines.append(line)
    return "\n".join(lines)


def read_pages(path, first_page=1, last_page=None) -> list[tuple[int, str]]:
    """Retorna [(nº_da_página, texto_limpo), ...] com numeração começando em 1."""
    doc = pymupdf.open(path)
    last = last_page or len(doc)
    pages = []
    for n in range(first_page, last + 1):
        txt = clean_page(doc[n - 1].get_text())
        if txt:
            pages.append((n, txt))
    return pages


if __name__ == "__main__":  # inspeção rápida
    from .config import DOCS, RAW_DIR
    for d in DOCS:
        pgs = read_pages(RAW_DIR / d["file"], d["first_page"], d.get("last_page"))
        print(f"{d['id']}: {len(pgs)} páginas, {sum(len(t) for _, t in pgs)} caracteres")
