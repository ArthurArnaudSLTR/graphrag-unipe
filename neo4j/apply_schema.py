
"""Aplica neo4j/schema.cypher no banco configurado no .env.

Uso (na raiz do projeto):
    py -m pip install -r requirements.txt
    py neo4j/apply_schema.py
"""

import os
import sys
from pathlib import Path

import certifi

# Configura os certificados SSL utilizados pelo Python.
os.environ["SSL_CERT_FILE"] = certifi.where()

from dotenv import load_dotenv
from neo4j import GraphDatabase


ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def exigir(nome: str) -> str:
    valor = os.getenv(nome)
    if not valor:
        sys.exit(f"Variavel {nome} ausente ou vazia no .env")
    return valor


# Dados de conexão
uri = exigir("NEO4J_URI")
usuario = exigir("NEO4J_USER")
senha = exigir("NEO4J_PASSWORD")
banco = exigir("NEO4J_DATABASE")

# Dimensão dos embeddings definida no .env
try:
    dimensao = int(exigir("EMBEDDING_DIM"))
except ValueError:
    sys.exit("EMBEDDING_DIM precisa ser um numero inteiro.")


# Lê o schema e substitui a dimensão dos vetores
arquivo_schema = ROOT / "neo4j" / "schema.cypher"

if not arquivo_schema.exists():
    sys.exit(f"Arquivo nao encontrado: {arquivo_schema}")

texto = arquivo_schema.read_text(encoding="utf-8")
texto = texto.replace("__EMBEDDING_DIM__", str(dimensao))


# Remove comentários e separa os comandos Cypher
linhas = [
    linha
    for linha in texto.splitlines()
    if not linha.strip().startswith("//")
]

comandos = [
    comando.strip()
    for comando in "\n".join(linhas).split(";")
    if comando.strip()
]


# Conecta ao Neo4j e aplica o schema
with GraphDatabase.driver(
    uri,
    auth=(usuario, senha)
) as driver:

    driver.verify_connectivity()

    for comando in comandos:
        driver.execute_query(
            comando,
            database_=banco
        )

        print("OK:", comando[:90].replace("\n", " "))


print(
    f"\n{len(comandos)} comandos aplicados com sucesso."
    "\nVerifique com: SHOW INDEXES; SHOW CONSTRAINTS;"
)
