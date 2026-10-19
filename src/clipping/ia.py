"""Triagem e resumos com IA.

Provedor padrão: Google Gemini, no plano gratuito (variável GEMINI_API_KEY).
Também aceita a API do Claude (ANTHROPIC_API_KEY), escolhendo `provedor: anthropic`
no config.yaml. Sem chave, ou se a cota acabar, o pipeline segue com a pontuação
por regras e resumos extrativos (primeiras frases do texto original).
"""
from __future__ import annotations

import json
import os
import re
import time

import requests

from .util import carregar_yaml, log, sem_travessao, truncar

ESCOPOS = ("DF", "Brasil", "Américas", "Internacional")
URL_GEMINI = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"

CONTEXTO = (
    "Você apoia a equipe de uma Residência Multiprofissional em Vigilância em Saúde no Distrito Federal, "
    "Brasil, na produção de um clipping epidemiológico semanal baseado em fontes oficiais e de sinal precoce. "
    "O público é formado por profissionais de vigilância epidemiológica."
)

REGRAS_TEXTO = (
    "Regras de redação: português do Brasil, registro técnico e formal; não use travessões; "
    "não inclua opiniões, recomendações nem conclusões que não estejam na fonte; não invente dados; "
    "reproduza números, datas e locais exatamente como aparecem no texto; use nomes de agravos e "
    "termos da vigilância brasileira (por exemplo, SRAG, notificação, caso confirmado, óbito)."
)


class AcessoNegado(RuntimeError):
    """Chave recusada pelo provedor (inválida, restrita ou com a API desativada)."""


class CotaEsgotada(RuntimeError):
    """A cota gratuita do dia acabou: o restante da edição segue sem IA."""


_ultima_chamada = 0.0
_cota_esgotada = False


def _cfg() -> dict:
    return carregar_yaml("config.yaml").get("ia", {})


def _provedor() -> str:
    return str(_cfg().get("provedor", "gemini")).lower()


def disponivel() -> bool:
    if _cota_esgotada:
        return False
    chave = "ANTHROPIC_API_KEY" if _provedor() == "anthropic" else "GEMINI_API_KEY"
    return bool(os.environ.get(chave))


def _modelos(tipo: str) -> list[str]:
    valor = _cfg().get(f"modelos_{tipo}") or _cfg().get(f"modelo_{tipo}")
    return [valor] if isinstance(valor, str) else list(valor or [])


def _pausa() -> None:
    """Respeita o limite de requisições por minuto do plano gratuito."""
    global _ultima_chamada
    intervalo = float(_cfg().get("pausa_segundos", 7))
    espera = _ultima_chamada + intervalo - time.time()
    if espera > 0:
        time.sleep(espera)
    _ultima_chamada = time.time()


def _gemini(modelo: str, sistema: str, usuario: str, max_tokens: int, json_saida: bool) -> str:
    corpo = {
        "systemInstruction": {"parts": [{"text": sistema}]},
        "contents": [{"role": "user", "parts": [{"text": usuario}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens},
    }
    if json_saida:
        corpo["generationConfig"]["responseMimeType"] = "application/json"
    r = requests.post(URL_GEMINI.format(modelo=modelo), json=corpo, timeout=120,
                      headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]})
    if r.status_code in (401, 403):
        try:
            err = r.json().get("error", {})
            motivo = err.get("message", "")
            detalhes = [d.get("reason", "") for d in err.get("details", []) if isinstance(d, dict)]
            motivo = f"{err.get('status', '')} {' '.join(x for x in detalhes if x)}: {motivo}".strip()
        except ValueError:
            motivo = r.text[:400]
        raise AcessoNegado(f"{r.status_code} {motivo}")
    if r.status_code == 429:
        raise PermissionError(f"429: {r.text[:300]}")
    if r.status_code in (400, 404):
        raise LookupError(f"{r.status_code}: {r.text[:300]}")
    r.raise_for_status()
    dados = r.json()
    partes = dados.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    texto = "".join(p.get("text", "") for p in partes if not p.get("thought"))
    if not texto:
        raise RuntimeError(f"Resposta vazia: {json.dumps(dados)[:300]}")
    return texto


