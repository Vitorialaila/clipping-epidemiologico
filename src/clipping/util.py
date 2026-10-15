"""Funções utilitárias compartilhadas."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests
import yaml

RAIZ = Path(__file__).resolve().parents[2]
CONFIG = RAIZ / "config"
DADOS = RAIZ / "data"
DOCS = RAIZ / "docs"
TEMPLATES = RAIZ / "templates"
ASSETS = RAIZ / "assets"
ARQ_NOTICIAS = DADOS / "noticias.json"
DIR_EDICOES_DADOS = DADOS / "edicoes"
DIR_EDICOES_PDF = DOCS / "edicoes"

log = logging.getLogger("clipping")

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]
MESES_ABREV = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
ORDEM_ESCOPO = ["DF", "Brasil", "Américas", "Internacional"]
NOME_ESCOPO = {
    "DF": "Distrito Federal",
    "Brasil": "Brasil",
    "Américas": "Américas",
    "Internacional": "Internacional",
}


def configurar_log() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")


def carregar_yaml(nome: str) -> dict:
    return yaml.safe_load((CONFIG / nome).read_text(encoding="utf-8"))


def ler_json(caminho: Path, padrao):
    if caminho.exists():
        return json.loads(caminho.read_text(encoding="utf-8"))
    return padrao


def salvar_json(caminho: Path, dados) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")


def sessao_http() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/126.0 Safari/537.36 ClippingEpidemiologico/1.0"),
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8,es;q=0.6",
    })
    return s


def normalizar(texto: str) -> str:
    """Minúsculas e sem acentos, para comparação de termos."""
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto.lower())


def normalizar_url(url: str) -> str:
    partes = urlsplit(url.strip())
    caminho = partes.path.rstrip("/") or "/"
    return urlunsplit((partes.scheme.lower(), partes.netloc.lower(), caminho, partes.query, ""))


def id_noticia(url: str) -> str:
    return hashlib.sha1(normalizar_url(url).encode()).hexdigest()[:14]


def sem_html(texto: str) -> str:
    texto = re.sub(r"<[^>]+>", " ", texto or "")
    texto = re.sub(r"&nbsp;", " ", texto)
    from html import unescape
    return re.sub(r"\s+", " ", unescape(texto)).strip()


def sem_travessao(texto: str) -> str:
    """Remove travessões (em dash e en dash), que não devem aparecer nos produtos."""
    if not texto:
        return texto
    texto = re.sub(r"(\d)\s*[–—]\s*(\d)", r"\1-\2", texto)
    texto = re.sub(r"\s*[—–]\s*", ", ", texto)
    return re.sub(r",\s*,", ",", texto)


def data_extenso(d: date) -> str:
    return f"{d.day} de {MESES[d.month - 1]} de {d.year}"


def data_curta(d: date) -> str:
    return f"{d.day:02d} {MESES_ABREV[d.month - 1]} {d.year}"


def para_data(valor) -> date | None:
    if not valor:
        return None
    if isinstance(valor, date) and not isinstance(valor, datetime):
        return valor
    if isinstance(valor, datetime):
        return valor.date()
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def truncar(texto: str, limite: int) -> str:
    texto = (texto or "").strip()
    if len(texto) <= limite:
        return texto
    corte = texto[:limite].rsplit(" ", 1)[0]
    return corte.rstrip(",.;: ") + "…"
