# GraphRAG UNIPÊ

Assistente de perguntas e respostas sobre as normas acadêmicas do UNIPÊ, construído sobre um
**grafo de conhecimento (LPG) no Neo4j** e o **Gemini** como LLM. Os documentos usados são o
Código Disciplinar, o Manual do Aluno 2026 e o Regulamento de Aproveitamento de Estudos.

## Fases do projeto

| Fase | Conteúdo | Onde está |
|---|---|---|
| 1. Proposta e modelagem LPG | Escopo do domínio, dataset, modelo conceitual, schema, constraints e índices | `docs/`, `neo4j/` |
| 2. Ingestão e pipeline NLP/LLM | Chunking, extração NER/RE com Gemini, resolução de entidades, embeddings, ingestão no Neo4j | `src/`, `run_phase2.py` |
| 3. GraphRAG/KAG | Busca híbrida (vetor + fulltext com RRF), Text2Cypher, travessia multi-hop, redução de alucinações | `src/retrieval.py`, `src/text2cypher.py`, `src/grounding.py`, `src/graphrag.py`, `run_phase3.py` |
| 4. Interface gráfica | Aplicação web em Streamlit | `app.py` |

## Instalação

Requer Python 3.11+, uma instância do Neo4j 5 (por exemplo, Aura) e uma chave da API do Gemini.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
```

Copie `.env.example` para `.env` e preencha:

```
NEO4J_URI=neo4j+s://<instancia>.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=...
GOOGLE_API_KEY=...
LLM_MODEL=gemini-3.6-flash
EMBEDDING_MODEL=gemini-embedding-2
EMBEDDING_DIM=768
```

> O plano gratuito da API do Gemini permite poucas requisições por dia por modelo. A extração completa
> (174 trechos) e a avaliação precisam de um projeto com faturamento ativo ou de vários dias de cota.

## Interface gráfica (Fase 4)

```bash
streamlit run app.py
```

Abre em `http://localhost:8501` e tem quatro abas:

- **Perguntar**: pergunta em linguagem natural, com escolha do modo (`vetorial`, `hibrido`, `graphrag`),
  número de trechos e de saltos. Mostra a resposta com a fonte de cada afirmação, as afirmações
  descartadas pela verificação, os trechos usados, o Cypher gerado e o contexto enviado ao LLM.
- **Explorar**: busca uma entidade do grafo e desenha a vizinhança (até 2 saltos) e os trechos onde aparece.
- **Grafo**: contagens de nós e relações no Neo4j e o resultado da resolução de entidades.
- **Avaliação**: resultados de `run_phase3.py eval` comparando os três modos.

## Pipeline completo pela linha de comando

```bash
# Fase 1: schema, constraints e índices
python neo4j/apply_schema.py

# Fase 2: ingestão
python run_phase2.py chunk                       # PDFs -> trechos
python run_phase2.py extract                     # NER/RE few-shot (cache: pode interromper e retomar)
python run_phase2.py extract --prompt baseline   # prompt sem exemplos, para comparação
python run_phase2.py review --n 40               # amostra para revisão manual (CSV)
python run_phase2.py metrics                     # precisão/cobertura a partir do CSV preenchido
python run_phase2.py resolve                     # resolução de entidades
python run_phase2.py embed                       # embeddings dos trechos e Processos
python run_phase2.py ingest --reset              # grava no Neo4j (apaga antes só o que a Fase 2 criou)
python run_phase2.py stats                       # contagens e checagens

# Fase 3: GraphRAG
python run_phase3.py ask "Quem julga o recurso contra uma penalidade?"
python run_phase3.py ask "..." --modo vetorial --contexto
python run_phase3.py cypher "Quais penalidades cabem para plágio?"
python run_phase3.py eval                        # perguntas de docs/perguntas_teste.md nos 3 modos
python run_phase3.py metrics                     # notas manuais do CSV, por modo
```

## Testes

```bash
pytest
```

Os testes não chamam a API do Gemini nem o Neo4j.

## Estrutura

```
app.py               interface gráfica (Streamlit)
run_phase2.py        orquestrador da ingestão
run_phase3.py        orquestrador do GraphRAG e da avaliação
src/                 chunking, extração, resolução, Neo4j, recuperação, Text2Cypher, grounding
neo4j/               schema.cypher, apply_schema.py, teste de fumaça
docs/                domínio, fontes, modelo LPG, perguntas de teste
data/raw/            PDFs originais
data/processed/      trechos, extrações, entidades, embeddings, resultados da avaliação
tests/               testes automatizados
```
