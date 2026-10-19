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
from .pontuar import municipios, pontuar
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


def _municipios_item(titulo: str, descricao: str, escopo: str) -> list[str]:
    lista = municipios(titulo, descricao)
    if escopo == "DF" and "Distrito Federal" not in lista:
        lista.insert(0, "Distrito Federal")
    return lista


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
    sopa = BeautifulSoup(r.content, "html.parser")  # bytes: o parser detecta a codificação da página
    padrao = re.compile(fonte["padrao_link"], re.I)
    vistos: dict[str, dict] = {}
    for a in sopa.find_all("a", href=True):
        link = urljoin(fonte["url"], a["href"]).split("#")[0]
        if not padrao.search(link):
            continue
        cab = a.find(["h1", "h2", "h3", "h4"])
        titulo = (cab or a).get_text(" ", strip=True)
        titulo = re.split(r"\s+postado em\s+\d", titulo, maxsplit=1)[0].strip()
        atual = vistos.get(link)
        if atual is None or len(titulo) > len(atual["titulo"]):
            # Data próxima ao link (ex.: listagens da OPAS e do gov.br)
            contexto = a.find_parent(["li", "article", "div"])
            texto_ctx = contexto.get_text(" ", strip=True)[:400] if contexto else ""
            d = _data_texto(titulo) or _data_texto(texto_ctx) or _data_br(texto_ctx) or _data_url(link)
            vistos[link] = {"titulo": titulo, "url": link, "data": d, "descricao": ""}
    return [v for v in vistos.values() if len(v["titulo"]) >= 15][:40]


def _data_url(url: str) -> date | None:
    """Data no caminho do link (ex.: cnn.com/2026/10/14/health/...)."""
    m = re.search(r"/(20\d{2})/(\d{2})/(\d{2})/", url)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


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


def _google_news(fonte: dict, s, timeout: int) -> list[dict]:
    """Busca no índice do Google Notícias, mantendo apenas itens publicados por domínios oficiais.

    Usado para portais oficiais que bloqueiam o acesso automatizado direto (ex.: portais do GDF).
    """
    from urllib.parse import quote, urlsplit

    permitidos = tuple(d.lower() for d in fonte.get("dominios_permitidos", [".gov.br"]))
    nomes = fonte.get("nomes_fontes", {})
    itens, vistos = [], set()
    for consulta in fonte["consultas"]:
        url = ("https://news.google.com/rss/search?q=" + quote(consulta + " when:" + str(fonte.get("dias", 14)) + "d")
               + "&" + fonte.get("regiao", "hl=pt-BR&gl=BR&ceid=BR:pt-419"))
        r = s.get(url, timeout=timeout)
        r.raise_for_status()
        for e in feedparser.parse(r.content).entries:
            origem = (e.get("source") or {}).get("href", "")
            dominio = urlsplit(origem).netloc.lower()
            if not dominio or not dominio.endswith(permitidos):
                continue
            titulo = sem_html(e.get("title", ""))
            nome_origem = (e.get("source") or {}).get("title", "")
            if nome_origem and titulo.endswith(" - " + nome_origem):
                titulo = titulo[: -len(nome_origem) - 3]
            link = e.get("link")
            if not link or titulo in vistos:
                continue
            vistos.add(titulo)
            sigla = next((v for k, v in nomes.items() if dominio.endswith(k)), nome_origem or dominio)
            itens.append({"titulo": titulo, "url": link, "data": _data_struct(e.get("published_parsed")),
                          "descricao": "", "sigla": sigla, "fonte_nome": nome_origem or sigla})
        time.sleep(1.5)
    return itens


def _link_original(url: str) -> str:
    """Troca o link de redirecionamento do Google Notícias pelo endereço da matéria (o texto completo
    e o resumo do clipping dependem da página original). Se não conseguir, mantém o link recebido."""
    if "news.google.com" not in url:
        return url
    try:
        from googlenewsdecoder import gnewsdecoder
        r = gnewsdecoder(url, interval=1, timeout=15)
        if (r.get("success") or r.get("status")) and r.get("decoded_url"):
            return r["decoded_url"]
    except Exception as erro:  # noqa: BLE001
        log.warning("Não foi possível obter o link original de %s: %s", url[:80], erro)
    return url


