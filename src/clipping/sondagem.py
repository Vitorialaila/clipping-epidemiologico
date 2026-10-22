"""Sondagem temporária: baixa os CDTR semanais do ECDC (2026) e guarda o parágrafo do Ebola.

Executada uma vez no GitHub Actions para montar a série histórica do painel. Remover depois.
"""
from __future__ import annotations

import io
import re
import subprocess
import sys

from .util import DADOS, salvar_json, sessao_http

URL_CDTR = "https://www.ecdc.europa.eu/sites/default/files/documents/communicable-disease-threats-report-week-{w}-2026.pdf"


def _texto_pdf(conteudo: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pypdf"], check=False)
        from pypdf import PdfReader
    leitor = PdfReader(io.BytesIO(conteudo))
    return "\n".join((p.extract_text() or "") for p in leitor.pages)


def sondar() -> None:
    s = sessao_http()
    out = {}
    for w in range(20, 44):
        url = URL_CDTR.format(w=w)
        try:
            r = s.get(url, timeout=60)
            if r.status_code != 200 or not r.content.startswith(b"%PDF"):
                out[w] = {"url": url, "http": r.status_code}
                continue
            t = re.sub(r"\s+", " ", _texto_pdf(r.content))
            trechos = [t[max(0, m.start() - 300):m.end() + 1500]
                       for m in re.finditer(r"confirmed cases, including [\d ]+ (?:related )?deaths", t)][:2]
            out[w] = {"url": url, "http": 200, "trechos": trechos}
        except Exception as e:
            out[w] = {"url": url, "erro": str(e)[:200]}
    salvar_json(DADOS / "paineis" / "historico" / "sondagem_cdtr.json", out)
