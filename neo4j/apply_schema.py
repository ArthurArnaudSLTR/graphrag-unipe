"""Aplica neo4j/schema.cypher no banco configurado no .env.

Uso (na raiz do projeto):
    pip install -r requirements.txt
    python neo4j/apply_schema.py
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def exigir(nome: str) -> str:
    valor = os.getenv(nome)
    if not valor:
        sys.exit(f"Variavel {nome} ausente ou vazia no .env")
    return valor


uri = exigir("NEO4J_URI")
usuario = exigir("NEO4J_USER")
senha = exigir("NEO4J_PASSWORD")
dimensao = int(exigir("EMBEDDING_DIM"))

texto = (ROOT / "neo4j" / "schema.cypher").read_text(encoding="utf-8")
texto = texto.replace("__EMBEDDING_DIM__", str(dimensao))
linhas = [l for l in texto.splitlines() if not l.strip().startswith("//")]
comandos = [c.strip() for c in "\n".join(linhas).split(";") if c.strip()]

with GraphDatabase.driver(uri, auth=(usuario, senha)) as driver:
    driver.verify_connectivity()
    for comando in comandos:
        driver.execute_query(comando)
        print("OK:", comando[:90].replace("\n", " "))

print(f"\n{len(comandos)} comandos aplicados. Verifique com: SHOW INDEXES; SHOW CONSTRAINTS;")