def _anthropic(modelo: str, sistema: str, usuario: str, max_tokens: int, json_saida: bool) -> str:
    import anthropic

    resp = anthropic.Anthropic().messages.create(
        model=modelo, max_tokens=max_tokens, system=sistema, messages=[{"role": "user", "content": usuario}])
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def _chamar(tipo: str, sistema: str, usuario: str, max_tokens: int = 2000, json_saida: bool = True) -> str:
    """Tenta cada modelo configurado; em limite por minuto, aguarda e repete."""
    global _cota_esgotada
    chamar = _anthropic if _provedor() == "anthropic" else _gemini
    ultimo_erro = None
    for modelo in _modelos(tipo):
        for tentativa in range(3):
            _pausa()
            try:
                return chamar(modelo, sistema, usuario, max_tokens, json_saida)
            except AcessoNegado as erro:  # chave recusada: não adianta repetir nem trocar de modelo
                _cota_esgotada = True
                log.error("A chave GEMINI_API_KEY foi recusada pelo Google. Motivo informado: %s", erro)
                log.error("A edição segue sem IA. Verifique a chave em https://aistudio.google.com/apikey.")
                raise
            except LookupError as erro:  # modelo inexistente ou requisição recusada: próximo modelo
                log.warning("Modelo %s indisponível: %s", modelo, erro)
                ultimo_erro = erro
                break
            except PermissionError as erro:  # limite atingido
                ultimo_erro = erro
                if "PerDay" in str(erro) or "per day" in str(erro).lower():
                    log.warning("Cota diária do modelo %s esgotada.", modelo)
                    break
                log.warning("Limite por minuto em %s; aguardando.", modelo)
                time.sleep(30 * (tentativa + 1))
            except Exception as erro:  # noqa: BLE001
                ultimo_erro = erro
                log.warning("Falha em %s (tentativa %d): %s", modelo, tentativa + 1, erro)
                time.sleep(5 * (tentativa + 1))
    if isinstance(ultimo_erro, PermissionError):
        _cota_esgotada = True
        raise CotaEsgotada(str(ultimo_erro))
    raise RuntimeError(f"IA indisponível: {ultimo_erro}")


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
        "O clipping é lido por gestores de vigilância em saúde do Distrito Federal e deve responder à "
        "pergunta: com o que devo me preocupar nesta semana? Avalie cada notícia abaixo e atribua uma nota "
        "de 0 a 10 conforme ela ajude a responder essa pergunta. Critérios, em ordem de importância: "
        "(1) ocorrência concreta no Distrito Federal ou no seu entorno, inclusive desastres e eventos "
        "climáticos com impacto na população (alagamentos, temporais, calor extremo, baixa umidade, "
        "queimadas); (2) evento novo, inusitado ou com potencial de emergência de saúde pública de "
        "importância internacional; (3) mudança na situação de agravos acompanhados de perto, como "
        "atualizações de SRAG e vírus respiratórios (InfoGripe, boletins), arboviroses, sarampo e El Niño; "
        "(4) novos boletins, alertas, notas técnicas e avaliações de risco oficiais; (5) magnitude "
        "(aumento de casos, óbitos, novos países ou territórios). Dê notas baixas (0 a 3) a estudos e "
        "pesquisas, matérias explicativas, implantação de programas ou metodologias, seminários, "
        "capacitações, campanhas genéricas, notícias institucionais e publicações sem um evento ou "
        "mudança de situação concreta.\n\n"
        "Para cada notícia, informe também: agravo (nome curto em português), escopo (um entre DF, "
        "Brasil, Américas, Internacional, conforme o local do evento) e evento (uma chave curta, em "
        "minúsculas e com hífens, que seja idêntica para notícias que tratam do mesmo evento, por "
        "exemplo 'ebola-rdc' ou 'sarampo-americas').\n\n"
        "Responda somente com um array JSON no formato "
        '[{"id": "...", "nota": 0, "agravo": "...", "escopo": "...", "evento": "..."}].\n\n'
        "Notícias:\n" + json.dumps(lista, ensure_ascii=False)
    )
    resposta = _chamar("triagem", sistema, usuario, 6000)
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
        "Informe também 'local' (país, estado ou município do evento, em português), 'agravo' "
        "(nome curto em português), 'paises' (lista de códigos ISO 3166-1 alfa-3 dos países onde o evento "
        "ocorre, no máximo seis, por exemplo [\"COD\", \"UGA\"]) e 'uf' (sigla da unidade federativa "
        "quando o evento for no Brasil e restrito a uma UF, por exemplo \"DF\"; caso contrário, vazio).\n"
        'Responda somente com JSON: {"titulo": "...", "resumo": "...", "local": "...", "agravo": "...", '
        '"paises": ["..."], "uf": ""}\n\n'
        f"Fonte: {item['fonte']}\nData de publicação: {item['data']}\nTítulo original: {item['titulo']}\n"
        f"URL: {item['url']}\n\nTexto:\n{truncar(texto or item.get('descricao', ''), 16000)}"
    )
    r = _extrair_json(_chamar("resumo", sistema, usuario, 4000))
    saida = {k: sem_travessao(str(r.get(k, "")).strip()) for k in ("titulo", "resumo", "local", "agravo")}
    saida["paises"] = r.get("paises") if isinstance(r.get("paises"), list) else []
    saida["uf"] = str(r.get("uf") or "")
    return saida


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
    texto = _chamar("resumo", sistema, usuario, 3000, json_saida=False)
    return sem_travessao(texto.strip().strip('"'))


