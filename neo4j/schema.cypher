// =====================================================================
// Schema LPG - GraphRAG UNIPE (Fase 1)
// Execute com:  python neo4j/apply_schema.py
// (o script le o .env e troca __EMBEDDING_DIM__ pela dimensao do modelo)
// Todos os comandos usam IF NOT EXISTS: pode rodar mais de uma vez.
// Comentarios so em linhas inteiras iniciadas por //
// =====================================================================

// ---------- 1. Constraints de unicidade (um "id" canonico por no) ----------
CREATE CONSTRAINT documento_id IF NOT EXISTS FOR (n:Documento) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT trecho_id IF NOT EXISTS FOR (n:Trecho) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT processo_id IF NOT EXISTS FOR (n:Processo) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT regra_id IF NOT EXISTS FOR (n:Regra) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT infracao_id IF NOT EXISTS FOR (n:Infracao) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT penalidade_id IF NOT EXISTS FOR (n:Penalidade) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT orgao_id IF NOT EXISTS FOR (n:Orgao) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT prazo_id IF NOT EXISTS FOR (n:Prazo) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT documento_exigido_id IF NOT EXISTS FOR (n:DocumentoExigido) REQUIRE n.id IS UNIQUE;

// ---------- 2. Indices de propriedade (range / B-tree) ----------
CREATE INDEX trecho_documento_idx IF NOT EXISTS FOR (n:Trecho) ON (n.documento_id);
CREATE INDEX trecho_ano_idx IF NOT EXISTS FOR (n:Trecho) ON (n.ano);
CREATE INDEX infracao_gravidade_idx IF NOT EXISTS FOR (n:Infracao) ON (n.gravidade);
CREATE INDEX processo_nome_idx IF NOT EXISTS FOR (n:Processo) ON (n.nome);

// ---------- 3. Indices fulltext ----------
// Se der erro no analisador, confira os nomes disponiveis com:
//   CALL db.index.fulltext.listAvailableAnalyzers()
// e ajuste (ou remova a clausula OPTIONS).
CREATE FULLTEXT INDEX trecho_texto_ft IF NOT EXISTS FOR (n:Trecho) ON EACH [n.texto] OPTIONS { indexConfig: { `fulltext.analyzer`: 'portuguese' } };
CREATE FULLTEXT INDEX entidade_nome_ft IF NOT EXISTS FOR (n:Processo|Regra|Infracao|Penalidade|Orgao|DocumentoExigido) ON EACH [n.nome, n.aliases] OPTIONS { indexConfig: { `fulltext.analyzer`: 'portuguese' } };

// ---------- 4. Indices vetoriais ----------
// A dimensao vem de EMBEDDING_DIM no .env.
// Se trocar o modelo de embeddings: DROP INDEX trecho_embedding_idx e rode de novo.
CREATE VECTOR INDEX trecho_embedding_idx IF NOT EXISTS FOR (n:Trecho) ON (n.embedding) OPTIONS { indexConfig: { `vector.dimensions`: __EMBEDDING_DIM__, `vector.similarity_function`: 'cosine' } };
CREATE VECTOR INDEX processo_embedding_idx IF NOT EXISTS FOR (n:Processo) ON (n.embedding) OPTIONS { indexConfig: { `vector.dimensions`: __EMBEDDING_DIM__, `vector.similarity_function`: 'cosine' } };
