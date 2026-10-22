"""Geração do site estático (GitHub Pages) na pasta docs/."""
from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import boletim, icones, mapas, paineis, semana
from .pontuar import limiares
from .util import (ARQ_NOTICIAS, ASSETS, DIR_EDICOES_DADOS, DOCS, NOME_ESCOPO, ORDEM_ESCOPO, TEMPLATES,
                   carregar_yaml, data_curta, data_extenso, ler_json, log, normalizar, para_data, salvar_json, truncar)


def _edicoes() -> list[dict]:
    lista = []
    for arq in DIR_EDICOES_DADOS.glob("*.json"):
        e = ler_json(arq, {})
        if e.get("pdf"):
            lista.append(e)
    return sorted(lista, key=lambda e: (e["se_codigo"], e.get("gerado_em", "")), reverse=True)


def _boletins() -> list[dict]:
    """Boletins epidemiológicos publicados, uma entrada por SE (a versão mais recente)."""
    vistos, lista = set(), []
    for b in boletim.listar():
        if b["se_codigo"] not in vistos:
            vistos.add(b["se_codigo"])
            lista.append(b)
    return lista


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
        "m": n.get("municipios", []),
    } for n in noticias if n["pontuacao"] >= limiar_feed and n["data"] >= corte]
    # A mesma matéria pode chegar por dois caminhos (ex.: página da CNN e busca da CNN): fica a de maior pontuação
    unicos: dict[str, dict] = {}
    for x in sorted(feed, key=lambda x: x["p"], reverse=True):
        unicos.setdefault(re.sub(r"\W+", " ", normalizar(x["t"])).strip(), x)
    feed = list(unicos.values())
    feed.sort(key=lambda x: (x["d"], x["p"]), reverse=True)

    # Semanas epidemiológicas presentes no feed, da mais recente para a mais antiga
    semanas = []
    for cod in sorted({x["se"] for x in feed if x.get("se")}, reverse=True):
        try:
            w = semana.de_codigo(cod)
        except ValueError:
            continue
        ini, fim = w.startdate(), w.enddate()
        semanas.append((cod, f"{semana.rotulo(w)} ({ini.day:02d}/{ini.month:02d} a {fim.day:02d}/{fim.month:02d})"))
    presentes = {m for x in feed for m in x["m"]}
    municipios = (["Distrito Federal"] if "Distrito Federal" in presentes else []) + sorted(
        m for m in presentes if m != "Distrito Federal")
    agravos = Counter(a for x in feed for a in x["a"])
    fontes = sorted({x["f"] for x in feed})
    edicoes = _edicoes()
    # Uma linha por SE no arquivo (a versão mais recente), com as anteriores acessíveis
    por_se: dict[str, list] = {}
    for e in edicoes:
        por_se.setdefault(e["se_codigo"], []).append(e)

    # Carrega painéis de vigilância
    paineis_lista = paineis.listar_paineis()

    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    html = env.get_template("site.html").render(
        titulo=cfg["publicacao"]["titulo"], subtitulo=cfg["publicacao"].get("subtitulo", ""),
        instituicoes=cfg["publicacao"].get("instituicoes", []), autoria=cfg["publicacao"].get("autoria", ""), mapa_hero="assets/mapa-hero.svg",
        rodape=cfg["publicacao"].get("rodape", ""),
        ultima=edicoes[0] if edicoes else None, arquivo=por_se, boletins=_boletins(),
        feed_json=json.dumps(feed, ensure_ascii=False).replace("</", "<\\/"),
        agravos=[a for a, _ in agravos.most_common()], fontes=fontes, semanas=semanas, municipios=municipios,
        ordem_escopo=ORDEM_ESCOPO, nome_escopo=NOME_ESCOPO, dias=dias, total_feed=len(feed),
        atualizado=data_extenso(date.today()),
        atualizado_iso=datetime.now(timezone.utc).isoformat(timespec="minutes"),
        fontes_monitoradas=sorted({f["nome"].split(" | ")[0] for f in cfg["fontes"] if f.get("ativo", True)}),
        icone=icones.svg,
        icones_json=json.dumps({k: re.sub(r"\s+", " ", v) for k, v in icones.ICONES.items()}).replace("</", "<\\/"),
        paineis_json=json.dumps(paineis_lista, ensure_ascii=False).replace("</", "<\\/"),
    )
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "index.html").write_text(html, encoding="utf-8")
    (DOCS / "assets").mkdir(exist_ok=True)
    shutil.copy(ASSETS / "img" / "logos.png", DOCS / "assets" / "logos.png")
    shutil.copy(ASSETS / "img" / "logos-transparente.png", DOCS / "assets" / "logos-transparente.png")
    shutil.copy(ASSETS / "img" / "favicon.png", DOCS / "assets" / "favicon.png")
    (DOCS / "assets" / "fonts").mkdir(exist_ok=True)
    for fonte in (ASSETS / "fonts").glob("Inter-*.woff2"):
        shutil.copy(fonte, DOCS / "assets" / "fonts" / fonte.name)
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")
    # Fundo da abertura: mapa da semana em tema escuro (ou mapa-múndi sem marcadores, antes da 1ª edição)
    ultima = edicoes[0] if edicoes else None
    eventos = [{"n": d.get("n", i), "paises": d.get("paises", []), "uf": d.get("uf", ""), "escopo": d["escopo"]}
               for i, d in enumerate(ultima["destaques"], start=1)] if ultima else []
    (DOCS / "assets" / "mapa-hero.svg").write_text(mapas.mapa_semana(eventos, tema="escuro"),
                                                   encoding="utf-8")
    # Mapa-múndi decorativo (sem marcadores) para o fundo azul da abertura
    (DOCS / "assets" / "mapa-decorativo.svg").write_text(mapas.mapa_semana([], tema="escuro"), encoding="utf-8")
    salvar_json(DOCS / "dados" / "feed.json", feed)
    log.info("Site gerado: %d notícias no feed, %d edições.", len(feed), len(edicoes))
