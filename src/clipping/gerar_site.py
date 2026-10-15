"""Geração do site estático (GitHub Pages) na pasta docs/."""
from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import icones
from .pontuar import limiares
from .util import (ARQ_NOTICIAS, ASSETS, DIR_EDICOES_DADOS, DOCS, NOME_ESCOPO, ORDEM_ESCOPO, TEMPLATES,
                   carregar_yaml, data_curta, data_extenso, ler_json, log, para_data, salvar_json, truncar)


def _edicoes() -> list[dict]:
    lista = []
    for arq in DIR_EDICOES_DADOS.glob("*.json"):
        e = ler_json(arq, {})
        if e.get("pdf"):
            lista.append(e)
    return sorted(lista, key=lambda e: (e["se_codigo"], e.get("gerado_em", "")), reverse=True)


def gerar() -> None:
    cfg = carregar_yaml("config.yaml")
    limiar_feed, _ = limiares()
    dias = int(cfg.get("site", {}).get("dias_feed", 90))
    corte = (date.today() - timedelta(days=dias)).isoformat()

    noticias = ler_json(ARQ_NOTICIAS, [])
    feed = [{
        "t": n["titulo"], "u": n["url"], "d": n["data"], "df": data_curta(para_data(n["data"])),
        "f": n.get("sigla") or n["fonte"], "fn": n["fonte"], "e": n["escopo"], "a": n.get("agravos", []),
        "p": n["pontuacao"], "se": n.get("se", ""), "r": truncar(n.get("descricao", ""), 260),
        "i": icones.icone_para(*(n.get("agravos") or [n["titulo"]])),
    } for n in noticias if n["pontuacao"] >= limiar_feed and n["data"] >= corte]
    feed.sort(key=lambda x: (x["d"], x["p"]), reverse=True)

    agravos = Counter(a for x in feed for a in x["a"])
    fontes = sorted({x["f"] for x in feed})
    edicoes = _edicoes()
    # Uma linha por SE no arquivo (a versão mais recente), com as anteriores acessíveis
    por_se: dict[str, list] = {}
    for e in edicoes:
        por_se.setdefault(e["se_codigo"], []).append(e)

    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    html = env.get_template("site.html").render(
        titulo=cfg["publicacao"]["titulo"], subtitulo=cfg["publicacao"].get("subtitulo", ""),
        rodape=cfg["publicacao"].get("rodape", ""),
        ultima=edicoes[0] if edicoes else None, arquivo=por_se,
        feed_json=json.dumps(feed, ensure_ascii=False).replace("</", "<\\/"),
        agravos=[a for a, _ in agravos.most_common()], fontes=fontes,
        ordem_escopo=ORDEM_ESCOPO, nome_escopo=NOME_ESCOPO, dias=dias, total_feed=len(feed),
        atualizado=data_extenso(date.today()),
        atualizado_iso=datetime.now(timezone.utc).isoformat(timespec="minutes"),
        fontes_monitoradas=sorted({f["nome"].split(" | ")[0] for f in cfg["fontes"]}),
        icone=icones.svg,
        icones_json=json.dumps({k: re.sub(r"\s+", " ", v) for k, v in icones.ICONES.items()}).replace("</", "<\\/"),
    )
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "index.html").write_text(html, encoding="utf-8")
    (DOCS / "assets").mkdir(exist_ok=True)
    shutil.copy(ASSETS / "img" / "logos.png", DOCS / "assets" / "logos.png")
    (DOCS / "assets" / "fonts").mkdir(exist_ok=True)
    for fonte in (ASSETS / "fonts").glob("*.ttf"):
        shutil.copy(fonte, DOCS / "assets" / "fonts" / fonte.name)
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")
    salvar_json(DOCS / "dados" / "feed.json", feed)
    log.info("Site gerado: %d notícias no feed, %d edições.", len(feed), len(edicoes))
