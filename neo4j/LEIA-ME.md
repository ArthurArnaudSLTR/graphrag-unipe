# Como aplicar o schema

1. Instale as dependências (na raiz do projeto): `pip install -r requirements.txt`
2. Confirme que o `.env` tem `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` e `EMBEDDING_DIM`.
3. Rode: `python neo4j/apply_schema.py`
4. No Query do Aura, confira: `SHOW CONSTRAINTS;` e `SHOW INDEXES;` (estado ONLINE).
5. Rode os blocos de `neo4j/teste_fumaca.cypher`, um de cada vez.

Alternativa sem Python: cole cada comando de `schema.cypher` no Query do Aura, um por vez, trocando `__EMBEDDING_DIM__` pelo número da dimensão.
