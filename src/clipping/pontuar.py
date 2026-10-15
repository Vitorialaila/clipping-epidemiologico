"""Pontuação de relevância epidemiológica por regras explícitas (config/criterios.yaml)."""
from __future__ import annotations

import re
from functools import lru_cache

from .util import carregar_yaml, normalizar


def _padrao(termos: list[str]) -> re.Pattern:
    partes = sorted({re.escape(normalizar(str(t))) for t in termos}, key=len, reverse=True)
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
    return c, agravos, eventos, locais, ajustes


def limiares() -> tuple[float, float]:
    c = _criterios()[0]
    return float(c.get("limiar_feed", 4)), float(c.get("limiar_clipping", 5))


def pontuar(titulo: str, descricao: str, fonte: dict) -> dict:
    """Retorna pontuação, agravos identificados, escopo geográfico e termos de evento."""
    _, agravos, eventos, locais, ajustes = _criterios()
    titulo_n = normalizar(titulo)
    texto = titulo_n + " " + normalizar(descricao)

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

    if not achados and pts_evento < 2.5:
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

    for peso, padrao in ajustes:
        if padrao.search(texto):
            pts += peso

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
