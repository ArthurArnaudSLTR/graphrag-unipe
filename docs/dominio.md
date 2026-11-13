# Definição do domínio

## O que o assistente faz

Assistente que responde dúvidas de estudantes de graduação do UNIPÊ sobre:

- avaliação e frequência (A1, A2, NF, AF, segunda chamada, revisão de notas e faltas);
- situação acadêmica (trancamento, cancelamento, retorno ao curso, transferências internas);
- aproveitamento de estudos e dispensa de disciplinas;
- estágio, monitoria, extensão e iniciação científica;
- regime disciplinar (infrações, penalidades, procedimentos e recursos).

As respostas devem citar os trechos e o caminho do grafo usados como base.

## O que NÃO cobre

- dados individuais de alunos (notas, faltas, situação financeira);
- valores financeiros, mensalidades e taxas em reais;
- calendários de cada semestre;
- o Regimento Geral e normas que não estão no corpus (ver `docs/fontes.md`).

## Por que GraphRAG neste domínio

As regras estão espalhadas em documentos diferentes e se cruzam. Exemplo: o Código Disciplinar manda destituir da monitoria quem sofre sanção grave, e o Manual exige, para ser monitor, nunca ter sofrido sanção. Responder bem exige ligar os dois, o que a busca vetorial pura não garante.

## Principais desafios do corpus

- Sinônimos: "expulsão" (Manual) e "desligamento" (Código); "Coordenação" e "Coordenadoria".
- Versões diferentes: o Regulamento de 2020 e o Manual de 2026 divergem sobre como a dispensa de disciplina é analisada.
- Referências a documentos que não temos (Regimento Geral, IN 01/2019, Res. 7/2018).
