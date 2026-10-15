"""Triagem e resumos com a API do Claude (Anthropic).

Sem a variável ANTHROPIC_API_KEY, o pipeline continua funcionando com a
pontuação por regras e resumos extrativos (primeiras frases do texto original).
"""
from __future__ import annotations

import json
import os
import re
import time

from .util import carregar_yaml, log, sem_travessao, truncar

ESCOPOS = ("DF", "Brasil", "Américas", "Internacional")

CONTEXTO = (
    "Você apoia a equipe de uma Residência Multiprofissional em Vigilância em Saúde no Distrito Federal, "
    "Brasil, na produção de um clipping epidemiológico semanal baseado exclusivamente em fontes oficiais. "
    "O público é formado por profissionais de vigilância epidemiológica."
)

REGRAS_TEXTO = (
    "Regras de redação: português do Brasil, registro técnico e formal; não use travessões; "
    "não inclua opiniões, recomendações nem conclusões que não estejam na fonte; não invente dados; "
    "reproduza números, datas e locais exatamente como aparecem no texto; use nomes de agravos e "
    "termos da vigilância brasileira (por exemplo, SRAG, notificação, caso confirmado, óbito)."
)


def disponivel() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _cfg() -> dict:
    return carregar_yaml("config.yaml").get("ia", {})


def _chamar(modelo: str, sistema: str, usuario: str, max_tokens: int = 1500) -> str:
    import anthropic

    cliente = anthropic.Anthropic()
    for tentativa in range(3):
        try:
            resp = cliente.messages.create(
                model=modelo, max_tokens=max_tokens, system=sistema,
                messages=[{"role": "user", "content": usuario}],
            )
            return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        except Exception as erro:  # noqa: BLE001
            log.warning("API (tentativa %d) falhou: %s", tentativa + 1, erro)
            time.sleep(4 * (tentativa + 1))
    raise RuntimeError("API indisponível após 3 tentativas")


def _extrair_json(texto: str):
    m = re.search(r"```(?:json)?\s*(.+?)```", texto, re.S)
    if m:
        texto = m.group(1)
    inicio = min((i for i in (texto.find("["), texto.find("{")) if i >= 0), default=-1)
    if inicio < 0:
        raise ValueError("Resposta sem JSON")
    fim = max(texto.rfind("]"), texto.rfind("}"))
    return json.loads(texto[inicio:fim + 1])


def triar(candidatos: list[dict]) -> dict[str, dict]:
    """Atribui nota de relevância (0 a 10), agravo, escopo e chave de evento a cada candidata."""
    lista = [{
        "id": c["id"], "titulo": c["titulo"], "fonte": c["fonte"], "data": c["data"],
        "descricao": truncar(c.get("descricao", ""), 450),
    } for c in candidatos]
    sistema = CONTEXTO + " Sua tarefa agora é triar notícias por relevância epidemiológica."
    usuario = (
        "Avalie cada notícia abaixo e atribua uma nota de 0 a 10 de relevância epidemiológica para o "
        "clipping da semana. Critérios, em ordem de importância: (1) risco ou impacto para o Brasil e o "
        "Distrito Federal; (2) evento novo, inusitado ou com potencial de emergência de saúde pública "
        "de importância internacional; (3) agravo de notificação imediata ou compulsória; (4) magnitude "
        "e mudança recente na situação (aumento de casos, óbitos, novos países ou territórios); "
        "(5) alertas, avaliações de risco e atualizações epidemiológicas oficiais. Dê notas baixas (0 a 3) "
        "a notícias institucionais, parcerias, eventos, capacitações, prêmios, campanhas genéricas de "
        "promoção da saúde e publicações sem um evento epidemiológico concreto.\n\n"
        "Para cada notícia, informe também: agravo (nome curto em português), escopo (um entre DF, "
        "Brasil, Américas, Internacional, conforme o local do evento) e evento (uma chave curta, em "
        "minúsculas e com hífens, que seja idêntica para notícias que tratam do mesmo evento, por "
        "exemplo 'ebola-rdc' ou 'sarampo-americas').\n\n"
        "Responda somente com um array JSON no formato "
        '[{"id": "...", "nota": 0, "agravo": "...", "escopo": "...", "evento": "..."}].\n\n'
        "Notícias:\n" + json.dumps(lista, ensure_ascii=False)
    )
    resposta = _chamar(_cfg().get("modelo_triagem", "claude-haiku-5-5"), sistema, usuario, 4000)
    saida = {}
    for r in _extrair_json(resposta):
        if not isinstance(r, dict) or "id" not in r:
            continue
        escopo = r.get("escopo") if r.get("escopo") in ESCOPOS else None
        saida[r["id"]] = {
            "nota": float(r.get("nota", 0) or 0),
            "agravo": sem_travessao(str(r.get("agravo", "")).strip()),
            "escopo": escopo,
            "evento": str(r.get("evento", "")).strip().lower() or r["id"],
        }
    return saida


