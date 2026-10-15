"""Pictogramas próprios por grupo de agravo (traço fino, cor herdada via currentColor)."""
from __future__ import annotations

import re
from functools import lru_cache

from .util import carregar_yaml, normalizar

_T = 'fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"'

ICONES = {
    "virus": f'''<g {_T}><circle cx="24" cy="24" r="9.5"/>
      <path d="M24 14.5V8M24 33.5V40M14.5 24H8M33.5 24H40M17.3 17.3l-4.6-4.6M30.7 30.7l4.6 4.6M30.7 17.3l4.6-4.6M17.3 30.7l-4.6 4.6"/>
      <circle cx="24" cy="6.6" r="1.6"/><circle cx="24" cy="41.4" r="1.6"/><circle cx="6.6" cy="24" r="1.6"/><circle cx="41.4" cy="24" r="1.6"/>
      <circle cx="11.7" cy="11.7" r="1.6"/><circle cx="36.3" cy="36.3" r="1.6"/><circle cx="36.3" cy="11.7" r="1.6"/><circle cx="11.7" cy="36.3" r="1.6"/>
      <circle cx="21" cy="22" r="1.3"/><circle cx="26.5" cy="26.5" r="1.3"/><circle cx="26" cy="20.5" r="0.9"/></g>''',
    "mosquito": f'''<g {_T}><path d="M14 34l18-18"/><ellipse cx="23" cy="25" rx="3" ry="9" transform="rotate(45 23 25)"/>
      <circle cx="32.5" cy="15.5" r="2.6"/><path d="M34.6 13.4l6-6"/>
      <path d="M22 22c-4-8-12-11-15-9s1 9 11 11M26 26c8 4 11 12 9 15s-9-1-11-11"/>
      <path d="M21 29l-6 3-2 6M24 30l-2 6 2 5M18 26l-7 1-4 4"/></g>''',
    "respiratorio": f'''<g {_T}><path d="M24 7v14m0 0c-2 2-4 3-6 3M24 21c2 2 4 3 6 3"/>
      <path d="M18 15c-6 2-10 9-10 17 0 5 1 9 4 9 5 0 9-3 9-9V19c0-2-1-4-3-4z"/>
      <path d="M30 15c6 2 10 9 10 17 0 5-1 9-4 9-5 0-9-3-9-9V19c0-2 1-4 3-4z"/>
      <path d="M13 30c2-1 4 0 5 2M35 30c-2-1-4 0-5 2"/></g>''',
    "vacina": f'''<g {_T}><path d="M33 9l6 6M36 12l-4.5 4.5"/><path d="M31.5 16.5L16 32l-4-4 15.5-15.5z"/>
      <path d="M27.5 12.5l8 8M14 30l-6 6M20 24l2 2M23 21l2 2M17 27l2 2"/></g>''',
    "bacteria": f'''<g {_T}><rect x="10" y="17" width="28" height="14" rx="7" transform="rotate(-25 24 24)"/>
      <path d="M36 15c3-2 5-1 7-3M12 33c-3 2-5 1-7 3M38 22c3 0 4 2 6 2M10 26c-3 0-4-2-6-2"/>
      <circle cx="19" cy="25" r="1.4"/><circle cx="24" cy="23" r="1.4"/><circle cx="29" cy="21" r="1.4"/></g>''',
    "fungo": f'''<g {_T}><path d="M24 41V27M24 33c-4-1-7-4-8-8M24 30c4-1 7-4 8-9M16 25c-2-3-6-4-8-3M32 21c2-3 6-4 8-3"/>
      <circle cx="24" cy="23" r="3.5"/><circle cx="15" cy="23" r="2.4"/><circle cx="33" cy="19" r="2.4"/><circle cx="8" cy="21" r="1.8"/><circle cx="40" cy="17" r="1.8"/>
      <circle cx="20" cy="12" r="2"/><circle cx="28" cy="10" r="1.5"/><path d="M24 19.5V15"/></g>''',
    "agua": f'''<g {_T}><path d="M24 7c6 8 11 14 11 20a11 11 0 0 1-22 0c0-6 5-12 11-20z"/>
      <path d="M17 29c2 2 4 3 7 3s5-1 7-3"/><path d="M19 23c1.5 1 3 1.5 5 1.5"/></g>''',
    "alerta": f'''<g {_T}><path d="M24 7L42 39H6z"/><path d="M24 19v10"/><circle cx="24" cy="34" r="0.6" fill="currentColor"/></g>''',
    "desastre": f'''<g {_T}><path d="M5 30h7l3-10 4 18 4-26 4 22 3-10 3 6h10"/><path d="M9 40h30"/></g>''',
    "clima": f'''<g {_T}><path d="M19 29V11a4 4 0 0 1 8 0v18a7 7 0 1 1-8 0z"/><circle cx="23" cy="34" r="3"/><path d="M23 31V18"/>
      <path d="M33 13h5M33 19h3M33 25h5"/></g>''',
    "quimico": f'''<g {_T}><path d="M19 7h10M21 7v12L10 37a3 3 0 0 0 2.6 4.5h22.8A3 3 0 0 0 38 37L27 19V7"/>
      <path d="M15 30h18"/><circle cx="21" cy="35" r="1.2"/><circle cx="27" cy="33" r="1.2"/></g>''',
    "animal": f'''<g {_T}><ellipse cx="24" cy="31" rx="8" ry="7"/><ellipse cx="13" cy="21" rx="3" ry="4"/><ellipse cx="35" cy="21" rx="3" ry="4"/>
      <ellipse cx="19" cy="13" rx="3" ry="4"/><ellipse cx="29" cy="13" rx="3" ry="4"/></g>''',
    "saude": f'''<g {_T}><path d="M19 8h10v11h11v10H29v11H19V29H8V19h11z"/></g>''',
}

