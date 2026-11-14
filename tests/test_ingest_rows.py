from src.neo4j_io import aggregate_relations, PROPS
from src.extraction import NODE_TYPES


def test_agrega_mesma_aresta_de_trechos_diferentes():
    rels = [{"source": "recurso", "source_type": "Processo", "target": "consepe", "target_type": "Orgao",
             "type": "DECIDIDO_POR", "trecho_id": t, "evidence": ev}
            for t, ev in [("codigo:art-59", "ao Consepe"), ("manual:p20", "Consepe julga")]]
    agg = aggregate_relations(rels, {"codigo:art-59": 2026, "manual:p20": 2026})
    assert len(agg) == 1 and agg[0]["trecho_ids"] == ["codigo:art-59", "manual:p20"] and agg[0]["anos"] == [2026, 2026]


def test_todo_rotulo_tem_propriedades_definidas():
    assert set(PROPS) == set(NODE_TYPES)
    assert "embedding" in PROPS["Processo"] and "gravidade" in PROPS["Infracao"]
