"""Texto -> grafo: extração de entidades (NER) e relações (RE) com o Gemini.

O vocabulário (rótulos e tipos de relação) é o do modelo LPG da Fase 1 (docs/modelo.md).
Ele é FECHADO de propósito: sem isso o LLM inventa um tipo diferente a cada chamada.

Há duas versões do prompt, para medir o efeito dos exemplos (figura do artigo):
  "baseline"  só regras e vocabulário (sem exemplos)
  "fewshot"   regras + 4 exemplos reais (Código, Regulamento, Manual)   <- padrão
Cada versão tem seu próprio cache em data/processed/extractions/<versao>/.
"""
import re
from typing import Literal
from pydantic import BaseModel

from .chunking import Chunk
from .config import PROCESSED_DIR

# ---------------------------------------------------------------- vocabulário (Fase 1)
NODE_TYPES = {
    "Processo": "procedimento, serviço, programa ou instrumento com etapas ou regras próprias "
                "(Trancamento de Matrícula, Segunda Chamada, Monitoria, Sindicância, "
                "Processo Administrativo Disciplinar, Recurso, Avaliação Final, Aproveitamento de Estudos)",
    "Regra": "condição, requisito ou limite que se aplica (75% de frequência, Nota Final >= 6,0, "
             "não ter sofrido sanção disciplinar, máximo de 70% da carga horária dispensada)",
    "Infracao": "conduta proibida (Plágio, Fraude acadêmica, Agressão)",
    "Penalidade": "sanção (Advertência, Repreensão, Suspensão, Desligamento)",
    "Orgao": "órgão, setor ou cargo que decide ou recebe pedidos (Reitor, Consepe, "
             "Coordenação de Curso, Secretaria de Controle e Registro Acadêmico)",
    "Prazo": "intervalo de tempo (5 dias úteis, 45 dias úteis, 2 anos, 1 semestre)",
    "DocumentoExigido": "documento que o aluno precisa apresentar (atestado médico, histórico escolar, ementa)",
}

_ANY = set(NODE_TYPES)
# nome -> (tipos de origem permitidos, tipos de destino permitidos, explicação)
# MENCIONADO_EM e PARTE_DE são estruturais (feitas pelo pipeline, não pelo LLM).
REL_TYPES = {
    "PUNIVEL_COM": ({"Infracao"}, {"Penalidade"}, "infração -> penalidade que pode receber"),
    "AFETA": ({"Penalidade"}, {"Processo"}, "penalidade que impede, encerra ou altera um processo/programa"),
    "REQUER": ({"Processo"}, {"Regra"}, "processo -> condição/requisito/limite que precisa ser cumprido"),
    "EXIGE": ({"Processo"}, {"DocumentoExigido"}, "processo -> documento que precisa ser apresentado"),
    # Ajuste ao modelo da Fase 1 (que previa só Processo -> Prazo): prescrição pertence à Infração, etc.
    "TEM_PRAZO": ({"Processo", "Infracao", "Penalidade", "Regra"}, {"Prazo"}, "prazo associado"),
    "DECIDIDO_POR": ({"Processo"}, {"Orgao"}, "processo -> órgão que decide, defere ou julga"),
    "PRECEDE": ({"Processo"}, {"Processo"}, "processo que vem antes de outro"),
}

GRAVIDADES = ("leve", "media", "grave", "gravissima")

NodeType = Literal[tuple(NODE_TYPES)]   # vira enum no schema enviado ao Gemini
RelType = Literal[tuple(REL_TYPES)]
Gravidade = Literal["", "leve", "media", "grave", "gravissima"]


class Entity(BaseModel):
    id: str            # id LOCAL ao trecho: "e1", "e2"... (a identidade global vem na resolução)
    name: str
    type: NodeType
    description: str   # detalhe curto (pode ser "")
    gravidade: Gravidade  # só para Infracao ("" nos demais tipos)


class Relation(BaseModel):
    source: str        # id local da entidade de origem
    target: str        # id local da entidade de destino
    type: RelType
    evidence: str      # trecho LITERAL do texto (<= 25 palavras) que justifica a relação


class Extraction(BaseModel):
    entities: list[Entity]
    relations: list[Relation]


# ---------------------------------------------------------------- prompt
PROMPT_VERSIONS = ("baseline", "fewshot")