ROTULO_ICONE = {
    "virus": "Vírus", "mosquito": "Vetores", "respiratorio": "Respiratório", "vacina": "Imunização",
    "bacteria": "Bactérias", "fungo": "Fungos", "agua": "Água e alimentos", "alerta": "Alerta",
    "desastre": "Desastres", "clima": "Clima", "quimico": "Intoxicações", "animal": "Zoonoses", "saude": "Saúde",
}


def svg(nome: str, tamanho: int = 48, cor: str | None = None) -> str:
    estilo = f' style="color:{cor}"' if cor else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" width="{tamanho}" height="{tamanho}"{estilo}>'
            f'{ICONES.get(nome, ICONES["alerta"])}</svg>')


# Termos adicionais para nomes de agravo livres (vindos da IA)
EXTRAS = [("pneumon", "respiratorio"), ("respirat", "respiratorio"), ("virus", "virus"), ("viral", "virus"),
          ("bacter", "bacteria"), ("fung", "fungo"), ("vetor", "mosquito"), ("vacin", "vacina"),
          ("intoxica", "quimico"), ("calor", "clima"), ("terremoto", "desastre"), ("enchente", "desastre")]


@lru_cache(maxsize=1)
def _mapa_agravos():
    c = carregar_yaml("criterios.yaml")
    saida = []
    for a in c["agravos"]:
        termos = [normalizar(a["nome"])] + [normalizar(str(t)) for t in a["termos"]]
        padrao = re.compile(r"(?<![\w-])(" + "|".join(re.escape(t) for t in termos) + r")(?![\w])")
        saida.append((padrao, a.get("icone", "alerta")))
    return saida


def icone_para(*textos: str) -> str:
    """Escolhe o pictograma a partir do nome do agravo (ou de qualquer texto descritivo)."""
    alvo = normalizar(" ".join(t for t in textos if t))
    for padrao, icone in _mapa_agravos():
        if padrao.search(alvo):
            return icone
    for termo, icone in EXTRAS:
        if termo in alvo:
            return icone
    return "alerta"
