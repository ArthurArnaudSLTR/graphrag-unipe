"""Único arquivo que fala com o Gemini. Se trocarem de modelo/SDK, só mexem aqui."""
import time
from google import genai
from google.genai import errors, types
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from .config import GOOGLE_API_KEY, LLM_MODEL, EMBED_MODEL, EMBED_DIM

if not GOOGLE_API_KEY:
    raise SystemExit("Defina GOOGLE_API_KEY no arquivo .env (veja .env.example).")
_client = genai.Client(api_key=GOOGLE_API_KEY)
REQUEST_DELAY = 0.5  # segundos entre chamadas (o plano gratuito tem limite por minuto)


def _is_retryable(exc: BaseException) -> bool:
    # 429 = estourou a cota por minuto; 5xx = instabilidade do servidor
    return isinstance(exc, errors.APIError) and (exc.code == 429 or (exc.code or 0) >= 500)


@retry(retry=retry_if_exception(_is_retryable), wait=wait_exponential(min=4, max=90),
       stop=stop_after_attempt(6), reraise=True)
def generate_json(prompt: str, schema, system: str | None = None):
    """Chama o LLM pedindo saída que obedece ao `schema` (classe Pydantic).
    Retorna uma instância já validada da classe."""
    time.sleep(REQUEST_DELAY)
    resp = _client.models.generate_content(
        model=LLM_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.0,                      # extração = determinismo, não criatividade
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )
    if resp.parsed is None:
        raise ValueError(f"Resposta fora do schema: {(resp.text or '')[:200]}")
    return resp.parsed


def _as_content(text: str) -> types.Content:
    return types.Content(parts=[types.Part.from_text(text=text)])


@retry(retry=retry_if_exception(_is_retryable), wait=wait_exponential(min=4, max=90),
       stop=stop_after_attempt(6), reraise=True)
def _embed_batch(texts: list[str]) -> list[list[float]]:
    time.sleep(REQUEST_DELAY)
    resp = _client.models.embed_content(
        model=EMBED_MODEL,
        # ATENÇÃO: no gemini-embedding-2, uma lista de strings vira UM vetor agregado.
        # Para ter um vetor por texto, cada texto precisa estar embrulhado em um Content.
        contents=[_as_content(t) for t in texts],
        config=types.EmbedContentConfig(output_dimensionality=EMBED_DIM),
    )
    vectors = [e.values for e in resp.embeddings]
    if len(vectors) != len(texts):  # rede de segurança contra a agregação silenciosa
        raise ValueError(f"Esperava {len(texts)} vetores e recebi {len(vectors)}")
    return vectors


def embed_documents(items: list[tuple[str, str]], batch_size: int = 16) -> list[list[float]]:
    """items = [(título, texto), ...]. Formato de documento recomendado pelo Google
    para o gemini-embedding-2: 'title: ... | text: ...'."""
    prepared = [f"title: {t or 'none'} | text: {x}" for t, x in items]
    out: list[list[float]] = []
    for i in range(0, len(prepared), batch_size):
        out.extend(_embed_batch(prepared[i:i + batch_size]))
    return out


def embed_query(question: str) -> list[float]:
    """Formato de PERGUNTA (use na Fase 3). Precisa ser diferente do formato de documento."""
    return _embed_batch([f"task: question answering | query: {question}"])[0]
