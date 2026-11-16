from src.grounding import SEM_RESPOSTA, Afirmacao, Resposta, montar_contexto, numeros, verificar

TRECHOS = [{"id": "codigo_disciplinar:art-59", "ref": "Art. 59", "ano": 2026, "documento": "Código Disciplinar",
            "documento_id": "codigo_disciplinar",
            "texto": "Da decisão do Reitor, cabe recurso, no prazo de cinco dias úteis, ao Consepe."}]
FATOS = [{"tipo_origem": "Processo", "origem": "Recurso", "relacao": "DECIDIDO_POR", "tipo_destino": "Orgao",
          "destino": "Consepe", "evidencias": ["ao Consepe, que decidirá"],
          "trecho_ids": ["codigo_disciplinar:art-59"], "anos": [2026], "salto": 1}]


def _ctx():
    return montar_contexto(TRECHOS, FATOS, [{"orgao": "Consepe"}])


def _resp(*afirmacoes, suficiente=True, conflitos=""):
    return Resposta(afirmacoes=[Afirmacao(texto=t, fontes=f) for t, f in afirmacoes],
                    suficiente=suficiente, conflitos=conflitos)


def test_contexto_numera_fontes():
    texto, fontes = _ctx()
    assert set(fontes) == {"T1", "F1", "C1"} and "[T1] Código Disciplinar (2026), Art. 59" in texto


def test_numero_por_extenso_na_fonte_confirma_algarismo():
    _, fontes = _ctx()
    out = verificar(_resp(("O recurso vai ao Consepe em 5 dias úteis (Art. 59).", ["[T1]"])), fontes)
    assert out["aprovadas"] and not out["removidas"] and out["taxa_suporte"] == 1.0


def test_descarta_numero_inventado_e_fonte_inexistente():
    _, fontes = _ctx()
    out = verificar(_resp(("O prazo é de 10 dias úteis.", ["T1"]),
                          ("Cabe recurso ao Consepe.", ["F1"]),
                          ("A taxa é de R$ 50.", ["T9"])), fontes)
    assert [a["texto"] for a in out["aprovadas"]] == ["Cabe recurso ao Consepe."]
    assert {r["motivo"].split()[0] for r in out["removidas"]} == {"número(s)", "sem"}
    assert "Resposta parcial" in out["resposta"] and out["taxa_suporte"] == round(1 / 3, 3)


def test_sem_afirmacao_valida_vira_sem_resposta():
    _, fontes = _ctx()
    out = verificar(_resp(("Inventado.", []), suficiente=False), fontes)
    assert out["resposta"] == SEM_RESPOSTA and not out["suficiente"]


def test_conflito_entre_fontes_aparece_na_resposta():
    _, fontes = _ctx()
    out = verificar(_resp(("Cabe recurso ao Consepe.", ["F1"]), conflitos="Manual 2026 x Regulamento 2020"), fontes)
    assert "divergem" in out["resposta"]


def test_numeros_normaliza_e_le_extenso():
    assert numeros("NF >= 6,0 e 75%") == {"6", "75"}
    assert {"75", "2"} <= numeros("75% (setenta e cinco por cento), segunda instância", extenso=True)
