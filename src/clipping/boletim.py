"""Boletim epidemiológico semanal montado a partir do SITREP (slides do Canva).

Entrada (data/boletins/<edicao>/):
  boletim.json   seções, itens (slide, título, legenda da figura, texto corrido)
  slides/NN.png  slides exportados do Canva (baixados por boletim_slides.py)

Saída:
  docs/boletins/img/<edicao>/sNN.jpg   figuras recortadas (sem cabeçalho nem título do slide)
  docs/boletins/boletim-SE<NN>-<AAAA>.pdf   (nova versão -v2, -v3 se já existir; nada é sobrescrito)
  data/boletins/<edicao>/edicao.json   metadados usados pelo site
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import semana
from .boletim_slides import DIR_BOLETINS
from .util import (DOCS, RAIZ, TEMPLATES, carregar_yaml, data_extenso, ler_json, log, salvar_json,
                   sem_travessao)

DIR_PDF = DOCS / "boletins"
ALTURA_BASE = 540  # altura de referência dos slides no Canva (960 x 540)


def _aparar(im, limiar: int = 245, margem: int = 14):
    """Remove margens brancas ao redor da figura."""
    from PIL import ImageChops, Image

    cinza = im.convert("L")
    fundo = Image.new("L", cinza.size, 255)
    dif = ImageChops.difference(cinza, fundo).point(lambda v: 255 if v > 255 - limiar else 0)
    caixa = dif.getbbox()
    if not caixa:
        return im
    x0, y0, x1, y1 = caixa
    return im.crop((max(0, x0 - margem), max(0, y0 - margem), min(im.width, x1 + margem), min(im.height, y1 + margem)))


def recortar(edicao: str, item: dict) -> list[dict]:
    """Recorta apenas as figuras (mapas, gráficos, fotos) indicadas em item['paineis'].

    Cada painel: {"r": [x0, y0, x1, y1]} na base 960 x 540 do slide, com "apagar" opcional
    (áreas pintadas de branco, como a linha de fonte do slide) e "p" (título do painel).
    """
    from PIL import Image, ImageDraw

    pasta = DIR_BOLETINS / edicao / "slides"
    origem = next((p for p in (pasta / f"{item['slide']:02d}.png", pasta / f"{item['slide']:02d}.jpg") if p.exists()), None)
    if origem is None or not item.get("paineis"):
        return []
    base = Image.open(origem).convert("RGB")
    k = base.width / 960
    saida = []
    for n, pn in enumerate(item["paineis"], start=1):
        x0, y0, x1, y1 = pn["r"]
        im = base.copy()
        d = ImageDraw.Draw(im)
        for a in pn.get("apagar", []):
            d.rectangle([int(v * k) for v in a], fill=(255, 255, 255))
        im = _aparar(im.crop((int(x0 * k), int(y0 * k), int(x1 * k), int(y1 * k))))
        if im.width > 2000:
            im = im.resize((2000, int(im.height * 2000 / im.width)))
        destino = DIR_PDF / "img" / edicao / f"s{item['slide']:02d}-{n}.jpg"
        destino.parent.mkdir(parents=True, exist_ok=True)
        im.save(destino, "JPEG", quality=90, optimize=True)
        saida.append({"src": destino.relative_to(DOCS).as_posix(), "titulo": pn.get("p", ""),
                      "rotulo": chr(64 + n) if len(item["paineis"]) > 1 else "", "proporcao": im.width / im.height})
    return saida


def _caminho_pdf(se, forcar: bool):
    base = f"boletim-SE{se.week:02d}-{se.year}"
    caminho = DIR_PDF / f"{base}.pdf"
    if not caminho.exists():
        return caminho
    if not forcar:
        return None
    v = 2
    while (DIR_PDF / f"{base}-v{v}.pdf").exists():
        v += 1
    return DIR_PDF / f"{base}-v{v}.pdf"


def gerar(edicao: str, forcar: bool = False) -> dict | None:
    from weasyprint import HTML

    dados = ler_json(DIR_BOLETINS / edicao / "boletim.json", None)
    if not dados:
        log.warning("Boletim %s sem conteúdo (boletim.json ausente).", edicao)
        return None
    se = semana.de_codigo(dados["se"])
    caminho = _caminho_pdf(se, forcar)
    if caminho is None:
        log.info("Boletim da %s já existe. Use --forcar para gerar nova versão.", semana.rotulo(se))
        return None

    cfg = carregar_yaml("config.yaml")
    n_fig = n_tab = 0
    secoes = []
    for i, s in enumerate(dados["secoes"], start=1):
        itens = []
        for it in s["itens"]:
            paineis = recortar(edicao, it)
            if paineis:
                n_fig += 1
            n_tab_item = None
            if it.get("tabela"):
                n_tab += 1
                n_tab_item = n_tab
            itens.append({**it, "titulo": sem_travessao(it["titulo"]), "texto": sem_travessao(it["texto"]),
                          "figura_legenda": sem_travessao(it.get("figura", "")), "paineis": paineis,
                          "n_fig": n_fig if paineis else None,
                          "lado": len(paineis) == 1 and paineis[0]["proporcao"] < 1.3, "n_tab": n_tab_item, "ancora": f"s{it['slide']}"})
        secoes.append({**s, "n": i, "itens": itens, "ancora": f"sec-{s['id']}"})

    contexto = {
        "titulo": "Boletim Epidemiológico",
        "subtitulo": cfg["publicacao"].get("subtitulo", ""),
        "instituicoes": cfg["publicacao"].get("instituicoes", []),
        "rotulo": semana.rotulo(se), "se_numero": f"{se.week:02d}", "se_ano": se.year,
        "periodo": semana.periodo_extenso(se), "data_geracao": data_extenso(date.today()),
        "resumo": sem_travessao(dados.get("resumo", "")), "secoes": secoes,
        "n_itens": sum(len(s["itens"]) for s in secoes), "n_figuras": n_fig,
        "base_img": "docs/",
        "elaboracao": dados.get("elaboracao"),
    }
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    html = env.get_template("boletim.html").render(**contexto)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html, base_url=str(RAIZ)).write_pdf(str(caminho))

    meta = {
        "edicao": edicao, "se_codigo": semana.codigo(se), "rotulo": semana.rotulo(se),
        "se_numero": f"{se.week:02d}", "se_ano": se.year, "periodo": contexto["periodo"],
        "pdf": f"boletins/{caminho.name}", "resumo": contexto["resumo"],
        "secoes": [{"nome": s["nome"], "risco": s.get("risco", ""), "itens": len(s["itens"])} for s in secoes],
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    salvar_json(DIR_BOLETINS / edicao / "edicao.json", meta)
    log.info("Boletim gerado: %s (%d itens, %d figuras)", caminho.relative_to(RAIZ), contexto["n_itens"], n_fig)
    return meta


def listar() -> list[dict]:
    lista = [ler_json(p, {}) for p in DIR_BOLETINS.glob("*/edicao.json")]
    return sorted((e for e in lista if e.get("pdf")), key=lambda e: e["se_codigo"], reverse=True)
