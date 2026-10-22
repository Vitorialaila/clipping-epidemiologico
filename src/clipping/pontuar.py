"""Pontuação de relevância epidemiológica por regras explícitas (config/criterios.yaml)."""
from __future__ import annotations

import re
from datetime import date
from functools import lru_cache

from .util import carregar_yaml, normalizar


def _padrao(termos: list[str]) -> re.Pattern:
    """Termos exatos; um asterisco no fim indica prefixo (ex.: "shigel*" casa shigella, shigelose, shigellosis)."""
    partes = set()
    for t in termos:
        t = normalizar(str(t)).strip()
        partes.add(re.escape(t[:-1]) + r"\w*" if t.endswith("*") else re.escape(t))
    partes = sorted(partes, key=len, reverse=True)
    return re.compile(r"(?<![\w-])(" + "|".join(partes) + r")(?![\w])")


@lru_cache(maxsize=1)
def _criterios():
    c = carregar_yaml("criterios.yaml")
    agravos = [(a["nome"], float(a["peso"]), _padrao(a["termos"])) for a in c["agravos"]]
    eventos = [(float(e["peso"]), _padrao(e["termos"])) for e in c["eventos"]]
    ride = c.get("ride_df", [])
    locais = []
    for nome, v in c["localidades"].items():
        termos = list(v["termos"])
        if nome == "RIDE":
            for m in ride:
                termos += [m["nome"]] + m.get("termos", [])
        locais.append((nome, float(v["peso"]), _padrao(termos)))
    ajustes = [(float(a["peso"]), _padrao(a["termos"])) for a in c.get("ajustes", [])]
    exclusoes = _padrao(c["exclusoes"]) if c.get("exclusoes") else None
    return c, agravos, eventos, locais, ajustes, exclusoes


def limiares() -> tuple[float, float]:
    c = _criterios()[0]
    return float(c.get("limiar_feed", 4)), float(c.get("limiar_clipping", 5))


def pontuar(titulo: str, descricao: str, fonte: dict, ano_referencia: int | None = None) -> dict:
    """Retorna pontuação, agravos identificados, escopo geográfico e termos de evento."""
    c, agravos, eventos, locais, ajustes, exclusoes = _criterios()
    titulo_n = normalizar(titulo)
    # Fontes cujo resumo traz o sumário da edição inteira (ex.: MMWR) são avaliadas só pelo título
    corpo = "" if fonte.get("so_titulo") else normalizar(descricao)
    if exclusoes:  # expressões em sentido figurado ou fora de contexto ("lei seca", "cortina de fumaça")
        titulo_n = exclusoes.sub(" ", titulo_n)
        corpo = exclusoes.sub(" ", corpo)
    texto = titulo_n + " " + corpo
    canal_alerta = bool(fonte.get("canal_alerta"))

    achados = []
    for nome, peso, padrao in agravos:
        no_titulo = bool(padrao.search(titulo_n))
        if no_titulo:
            achados.append((peso + 0.5, nome))
        elif padrao.search(texto):
            # Citado só no corpo do texto: pesa menos (evita falsos positivos, como menções de passagem)
            achados.append((peso * 0.5, nome))
    achados.sort(reverse=True)
    nomes_agravos = [n for _, n in achados]

    pts_evento, termos_evento = 0.0, []
    for peso, padrao in eventos:
        m = padrao.search(texto)
        if m:
            pts_evento += peso
            termos_evento.append(m.group(1))
    pts_evento = min(pts_evento, 6.0)

    if not achados and pts_evento < 2.5 and not canal_alerta:
        return {"pontuacao": 0.0, "agravos": [], "escopo": fonte.get("escopo_padrao", "Internacional"),
                "termos_evento": termos_evento}

    pts = float(fonte.get("peso", 1))
    if achados:
        pts += achados[0][0] + min(0.5 * (len(achados) - 1), 1.0)
    pts += pts_evento

    # O escopo padrão da fonte (ex.: MS = Brasil) só é substituído por uma localidade mais próxima
    escopo = fonte.get("escopo_padrao") or "Internacional"
    pts_local = next((p for n, p, _ in locais if n == escopo), 0.0)
    for nome, peso, padrao in locais:
        if peso > pts_local and padrao.search(texto):
            escopo, pts_local = nome, peso
    pts += pts_local

    # Penalidades olham só o título: é ele que diz do que a matéria trata; o corpo de notícias sobre eventos
    # costuma trazer jargão institucional ("strengthen", "pesquisadores") que não muda a natureza da notícia
    for peso, padrao in ajustes:
        if padrao.search(titulo_n):
            pts += peso

    # Relato retrospectivo: o título cita apenas anos já encerrados (ex.: "January 2024-June 2025" em 2026)
    anos = [int(a) for a in re.findall(r"(?<!\d)(20\d{2})(?!\d)", titulo)]
    if anos and max(anos) < (ano_referencia or date.today().year):
        pts += float(c.get("penalidade_retrospectivo", -4))

    # Canais oficiais de alerta (DON da OMS, alertas da OPAS, atualizações e avaliações do ECDC): cada item
    # é, por definição, um evento; nunca ficam abaixo do piso, salvo relatos retrospectivos
    if canal_alerta and not (anos and max(anos) < (ano_referencia or date.today().year)):
        pts = max(pts, float(c.get("piso_canal_alerta", 6)))

    return {"pontuacao": round(max(pts, 0.0), 2), "agravos": nomes_agravos[:3], "escopo": escopo,
            "termos_evento": termos_evento}


@lru_cache(maxsize=1)
def _municipios():
    c = _criterios()[0]
    df = next(v for n, v in c["localidades"].items() if n == "DF")
    lista = [("Distrito Federal", _padrao(df["termos"]))]
    for m in c.get("ride_df", []):
        lista.append((f'{m["nome"]} ({m["uf"]})', _padrao([m["nome"]] + m.get("termos", []))))
    return lista


def municipios(titulo: str, descricao: str = "") -> list[str]:
    """Municípios da RIDE-DF (incluindo o Distrito Federal) citados na notícia."""
    texto = normalizar(titulo) + " " + normalizar(descricao)
    return [nome for nome, padrao in _municipios() if padrao.search(texto)]
