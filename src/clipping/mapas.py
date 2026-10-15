"""Mapas em SVG gerados localmente.

Bases: Natural Earth 1:50m (domínio público) e malha das UFs derivada do IBGE,
simplificadas em assets/geo/. Nenhum serviço externo é consultado.
"""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache

from .util import ASSETS, normalizar

COR_ESCOPO = {"DF": "#B8921A", "Brasil": "#0B7A32", "Américas": "#1E4E8C", "Internacional": "#5C6B82"}
TERRA, BORDA, MAR, TINTA = "#E3E6EB", "#FFFFFF", "#F6F7F9", "#0A3570"
DESTAQUE = "#9FB2CC"  # países e UFs com evento: um único tom, sem cores por escopo


@lru_cache(maxsize=1)
def _mundo() -> dict:
    return json.loads((ASSETS / "geo" / "mundo.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _ufs() -> dict:
    return json.loads((ASSETS / "geo" / "brasil_uf.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- localização

@lru_cache(maxsize=1)
def _indice_nomes():
    nomes = {}
    extras = {"RDC": "COD", "DRC": "COD", "CONGO-KINSHASA": "COD", "EUA": "USA", "ESTADOS UNIDOS": "USA",
              "UNITED STATES": "USA", "REINO UNIDO": "GBR", "UNITED KINGDOM": "GBR", "RUSSIA": "RUS",
              "FEDERACAO RUSSA": "RUS", "RUSSIAN FEDERATION": "RUS", "COREIA DO SUL": "KOR", "SOUTH KOREA": "KOR",
              "BOLIVIA": "BOL", "VENEZUELA": "VEN", "IRA": "IRN", "IRAN": "IRN", "VIETNA": "VNM", "VIETNAM": "VNM",
              "TANZANIA": "TZA", "LAOS": "LAO", "SIRIA": "SYR", "SYRIA": "SYR", "BRAZIL": "BRA"}
    for cod, p in _mundo().items():
        for n in (p["n"], p["en"]):
            if n and len(n) > 3:
                nomes[normalizar(n)] = cod
    for n, cod in extras.items():
        nomes[normalizar(n)] = cod
    chaves = sorted(nomes, key=len, reverse=True)
    padrao = re.compile(r"(?<![\w-])(" + "|".join(re.escape(k) for k in chaves) + r")(?![\w])")
    return nomes, padrao


@lru_cache(maxsize=1)
def _indice_ufs():
    nomes = {normalizar(p["n"]): s for s, p in _ufs().items()}
    nomes.update({"brasilia": "DF", "ses-df": "DF", "ride-df": "DF", "entorno do df": "DF"})
    nomes.pop("para", None)
    nomes["estado do para"] = "PA"
    chaves = sorted(nomes, key=len, reverse=True)
    return nomes, re.compile(r"(?<![\w-])(" + "|".join(re.escape(k) for k in chaves) + r")(?![\w])")


def localizar(texto: str, escopo: str = "") -> tuple[list[str], str]:
    """Identifica países (ISO3) e UF citados num texto. Usado quando a IA não informa."""
    alvo = normalizar(texto)
    nomes, padrao = _indice_nomes()
    paises = []
    for m in padrao.finditer(alvo):
        cod = nomes[m.group(1)]
        if cod not in paises:
            paises.append(cod)
    uf = ""
    if escopo in ("DF", "Brasil") or "BRA" in paises:
        n_uf, p_uf = _indice_ufs()
        m = p_uf.search(alvo)
        uf = n_uf[m.group(1)] if m else ("DF" if escopo == "DF" else "")
        if "BRA" not in paises:
            paises.insert(0, "BRA")
    return paises[:6], uf


def validar(paises, uf) -> tuple[list[str], str]:
    mundo, ufs = _mundo(), _ufs()
    paises = [str(p).upper().strip() for p in (paises or []) if str(p).upper().strip() in mundo]
    uf = str(uf or "").upper().strip()
    return paises[:8], (uf if uf in ufs else "")


# ---------------------------------------------------------------- projeções

def _equal_earth(lon: float, lat: float) -> tuple[float, float]:
    a1, a2, a3, a4 = 1.340264, -0.081106, 0.000893, 0.003796
    m = math.sqrt(3) / 2
    lam, phi = math.radians(lon), math.radians(lat)
    t = math.asin(m * math.sin(phi))
    t2, t6 = t * t, t ** 6
    x = 2 * math.sqrt(3) * lam * math.cos(t) / (3 * (a1 + 3 * a2 * t2 + t6 * (7 * a3 + 9 * a4 * t2)))
    y = t * (a1 + a2 * t2 + t6 * (a3 + a4 * t2))
    return x, -y


def _caminho(poligonos, proj) -> str:
    partes = []
    for anel in poligonos:
        pts = [proj(x, y) for x, y in anel]
        partes.append("M" + "L".join(f"{px:.1f},{py:.1f}" for px, py in pts) + "Z")
    return "".join(partes)


def _marcador(x, y, rotulo, cor, r=11.0, texto="#fff") -> str:
    return (f'<g><circle cx="{x:.1f}" cy="{y:.1f}" r="{r + 2.2:.1f}" fill="#fff" opacity=".9"/>'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{cor}"/>'
            f'<text x="{x:.1f}" y="{y + r * 0.42:.1f}" text-anchor="middle" font-family="IBM Plex Sans, sans-serif" '
            f'font-weight="700" font-size="{r * 1.15:.1f}" fill="{texto}">{rotulo}</text></g>')


def _afastar(pontos: list[list[float]], dist: float) -> None:
    """Afasta marcadores sobrepostos, preservando a ordem."""
    for _ in range(30):
        mexeu = False
        for i in range(len(pontos)):
            for j in range(i + 1, len(pontos)):
                dx, dy = pontos[j][0] - pontos[i][0], pontos[j][1] - pontos[i][1]
                d = math.hypot(dx, dy)
                if d < dist:
                    ang = math.atan2(dy, dx) if d > 0.01 else (j * 2.4)
                    empurra = (dist - d) / 2 + 0.5
                    pontos[i][0] -= math.cos(ang) * empurra
                    pontos[i][1] -= math.sin(ang) * empurra
                    pontos[j][0] += math.cos(ang) * empurra
                    pontos[j][1] += math.sin(ang) * empurra
                    mexeu = True
        if not mexeu:
            break


# ---------------------------------------------------------------- mapa da semana

TEMAS = {
    "claro": {"mar": MAR, "terra": TERRA, "borda": BORDA, "grade": "#ECEEF2", "opac": "1",
              "quadro": "#fff", "quadro_borda": "#CBD2DC", "uf": TERRA, "uf_borda": "#fff", "rotulo": "#6A7282",
              "cores": {e: DESTAQUE for e in COR_ESCOPO}, "marcador": TINTA, "texto_marcador": "#fff"},
    "escuro": {"mar": "none", "terra": "rgba(255,255,255,0.08)", "borda": "rgba(255,255,255,0.16)",
               "grade": "rgba(255,255,255,0.05)", "opac": "1", "quadro": "rgba(6,27,63,0.6)",
               "quadro_borda": "rgba(255,255,255,0.25)", "uf": "rgba(255,255,255,0.10)",
               "uf_borda": "rgba(255,255,255,0.22)", "rotulo": "rgba(255,255,255,0.7)",
               "cores": {e: "rgba(255,255,255,0.45)" for e in COR_ESCOPO}, "marcador": "#FFFFFF",
               "texto_marcador": TINTA},
}


def mapa_semana(eventos: list[dict], largura: int = 1000, tema: str = "claro") -> str:
    """Mapa-múndi com os destaques numerados. eventos: [{n, paises, uf, escopo}]."""
    t = TEMAS.get(tema, TEMAS["claro"])
    cores = t.get("cores", COR_ESCOPO)
    mundo = _mundo()
    escala = largura / (2 * 2.7066)
    lat_max, lat_min = 78, -56
    _, y_top = _equal_earth(0, lat_max)
    _, y_bot = _equal_earth(0, lat_min)
    altura = round((y_bot - y_top) * escala)

    def proj(lon, lat):
        x, y = _equal_earth(lon, lat)
        return (x + 2.7066) * escala, (y - y_top) * escala

    destaque = {}
    for e in eventos:
        for p in e.get("paises", []):
            destaque.setdefault(p, cores.get(e["escopo"], TINTA))
    camadas = [f'<rect width="{largura}" height="{altura}" fill="{t["mar"]}"/>']
    # graticula leve
    for lat in range(-40, 80, 20):
        pts = [proj(lon, lat) for lon in range(-180, 181, 6)]
        camadas.append(f'<path d="M{"L".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" fill="none" stroke="{t["grade"]}" stroke-width=".6"/>')
    for cod, p in mundo.items():
        cor = destaque.get(cod)
        estilo = f'fill="{cor}" fill-opacity="{t["opac"]}"' if cor else f'fill="{t["terra"]}"'
        camadas.append(f'<path d="{_caminho(p["p"], proj)}" {estilo} stroke="{t["borda"]}" stroke-width=".5"/>')

    tem_brasil = any(e["escopo"] in ("DF", "Brasil") or e.get("uf") for e in eventos)
    pontos, rotulos = [], []
    insercao = ""
    if tem_brasil:
        insercao, pts_br = _insercao_brasil([e for e in eventos if e["escopo"] in ("DF", "Brasil") or e.get("uf")],
                                            x0=12, y0=altura * 0.50, lado=altura * 0.47, t=t)
    for e in eventos:
        if e["escopo"] in ("DF", "Brasil") or e.get("uf"):
            continue
        cods = [c for c in e.get("paises", []) if c in mundo]
        if not cods:
            continue
        lon, lat = mundo[cods[0]]["c"]
        x, y = proj(lon, lat)
        pontos.append([x, y])
        rotulos.append((e["n"], cores.get(e["escopo"], TINTA)))
    _afastar(pontos, 27)
    marcas = [_marcador(x, y, n, t["marcador"], texto=t["texto_marcador"]) for (x, y), (n, cor) in zip(pontos, rotulos)]
    if tem_brasil:
        marcas.append(insercao)
        marcas.extend(pts_br)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {largura} {altura}" width="{largura}" height="{altura}" '
            f'role="img" aria-label="Mapa dos destaques da semana">{"".join(camadas)}{"".join(marcas)}</svg>')


def _insercao_brasil(eventos, x0, y0, lado, t=None):
    """Quadro com o Brasil e as UFs, posicionado sobre o Pacífico Sul."""
    ufs = _ufs()
    t = t or TEMAS["claro"]
    cores = t.get("cores", COR_ESCOPO)
    lon0, lon1, lat0, lat1 = -74.5, -34.0, -34.5, 6.0
    kx = math.cos(math.radians(-14))
    larg = lado * (lon1 - lon0) * kx / (lat1 - lat0)
    pad = 10

    def proj(lon, lat):
        return (x0 + pad + (lon - lon0) * kx * (lado / (lat1 - lat0)),
                y0 + pad + (lat1 - lat) * (lado / (lat1 - lat0)))

    marcadas = {}
    for e in eventos:
        if e.get("uf"):
            marcadas.setdefault(e["uf"], cores.get(e["escopo"], TINTA))
    partes = [f'<rect x="{x0}" y="{y0}" width="{larg + 2 * pad:.1f}" height="{lado + 2 * pad:.1f}" rx="6" fill="{t["quadro"]}" '
              f'stroke="{t["quadro_borda"]}" stroke-width="1"/>']
    for s, p in ufs.items():
        cor = marcadas.get(s)
        estilo = f'fill="{cor}" fill-opacity="{t["opac"]}"' if cor else f'fill="{t["uf"]}"'
        partes.append(f'<path d="{_caminho(p["p"], proj)}" {estilo} stroke="{t["uf_borda"]}" stroke-width=".6"/>')
    partes.append(f'<text x="{x0 + pad}" y="{y0 + lado + pad - 2:.1f}" font-family="IBM Plex Sans, sans-serif" '
                  f'font-size="10" font-weight="700" letter-spacing="1.5" fill="{t["rotulo"]}">BRASIL</text>')
    pontos, rot = [], []
    for e in eventos:
        alvo = ufs.get(e.get("uf")) if e.get("uf") else None
        lon, lat = alvo["c"] if alvo else (-51.5, -11.5)
        if e.get("uf") == "DF":
            lon, lat = -47.8, -15.8
        x, y = proj(lon, lat)
        pontos.append([x, y])
        rot.append((e["n"], cores.get(e["escopo"], TINTA)))
    _afastar(pontos, 22)
    return "".join(partes), [_marcador(x, y, n, t["marcador"], r=9.5, texto=t["texto_marcador"]) for (x, y), (n, c) in zip(pontos, rot)]


# ---------------------------------------------------------------- mapa de localização

def mapa_local(paises: list[str], uf: str, escopo: str, largura: int = 640, altura: int = 300) -> str | None:
    """Recorte regional com os países (ou a UF) do evento em destaque."""
    mundo, ufs = _mundo(), _ufs()
    paises = [p for p in paises if p in mundo]
    if not paises and not uf:
        return None
    cor = DESTAQUE
    usar_ufs = bool(uf) or paises == ["BRA"]

    if usar_ufs:
        if uf:
            b = ufs[uf]["b"]
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            meia_lat = max(b[3] - b[1], 7) * 1.1
        else:
            cx, cy, meia_lat = -53, -14.5, 20
    else:
        bbs = [mundo[p]["b"] for p in paises]
        x_min, y_min = min(b[0] for b in bbs), min(b[1] for b in bbs)
        x_max, y_max = max(b[2] for b in bbs), max(b[3] for b in bbs)
        if x_max - x_min > 200:  # cruza a linha de data ou é global: usa centroides
            cs = [mundo[p]["c"] for p in paises]
            x_min, x_max = min(c[0] for c in cs) - 15, max(c[0] for c in cs) + 15
        cx, cy = (x_min + x_max) / 2, (y_min + y_max) / 2
        kx = math.cos(math.radians(max(min(cy, 70), -70)))
        meia_lat = max((y_max - y_min) / 2 * 1.5, (x_max - x_min) * kx / 2 * 1.5 * altura / largura, 9)
    kx = math.cos(math.radians(max(min(cy, 70), -70)))
    esc = altura / (2 * meia_lat)
    meia_lon = largura / esc / kx / 2

    def proj(lon, lat):
        return (largura / 2 + (lon - cx) * kx * esc, altura / 2 - (lat - cy) * esc)

    def visivel(b):
        return not (b[2] < cx - meia_lon or b[0] > cx + meia_lon or b[3] < cy - meia_lat or b[1] > cy + meia_lat)

    camadas = [f'<rect width="{largura}" height="{altura}" fill="{MAR}"/>']
    for cod, p in mundo.items():
        if not visivel(p["b"]) or (usar_ufs and cod == "BRA"):
            continue
        destaque = cod in paises and not usar_ufs
        estilo = f'fill="{cor}"' if destaque else f'fill="{TERRA}"'
        camadas.append(f'<path d="{_caminho(p["p"], proj)}" {estilo} stroke="#fff" stroke-width=".8"/>')
    if usar_ufs:
        for s, p in ufs.items():
            estilo = f'fill="{cor}"' if s == uf or (not uf) else f'fill="{TERRA}"'
            camadas.append(f'<path d="{_caminho(p["p"], proj)}" {estilo} stroke="#fff" stroke-width=".8"/>')
        if uf:
            x, y = proj(*ufs[uf]["c"])
            camadas.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="16" fill="none" stroke="{TINTA}" stroke-width="1.5"/>'
                           f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="{TINTA}" stroke="#fff" stroke-width="2"/>')
    else:
        for p in paises:
            x, y = proj(*mundo[p]["c"])
            if 0 < x < largura and 0 < y < altura:
                camadas.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{TINTA}" stroke="#fff" stroke-width="2"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {largura} {altura}" width="{largura}" '
            f'height="{altura}" preserveAspectRatio="xMidYMid slice">{"".join(camadas)}</svg>')


def nome_pais(cod: str) -> str:
    return _mundo().get(cod, {}).get("n", cod)