COLETORES = {"rss": _rss, "who_api": _who_api, "html": _html, "google_news": _google_news}


def coletar() -> dict:
    cfg = carregar_yaml("config.yaml")
    timeout = int(cfg.get("coleta", {}).get("timeout_segundos", 30))
    retencao = int(cfg.get("coleta", {}).get("dias_retencao", 400))
    base = {n["id"]: n for n in ler_json(ARQ_NOTICIAS, [])}
    s = sessao_http()
    hoje = date.today()
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    resumo = {"novas": 0, "fontes_ok": [], "fontes_erro": {}}
    status = {}  # diagnóstico por fonte, salvo em data/coleta_status.json

    # Links antigos do Google Notícias (até 30 por execução, das últimas duas semanas): busca o endereço original
    recentes = (hoje - timedelta(days=14)).isoformat()
    pendentes = [n for n in base.values()
                 if "news.google.com" in n["url"] and n["data"] >= recentes and not n.get("link_tentado")][:30]
    for n in pendentes:
        n["url"] = _link_original(n["url"])
        n["link_tentado"] = True

    for fonte in cfg["fontes"]:
        if fonte.get("ativo", True) is False:
            continue
        try:
            itens = COLETORES[fonte["tipo"]](fonte, s, timeout)
        except Exception as erro:  # noqa: BLE001
            log.warning("Falha na fonte %s: %s", fonte["id"], erro)
            resumo["fontes_erro"][fonte["id"]] = str(erro)[:200]
            status[fonte["id"]] = {"em": agora, "erro": str(erro)[:300]}
            continue
        resumo["fontes_ok"].append(fonte["id"])
        novas_fonte = 0
        for it in itens:
            nid = id_noticia(it["url"])
            if nid in base:
                continue
            if fonte["tipo"] == "google_news":
                it["url"] = _link_original(it["url"])
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
            if (fonte["tipo"] == "google_news" or fonte.get("apenas_relevantes")) and p["pontuacao"] <= 0:
                continue  # fontes amplas (imprensa, meteorologia, buscas): guarda só o que tem relevância epidemiológica
            base[nid] = {
                "id": nid,
                "titulo": truncar(it["titulo"], 300),
                "url": it["url"],
                "fonte_id": fonte["id"],
                "fonte": it.get("fonte_nome") or fonte["nome"],
                "sigla": it.get("sigla") or fonte.get("sigla", ""),
                "idioma": fonte.get("idioma", "en"),
                "data": d.isoformat(),
                "data_estimada": estimada,
                "se": semana.codigo(semana.se_da_data(d)),
                "descricao": truncar(limpar_descricao(it["descricao"]), 1500),
                "coletado_em": agora,
                "municipios": _municipios_item(it["titulo"], limpar_descricao(it["descricao"]), p["escopo"]),
                **p,
            }
            novas_fonte += 1
        resumo["novas"] += novas_fonte
        status[fonte["id"]] = {"em": agora, "lidos": len(itens), "novos": novas_fonte,
                               "exemplos": [it["titulo"][:120] for it in itens[:3]]}
        log.info("%-24s %3d itens lidos, %3d novos", fonte["id"], len(itens), novas_fonte)

    limite = (hoje - timedelta(days=retencao)).isoformat()
    noticias = sorted((n for n in base.values() if n["data"] >= limite),
                      key=lambda n: (n["data"], n["pontuacao"]), reverse=True)
    salvar_json(ARQ_NOTICIAS, noticias)
    salvar_json(ARQ_NOTICIAS.parent / "coleta_status.json", status)
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
        n["municipios"] = _municipios_item(n["titulo"], n["descricao"], n["escopo"])
    salvar_json(ARQ_NOTICIAS, noticias)
    log.info("Base repontuada: %d notícias.", len(noticias))
