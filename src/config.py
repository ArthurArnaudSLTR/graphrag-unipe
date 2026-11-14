"""Configurações centrais: tudo que muda de máquina para máquina vem do .env.

Aceita os nomes de variáveis da Fase 1 (EMBEDDING_MODEL, EMBEDDING_DIM) e também os
apelidos curtos (EMBED_MODEL, EMBED_DIM).
"""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


def _env(*names, default=None):
    for n in names:
        v = os.getenv(n)
        if v:
            return v
    return default


GOOGLE_API_KEY = _env("GOOGLE_API_KEY", "GEMINI_API_KEY")
# ATENÇÃO: a página de depreciações do Google (atualizada em 13/08/2026) marca gemini-2.5-flash e
# gemini-2.5-flash-lite com desligamento em 16/10/2026. Confira os nomes vigentes em
# https://ai.google.dev/gemini-api/docs/models e https://ai.google.dev/gemini-api/docs/deprecations
LLM_MODEL = _env("LLM_MODEL", default="gemini-3.6-flash")
EMBED_MODEL = _env("EMBEDDING_MODEL", "EMBED_MODEL", default="gemini-embedding-2")
EMBED_DIM = int(_env("EMBEDDING_DIM", "EMBED_DIM", default="768"))

NEO4J_URI = _env("NEO4J_URI", default="bolt://localhost:7687")
NEO4J_USER = _env("NEO4J_USER", default="neo4j")
NEO4J_PASSWORD = _env("NEO4J_PASSWORD", default="")
NEO4J_DATABASE = _env("NEO4J_DATABASE", default="neo4j")

# Os 3 documentos do dataset (Fase 1: docs/fontes.md). `kind` decide a estratégia de chunking.
# Os campos titulo/tipo/numero/data seguem as propriedades do nó :Documento do modelo da Fase 1.
DOCS = [
    {"id": "codigo_disciplinar", "titulo": "Código Disciplinar", "tipo": "Resolução",
     "numero": "CONSU nº 7/2026", "data": "2026-05-20", "ano": 2026,
     "file": "codigo_disciplinar.pdf", "kind": "legal", "first_page": 1},
    {"id": "manual_aluno", "titulo": "Manual do Aluno 2026", "tipo": "Manual",
     "numero": "versão 01", "data": None, "ano": 2026,
     "file": "manual_do_aluno_2026_unipe.pdf", "kind": "manual",
     "first_page": 6, "last_page": 38},  # págs. 1-5 = capa, boas-vindas, manifesto, sumário
    # A pág. 1 do Regulamento (Res. 065/CONSEPE) é imagem sem texto: só o regulamento em si é lido.
    {"id": "regulamento_aproveitamento", "titulo": "Regulamento de Aproveitamento de Estudos",
     "tipo": "Resolução", "numero": "065/CONSEPE", "data": "2020-07-15", "ano": 2020,
     "file": "regulamento de aproveritamento de estudos.pdf", "kind": "legal", "first_page": 2},
]


def doc_path(doc: dict) -> Path:
    """Caminho do PDF. Tolera variações de nome (espaços x '_', erros de digitação)."""
    p = RAW_DIR / doc["file"]
    if p.exists():
        return p
    alvo = doc["file"].lower().replace(" ", "_").replace("manueal", "manual")
    for f in RAW_DIR.glob("*.pdf"):
        if f.name.lower().replace(" ", "_").replace("manueal", "manual") == alvo:
            return f
    raise FileNotFoundError(f"PDF não encontrado em {RAW_DIR}: {doc['file']}")
