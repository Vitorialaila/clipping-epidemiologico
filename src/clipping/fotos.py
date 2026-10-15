"""Fotos das notícias, apenas de fontes com licença de reutilização verificada (config.yaml)."""
from __future__ import annotations

import io
import re
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .util import carregar_yaml, log


def licenca_para(url: str) -> dict | None:
    for regra in carregar_yaml("config.yaml").get("imagens", {}).get("fotos_licenciadas", []):
        if regra["dominio"] in url:
            return regra
    return None


def _credito(sopa: BeautifulSoup, img_url: str) -> str:
    """Procura o crédito do fotógrafo na legenda ou no texto próximo à imagem."""
    candidatos = []
    for el in sopa.find_all(["figcaption", "small", "span", "p", "div"], limit=4000):
        t = el.get_text(" ", strip=True)
        if 4 < len(t) < 160 and re.search(r"\b(foto|fotos|imagem)\s*:", t, re.I):
            candidatos.append(t)
    for t in candidatos:
        m = re.search(r"(?:foto|fotos|imagem)\s*:\s*(.{3,80})", t, re.I)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip(" .")
    return ""


def obter(url: str, html: str, destino: Path, s) -> dict | None:
    """Baixa a imagem de destaque (og:image) se a fonte tiver licença verificada.

    A imagem é salva sem recorte nem alteração (exigência da licença SemDerivações).
    """
    regra = licenca_para(url)
    if not regra or not html:
        return None
    sopa = BeautifulSoup(html, "html.parser")
    meta = sopa.find("meta", property="og:image") or sopa.find("meta", attrs={"name": "twitter:image"})
    if not meta or not meta.get("content"):
        return None
    img_url = urljoin(url, meta["content"])
    if re.search(r"(logo|marca|brasao|default|placeholder)", img_url, re.I):
        return None
    try:
        from PIL import Image

        r = s.get(img_url, timeout=40)
        r.raise_for_status()
        im = Image.open(io.BytesIO(r.content))
        largura, altura = im.size
        if largura < 600 or altura < 300 or largura / altura > 3 or largura / altura < 0.8:
            return None
        ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}.get(im.format, ".jpg")
        if ext == ".webp":  # WeasyPrint não lê WebP de forma confiável: converte o formato, sem alterar a imagem
            im = im.convert("RGB")
            ext = ".jpg"
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=90)
            conteudo = buf.getvalue()
        else:
            conteudo = r.content
        destino = destino.with_suffix(ext)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(conteudo)
    except Exception as erro:  # noqa: BLE001
        log.warning("Foto não utilizada (%s): %s", img_url, erro)
        return None
    autor = _credito(sopa, img_url)
    credito = f"Foto: {autor}" if autor else f"Foto: {regra['credito']}"
    return {"arquivo": destino, "credito": f"{credito} | {regra['licenca']}", "proporcao": round(altura / largura, 4)}
