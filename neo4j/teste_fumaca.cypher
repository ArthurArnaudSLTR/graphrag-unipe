// Teste de fumaca da Fase 1. Rode UM bloco por vez no Query do Aura.

// --- Bloco 1: criar um mini-grafo de teste (caso da monitoria) ---
MERGE (i:Infracao {id:'teste_infracao_grave'}) SET i.nome='Infracao grave (teste)', i.gravidade='grave', i.teste=true
MERGE (p:Penalidade {id:'teste_suspensao'}) SET p.nome='Suspensao (teste)', p.teste=true
MERGE (m:Processo {id:'teste_monitoria'}) SET m.nome='Monitoria (teste)', m.teste=true
MERGE (r:Regra {id:'teste_sem_sancao'}) SET r.nome='Nao ter sancao disciplinar (teste)', r.teste=true
MERGE (i)-[:PUNIVEL_COM]->(p)
MERGE (p)-[:AFETA]->(m)
MERGE (m)-[:REQUER]->(r)
RETURN i, p, m, r;

// --- Bloco 2: consulta de 3 saltos ---
MATCH path = (i:Infracao {id:'teste_infracao_grave'})-[:PUNIVEL_COM]->(:Penalidade)-[:AFETA]->(:Processo)-[:REQUER]->(r:Regra)
RETURN path, r.nome AS regra_final;

// --- Bloco 3: a constraint deve BARRAR duplicata (este comando DEVE dar erro) ---
CREATE (:Processo {id:'teste_monitoria'});

// --- Bloco 4: limpar o teste ---
MATCH (n {teste:true}) DETACH DELETE n;