_RUIDO = re.compile(
    r"(leia (tamb[eé]m|mais)|veja (tamb[eé]m|mais)|confira (tamb[eé]m)?|saiba mais|siga o|inscreva-se|"
    r"publicidade|assine|compartilhe|clique aqui|whatsapp|newsletter|read more|related:|sign up)", re.I)


def resumo_extrativo(texto: str, limite: int = 620) -> str:
    """Alternativa sem IA: primeiras frases do texto original, sem chamadas para outras matérias."""
    linhas = [l.strip() for l in (texto or "").splitlines()]
    # Descarta linhas de navegação ("Leia também", chamadas, créditos curtos) antes de juntar o texto
    linhas = [l for l in linhas if l and not _RUIDO.search(l) and len(l) > 40]
    texto = re.sub(r"\s+", " ", " ".join(linhas)).strip()
    frases = re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-Ú0-9])", texto)
    saida = ""
    for f in frases:
        if _RUIDO.search(f):
            continue
        if len(saida) + len(f) > limite:
            break
        saida += (" " if saida else "") + f
    return sem_travessao(saida or truncar(texto, limite))


def testar() -> bool:
    """Faz uma chamada mínima a cada modelo configurado e mostra o resultado no log."""
    chave = "ANTHROPIC_API_KEY" if _provedor() == "anthropic" else "GEMINI_API_KEY"
    valor = os.environ.get(chave, "")
    if not valor:
        log.error("O segredo %s não está cadastrado no repositório.", chave)
        print(f"::error::O segredo {chave} não está cadastrado no repositório.")
        return False
    log.info("Chave %s encontrada (%d caracteres, começa com %s...).", chave, len(valor), valor[:4])
    print(f"::notice::Chave {chave} encontrada ({len(valor)} caracteres, começa com {valor[:4]}...)")
    chamar = _anthropic if _provedor() == "anthropic" else _gemini
    ok = False
    for tipo in ("triagem", "resumo"):
        for modelo in _modelos(tipo):
            try:
                resp = chamar(modelo, "Responda apenas com a palavra OK.", "Teste de conexão.", 50, False)
                log.info("Modelo %s: funcionando (resposta: %s)", modelo, resp.strip()[:20])
                print(f"::notice::Modelo {modelo}: funcionando")
                ok = True
            except Exception as erro:  # noqa: BLE001
                log.error("Modelo %s: falhou. %s", modelo, erro)
                print(f"::error::Modelo {modelo}: {str(erro)[:300]}")
            time.sleep(2)
    return ok