FEWSHOT = """
EXEMPLOS (reais; siga o estilo de nomes, tipos e evidências)

Exemplo 1 - Código Disciplinar, Art. 59
Trecho: "Art. 59. Da decisão do Reitor, cabe recurso, no prazo de cinco dias úteis, ao Consepe, que decidirá pelo voto da maioria simples dos membros presentes à reunião em que for julgado o recurso. [...] § 3º Das decisões proferidas em segunda instância, não cabe recurso."
Saída: {"entities":[{"id":"e1","name":"Recurso contra decisão do Reitor","type":"Processo","description":"recurso à segunda instância","gravidade":""},
{"id":"e2","name":"Consepe","type":"Orgao","description":"","gravidade":""},
{"id":"e3","name":"5 dias úteis","type":"Prazo","description":"prazo para recorrer","gravidade":""}],
"relations":[{"source":"e1","target":"e2","type":"DECIDIDO_POR","evidence":"cabe recurso, no prazo de cinco dias úteis, ao Consepe, que decidirá"},
{"source":"e1","target":"e3","type":"TEM_PRAZO","evidence":"cabe recurso, no prazo de cinco dias úteis"}]}

Exemplo 2 - Código Disciplinar, Art. 15 (efeito de penalidade sobre um programa)
Trecho: "Art. 15. São efeitos da aplicação da penalidade: [...] III - no caso de infrações graves ou gravíssimas, destituição de cargos, funções e vínculos acadêmicos voluntários ou remunerados que o aluno ocupe na Instituição, incluindo, exemplificativamente, representação de classe, monitoria acadêmica, iniciação científica"
Saída: {"entities":[{"id":"e1","name":"Penalidade por infração grave ou gravíssima","type":"Penalidade","description":"","gravidade":""},
{"id":"e2","name":"Monitoria","type":"Processo","description":"vínculo acadêmico","gravidade":""}],
"relations":[{"source":"e1","target":"e2","type":"AFETA","evidence":"destituição de cargos, funções e vínculos acadêmicos [...] monitoria acadêmica"}]}

Exemplo 3 - Regulamento de Aproveitamento de Estudos, Art. 7
Trecho: "Art. 7º O aproveitamento de estudos entre disciplinas pode ser concedido desde que haja similaridade de conteúdo e carga horária compatível. Em nenhuma hipótese será concedido aproveitamento de estudos quando o número de horas cursadas na disciplina de origem for inferior a 75% (setenta por cento) da carga horária da disciplina cuja dispensa é pretendida. § 3º O candidato/estudante só poderá ser dispensado de, no máximo, 70% da carga horária do curso"
Saída: {"entities":[{"id":"e1","name":"Aproveitamento de estudos","type":"Processo","description":"","gravidade":""},
{"id":"e2","name":"Mínimo de 75% da carga horária da disciplina de origem","type":"Regra","description":"","gravidade":""},
{"id":"e3","name":"Máximo de 70% da carga horária do curso dispensada","type":"Regra","description":"","gravidade":""}],
"relations":[{"source":"e1","target":"e2","type":"REQUER","evidence":"inferior a 75% (setenta por cento) da carga horária da disciplina cuja dispensa é pretendida"},
{"source":"e1","target":"e3","type":"REQUER","evidence":"só poderá ser dispensado de, no máximo, 70% da carga horária do curso"}]}

Exemplo 4 - Manual do Aluno, p. 24 (página com vários assuntos: extraia só o que tem relação)
Trecho: "Monitoria [...] São requisitos para o exercício da monitoria: ser estudante da graduação; ter cursado e sido aprovado na disciplina objeto de monitoria ou equivalente; não ter sofrido qualquer sanção disciplinar e estar cumprindo, regularmente, o Contrato de Prestação de Serviços Educacionais (CPSE)."
Saída: {"entities":[{"id":"e1","name":"Monitoria","type":"Processo","description":"","gravidade":""},
{"id":"e2","name":"Não ter sofrido sanção disciplinar","type":"Regra","description":"requisito para ser monitor","gravidade":""},
{"id":"e3","name":"Ter sido aprovado na disciplina objeto de monitoria","type":"Regra","description":"","gravidade":""}],
"relations":[{"source":"e1","target":"e2","type":"REQUER","evidence":"não ter sofrido qualquer sanção disciplinar"},
{"source":"e1","target":"e3","type":"REQUER","evidence":"ter cursado e sido aprovado na disciplina objeto de monitoria ou equivalente"}]}
"""

BASELINE_EXAMPLE = """
FORMATO (exemplo mínimo)
Trecho: "Art. 18. A punibilidade das infrações disciplinares prescreve: I - em três anos, para as infrações gravíssimas;"
Saída: {"entities":[{"id":"e1","name":"Infração gravíssima","type":"Infracao","description":"","gravidade":"gravissima"},
{"id":"e2","name":"3 anos","type":"Prazo","description":"prescrição da punibilidade","gravidade":""}],
"relations":[{"source":"e1","target":"e2","type":"TEM_PRAZO","evidence":"prescreve: I - em três anos, para as infrações gravíssimas"}]}
"""