def resumir(item: dict, texto: str) -> dict:
    """Gera título (quem, quando, onde) e resumo técnico de uma notícia."""
    sistema = CONTEXTO + " " + REGRAS_TEXTO
    usuario = (
        "Escreva o título e o resumo desta notícia para o clipping.\n"
        "Título: até 120 caracteres, informativo, seguindo a regra do quem, quando e onde (quem ou qual "
        "agravo, o que ocorreu, onde e quando, com mês e ano ou a data do evento).\n"
        "Resumo: um único parágrafo de 70 a 110 palavras com os fatos centrais: situação epidemiológica, "
        "números de casos e óbitos quando houver, local, período e a medida ou posicionamento oficial "
        "descrito na fonte. Se o texto disponível for insuficiente, resuma apenas o que estiver nele.\n"
        "Informe também 'local' (país, estado ou município do evento, em português) e 'agravo' "
        "(nome curto em português).\n"
        'Responda somente com JSON: {"titulo": "...", "resumo": "...", "local": "...", "agravo": "..."}\n\n'
        f"Fonte: {item['fonte']}\nData de publicação: {item['data']}\nTítulo original: {item['titulo']}\n"
        f"URL: {item['url']}\n\nTexto:\n{truncar(texto or item.get('descricao', ''), 14000)}"
    )
    r = _extrair_json(_chamar(_cfg().get("modelo_resumo", "claude-sonnet-5-5"), sistema, usuario, 1200))
    return {k: sem_travessao(str(r.get(k, "")).strip()) for k in ("titulo", "resumo", "local", "agravo")}


def panorama(destaques: list[dict], rotulo_se: str, periodo: str) -> str:
    """Parágrafo de abertura com a síntese da semana."""
    itens = "\n".join(f"- [{d['escopo']}] {d['titulo']}: {d['resumo']}" for d in destaques)
    sistema = CONTEXTO + " " + REGRAS_TEXTO
    usuario = (
        f"Com base apenas nos resumos abaixo, escreva o parágrafo de abertura do clipping da {rotulo_se} "
        f"({periodo}). Um único parágrafo de 80 a 120 palavras que sintetize os principais eventos da "
        "semana, priorizando o que tem maior relevância para o Brasil e o Distrito Federal. Não use "
        "listas, não cite o próprio clipping e não faça recomendações. Responda apenas com o parágrafo.\n\n"
        + itens
    )
    texto = _chamar(_cfg().get("modelo_resumo", "claude-sonnet-5-5"), sistema, usuario, 600)
    return sem_travessao(texto.strip().strip('"'))


def resumo_extrativo(texto: str, limite: int = 620) -> str:
    """Alternativa sem IA: primeiras frases do texto original."""
    texto = re.sub(r"\s+", " ", texto or "").strip()
    frases = re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-Ú0-9])", texto)
    saida = ""
    for f in frases:
        if len(saida) + len(f) > limite:
            break
        saida += (" " if saida else "") + f
    return sem_travessao(saida or truncar(texto, limite))
