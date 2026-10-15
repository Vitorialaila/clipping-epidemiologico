"""Coleta diária de notícias nas fontes oficiais configuradas."""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin

import feedparser
from bs4 import BeautifulSoup
from dateutil import parser as dtparser

from . import semana
from .pontuar import pontuar
from .util import (ARQ_NOTICIAS, carregar_yaml, id_noticia, ler_json, log, para_data, salvar_json,
                   sem_html, sessao_http, truncar)

MESES_EN = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"], start=1)}


def limpar_descricao(texto: str) -> str:
    """Remove trechos que não são da notícia (links de 'notícias relacionadas', assinatura e data do autor)."""
    texto = re.split(r"\bNot[ií]cias relacionadas\s*:", texto or "", maxsplit=1)[0]
    texto = re.sub(r"\b[\w.]+\s+(Seg|Ter|Qua|Qui|Sex|S[áa]b|Dom),\s*\d{2}/\d{2}/\d{4}\s*-\s*\d{2}:\d{2}", "", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _data_struct(struct) -> date | None:
    if not struct:
        return None
    return date(struct.tm_year, struct.tm_mon, struct.tm_mday)


def _data_texto(texto: str) -> date | None:
    """Procura datas como '26 August 2026', '26-08-2026' ou '2026/outubro' em títulos e URLs."""
    if not texto:
        return None
    m = re.search(r"(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|"
                  r"november|december)\s+(\d{4})", texto, re.I)
    if m:
        try:
            return date(int(m.group(3)), MESES_EN[m.group(2).lower()], int(m.group(1)))
        except ValueError:
            pass
    m = re.search(r"(\d{1,2})-(\d{1,2})-(20\d{2})", texto)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return None


def _rss(fonte: dict, s, timeout: int) -> list[dict]:
    r = s.get(fonte["url"], timeout=timeout)
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    itens = []
    for e in feed.entries:
        link = e.get("link")
        titulo = sem_html(e.get("title", ""))
        if not link or not titulo:
            continue
        d = _data_struct(e.get("published_parsed") or e.get("updated_parsed")) or _data_texto(titulo)
        itens.append({"titulo": titulo, "url": link, "data": d,
                      "descricao": sem_html(e.get("summary", "") or e.get("description", ""))})
    return itens


def _who_api(fonte: dict, s, timeout: int) -> list[dict]:
    r = s.get(fonte["url"], timeout=timeout, headers={"Accept": "application/json"})
    r.raise_for_status()
    itens = []
    for v in r.json().get("value", []):
        titulo = sem_html(v.get("OverrideTitle") if v.get("UseOverrideTitle") else v.get("Title", ""))
        if not titulo:
            titulo = sem_html(v.get("Title", ""))
        base = fonte["base_link"]
        if "UrlName" in v and "disease-outbreak-news" in base:
            link = base + v["UrlName"]
        else:
            link = base.rstrip("/") + "/" + str(v.get("ItemDefaultUrl", "")).lstrip("/")
        bruto = v.get("PublicationDateAndTime") or v.get("PublicationDate")
        d = dtparser.parse(bruto).date() if bruto else None
        descricao = " ".join(sem_html(v.get(k, "")) for k in ("Summary", "Overview") if v.get(k))
        itens.append({"titulo": titulo, "url": link, "data": d, "descricao": descricao})
    return itens


def _html(fonte: dict, s, timeout: int) -> list[dict]:
    r = s.get(fonte["url"], timeout=timeout)
    r.raise_for_status()
    sopa = BeautifulSoup(r.text, "html.parser")
    padrao = re.compile(fonte["padrao_link"], re.I)
    vistos: dict[str, dict] = {}
    for a in sopa.find_all("a", href=True):
        link = urljoin(fonte["url"], a["href"]).split("#")[0]
        if not padrao.search(link):
            continue
        titulo = a.get_text(" ", strip=True)
        atual = vistos.get(link)
        if atual is None or len(titulo) > len(atual["titulo"]):
            # Data próxima ao link (ex.: listagens da OPAS e do gov.br)
            contexto = a.find_parent(["li", "article", "div"])
            texto_ctx = contexto.get_text(" ", strip=True)[:400] if contexto else ""
            d = _data_texto(titulo) or _data_texto(texto_ctx) or _data_br(texto_ctx)
            vistos[link] = {"titulo": titulo, "url": link, "data": d, "descricao": ""}
    return [v for v in vistos.values() if len(v["titulo"]) >= 15][:40]


def _data_br(texto: str) -> date | None:
    m = re.search(r"(\d{2})/(\d{2})/(20\d{2})", texto or "")
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def _data_da_pagina(url: str, s, timeout: int) -> date | None:
    """Último recurso: lê a data de publicação nos metadados da página."""
    try:
        import trafilatura
        r = s.get(url, timeout=timeout)
        meta = trafilatura.extract_metadata(r.text)
        return para_data(meta.date) if meta and meta.date else None
    except Exception:  # noqa: BLE001
        return None


COLETORES = {"rss": _rss, "who_api": _who_api, "html": _html}


def coletar() -> dict:
    cfg = carregar_yaml("config.yaml")
    timeout = int(cfg.get("coleta", {}).get("timeout_segundos", 30))
    retencao = int(cfg.get("coleta", {}).get("dias_retencao", 400))
    base = {n["id"]: n for n in ler_json(ARQ_NOTICIAS, [])}
    s = sessao_http()
    hoje = date.today()
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    resumo = {"novas": 0, "fontes_ok": [], "fontes_erro": {}}

    for fonte in cfg["fontes"]:
        if fonte.get("ativo", True) is False:
            continue
        try:
            itens = COLETORES[fonte["tipo"]](fonte, s, timeout)
        except Exception as erro:  # noqa: BLE001
            log.warning("Falha na fonte %s: %s", fonte["id"], erro)
            resumo["fontes_erro"][fonte["id"]] = str(erro)[:200]
            continue
        resumo["fontes_ok"].append(fonte["id"])
        novas_fonte = 0
        for it in itens:
            nid = id_noticia(it["url"])
            if nid in base:
                continue
            d = it["data"]
            estimada = False
            if d is None:
                d = _data_da_pagina(it["url"], s, timeout)
                time.sleep(0.5)
            if d is None:
                d, estimada = hoje, True
            if d < hoje - timedelta(days=retencao) or d > hoje + timedelta(days=1):
                continue
            p = pontuar(it["titulo"], limpar_descricao(it["descricao"]), fonte)
            base[nid] = {
                "id": nid,
                "titulo": truncar(it["titulo"], 300),
                "url": it["url"],
                "fonte_id": fonte["id"],
                "fonte": fonte["nome"],
                "sigla": fonte.get("sigla", ""),
                "idioma": fonte.get("idioma", "en"),
                "data": d.isoformat(),
                "data_estimada": estimada,
                "se": semana.codigo(semana.se_da_data(d)),
                "descricao": truncar(limpar_descricao(it["descricao"]), 1500),
                "coletado_em": agora,
                **p,
            }
            novas_fonte += 1
        resumo["novas"] += novas_fonte
        log.info("%-24s %3d itens lidos, %3d novos", fonte["id"], len(itens), novas_fonte)

    limite = (hoje - timedelta(days=retencao)).isoformat()
    noticias = sorted((n for n in base.values() if n["data"] >= limite),
                      key=lambda n: (n["data"], n["pontuacao"]), reverse=True)
    salvar_json(ARQ_NOTICIAS, noticias)
    log.info("Coleta concluída: %d novas notícias, %d na base.", resumo["novas"], len(noticias))
    return resumo


def repontuar() -> None:
    """Recalcula a pontuação de toda a base (útil após editar criterios.yaml)."""
    cfg = carregar_yaml("config.yaml")
    fontes = {f["id"]: f for f in cfg["fontes"]}
    noticias = ler_json(ARQ_NOTICIAS, [])
    for n in noticias:
        n["descricao"] = limpar_descricao(n.get("descricao", ""))
        n.update(pontuar(n["titulo"], n["descricao"], fontes.get(n["fonte_id"], {})))
    salvar_json(ARQ_NOTICIAS, noticias)
    log.info("Base repontuada: %d notícias.", len(noticias))