def build_system_prompt(version: str = "fewshot") -> str:
    if version not in PROMPT_VERSIONS:
        raise ValueError(f"versão de prompt desconhecida: {version}")
    nodes = "\n".join(f"  - {k}: {v}" for k, v in NODE_TYPES.items())
    rels = "\n".join(f"  - {k} ({'|'.join(sorted(s))} -> {'|'.join(sorted(t))}): {d}"
                     for k, (s, t, d) in REL_TYPES.items())
    base = f"""Você extrai um grafo de conhecimento de normas acadêmicas do Centro Universitário UNIPÊ.
Receberá UM trecho (um artigo ou uma página). Devolva entidades e relações em JSON.

TIPOS DE ENTIDADE PERMITIDOS:
{nodes}

TIPOS DE RELAÇÃO PERMITIDOS (origem -> destino):
{rels}

REGRAS:
1. Extraia SOMENTE o que está explícito no trecho. Nada de conhecimento externo.
2. Nomes curtos e canônicos, sem artigo: "Reitor" (não "o Reitor da Instituição").
3. Prazos em forma padrão e com algarismos: "5 dias úteis", "2 anos". Limites em forma padrão:
   "Mínimo de 75% de frequência", "Nota Final >= 6,0".
4. Só devolva entidades que participem de pelo menos UMA relação.
5. Toda relação precisa de `evidence`: cópia LITERAL de até 25 palavras do trecho.
6. Use só os ids locais (e1, e2...) que você mesmo criou em `entities`.
7. Para Infracao, preencha `gravidade` (leve, media, grave ou gravissima) apenas se o trecho disser;
   caso contrário, "". Nos demais tipos, `gravidade` é sempre "".
8. Se o trecho não tiver nada aproveitável (contatos, boas-vindas, tutoriais), devolva listas vazias.
9. Respeite a direção das relações. Se não couber em nenhum tipo permitido, não extraia.
"""
    return base + (FEWSHOT if version == "fewshot" else BASELINE_EXAMPLE)


def build_user_prompt(chunk: Chunk, doc_title: str) -> str:
    where = f"{chunk.ref}" + (f" | {chunk.section_path}" if chunk.section_path else "")
    return f"DOCUMENTO: {doc_title}\nLOCALIZAÇÃO: {where}\nTRECHO:\n{chunk.text}"


# ---------------------------------------------------------------- validação
def clean_extraction(ext: Extraction) -> Extraction:
    """Descarta o que viola o schema da Fase 1 (o LLM às vezes escorrega mesmo com enum)."""
    ents = {}
    for e in ext.entities:
        name = re.sub(r"\s+", " ", e.name).strip()
        if not name or e.id in ents:
            continue
        grav = e.gravidade if e.type == "Infracao" else ""
        ents[e.id] = e.model_copy(update={"name": name, "gravidade": grav,
                                          "description": " ".join(e.description.split())})
    rels, seen = [], set()
    for r in ext.relations:
        src, tgt = ents.get(r.source), ents.get(r.target)
        if not src or not tgt or src.id == tgt.id:
            continue
        allowed_src, allowed_tgt, _ = REL_TYPES[r.type]
        if src.type not in allowed_src or tgt.type not in allowed_tgt:
            continue                      # ex.: PUNIVEL_COM só vai de Infracao para Penalidade
        key = (r.source, r.type, r.target)
        if key in seen:
            continue
        seen.add(key)
        rels.append(r.model_copy(update={"evidence": " ".join(r.evidence.split()[:40])}))
    used = {r.source for r in rels} | {r.target for r in rels}
    kept = [e for e in ents.values() if e.id in used]      # regra 4: sem entidade solta
    return Extraction(entities=kept, relations=rels)


# ---------------------------------------------------------------- execução com cache
def _cache_path(chunk_id: str, version: str = "fewshot"):
    d = PROCESSED_DIR / "extractions" / version
    d.mkdir(parents=True, exist_ok=True)
    return d / (chunk_id.replace(":", "__") + ".json")   # ':' não é válido em nome de arquivo no Windows


def extract_chunk(chunk: Chunk, doc_title: str, version: str = "fewshot") -> Extraction:
    from .gemini_client import generate_json            # import tardio: testes não exigem API
    raw = generate_json(build_user_prompt(chunk, doc_title), Extraction, build_system_prompt(version))
    return clean_extraction(raw)


def extract_all(chunks: list[Chunk], titles: dict[str, str], limit: int | None = None,
                version: str = "fewshot", extractor=None) -> dict[str, Extraction]:
    """Extrai de todos os chunks. Resultado fica em cache: se der erro no meio, rode de novo
    e ele continua de onde parou (não gasta cota repetindo o que já foi feito).
    `extractor` (opcional) permite injetar uma função falsa nos testes."""
    extractor = extractor or extract_chunk
    out = {}
    todo = chunks[:limit] if limit else chunks
    for i, ch in enumerate(todo, 1):
        path = _cache_path(ch.id, version)
        if path.exists():
            out[ch.id] = Extraction.model_validate_json(path.read_text(encoding="utf-8"))
            continue
        try:
            ext = extractor(ch, titles[ch.doc_id], version)
        except Exception as exc:                          # não derruba o lote inteiro
            print(f"  [{i}/{len(todo)}] FALHOU {ch.id}: {exc}")
            continue
        path.write_text(ext.model_dump_json(indent=1), encoding="utf-8")
        out[ch.id] = ext
        print(f"  [{i}/{len(todo)}] {ch.id}: {len(ext.entities)} entidades, {len(ext.relations)} relações")
    return out
