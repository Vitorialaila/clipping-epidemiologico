"""Geração semanal do clipping (seleção, resumos e PDF)."""
from __future__ import annotations

import time
from collections import Counter
from datetime import date, datetime, timezone
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import ia, semana
from .pontuar import limiares
from .util import (ARQ_NOTICIAS, DIR_EDICOES_DADOS, DIR_EDICOES_PDF, NOME_ESCOPO, ORDEM_ESCOPO, RAIZ,
                   TEMPLATES, carregar_yaml, data_curta, data_extenso, ler_json, log, para_data,
                   salvar_json, sem_travessao, sessao_http)


def _texto_completo(url: str, s) -> str:
    try:
        import trafilatura
        r = s.get(url, timeout=40)
        r.raise_for_status()
        return trafilatura.extract(r.text, include_comments=False, include_tables=True) or ""
    except Exception as erro:  # noqa: BLE001
        log.warning("Não foi possível ler o texto de %s: %s", url, erro)
        return ""


def _dominio(url: str) -> str:
    return urlsplit(url).netloc.replace("www.", "")


def _selecionar(itens: list[dict], cfg: dict) -> tuple[list[dict], list[dict], bool]:
    limiar_feed, limiar_clip = limiares()
    c_ia, c_clip = cfg.get("ia", {}), cfg.get("clipping", {})
    n_dest = int(c_clip.get("destaques", 10))
    max_agravo = int(c_clip.get("max_por_agravo", 2))

    candidatos = sorted((n for n in itens if n["pontuacao"] >= limiar_clip),
                        key=lambda n: n["pontuacao"], reverse=True)[: int(c_ia.get("candidatos_triagem", 45))]
    usou_ia = False
    triagem: dict[str, dict] = {}
    if candidatos and ia.disponivel():
        try:
            triagem = ia.triar(candidatos)
            usou_ia = bool(triagem)
        except Exception as erro:  # noqa: BLE001
            log.warning("Triagem por IA indisponível, seguindo com regras: %s", erro)

    for c in candidatos:
        t = triagem.get(c["id"], {})
        c["nota_ia"] = t.get("nota")
        c["agravo_final"] = t.get("agravo") or (c["agravos"][0] if c["agravos"] else "Outros eventos")
        c["escopo_final"] = t.get("escopo") or c["escopo"]
        c["evento"] = t.get("evento") or c["id"]

    if usou_ia:
        nota_min = float(c_ia.get("nota_minima", 5))
        candidatos = [c for c in candidatos if (c["nota_ia"] or 0) >= nota_min]
        candidatos.sort(key=lambda c: ((c["nota_ia"] or 0), c["pontuacao"]), reverse=True)

    destaques, eventos, por_agravo = [], set(), Counter()
    for c in candidatos:
        if c["evento"] in eventos or por_agravo[c["agravo_final"]] >= max_agravo:
            continue
        destaques.append(c)
        eventos.add(c["evento"])
        por_agravo[c["agravo_final"]] += 1
        if len(destaques) >= n_dest:
            break

    ids = {d["id"] for d in destaques}
    outras = sorted((n for n in itens if n["id"] not in ids and n["pontuacao"] >= limiar_feed),
                    key=lambda n: n["pontuacao"], reverse=True)[: int(c_clip.get("outras_noticias", 12))]
    return destaques, outras, usou_ia


def _caminho_pdf(se, forcar: bool):
    base = f"clipping-SE{se.week:02d}-{se.year}"
    caminho = DIR_EDICOES_PDF / f"{base}.pdf"
    if not caminho.exists():
        return caminho, None
    if not forcar:
        return None, caminho
    v = 2
    while (DIR_EDICOES_PDF / f"{base}-v{v}.pdf").exists():
        v += 1
    return DIR_EDICOES_PDF / f"{base}-v{v}.pdf", None


def renderizar_pdf(edicao: dict, caminho) -> None:
    from weasyprint import HTML

    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    html = env.get_template("clipping.html").render(**edicao, ordem_escopo=ORDEM_ESCOPO, nome_escopo=NOME_ESCOPO)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html, base_url=str(RAIZ)).write_pdf(str(caminho))


def gerar(codigo_se: str | None = None, forcar: bool = False, hoje: date | None = None) -> dict | None:
    cfg = carregar_yaml("config.yaml")
    se = semana.de_codigo(codigo_se) if codigo_se else semana.ultima_se_fechada(hoje)
    caminho, existente = _caminho_pdf(se, forcar)
    if caminho is None:
        log.info("Clipping da %s já existe (%s). Nada a fazer.", semana.rotulo(se), existente.name)
        return None

    noticias = ler_json(ARQ_NOTICIAS, [])
    itens = [n for n in noticias if semana.contem(se, para_data(n["data"]))]
    log.info("%s: %d notícias coletadas no período.", semana.rotulo(se), len(itens))
    destaques, outras, usou_ia = _selecionar(itens, cfg)
    if not destaques:
        log.warning("Nenhuma notícia atingiu os critérios para a %s. PDF não gerado.", semana.rotulo(se))
        return None

    s = sessao_http()
    usou_ia_resumo = False
    for d in destaques:
        texto = _texto_completo(d["url"], s)
        resumo = None
        if ia.disponivel():
            try:
                resumo = ia.resumir(d, texto)
                usou_ia_resumo = True
            except Exception as erro:  # noqa: BLE001
                log.warning("Resumo por IA falhou para %s: %s", d["url"], erro)
        if not resumo or not resumo.get("resumo"):
            resumo = {"titulo": d["titulo"], "resumo": ia.resumo_extrativo(texto or d.get("descricao", "")),
                      "local": "", "agravo": d["agravo_final"]}
        d.update({
            "titulo_clipping": resumo["titulo"] or d["titulo"],
            "resumo": resumo["resumo"],
            "local": resumo.get("local", ""),
            "agravo_final": resumo.get("agravo") or d["agravo_final"],
        })
        time.sleep(0.5)

    periodo = semana.periodo_extenso(se)
    texto_panorama = ""
    if usou_ia_resumo:
        try:
            texto_panorama = ia.panorama(
                [{"escopo": d["escopo_final"], "titulo": d["titulo_clipping"], "resumo": d["resumo"]}
                 for d in destaques], semana.rotulo(se), periodo)
        except Exception as erro:  # noqa: BLE001
            log.warning("Panorama por IA falhou: %s", erro)

    def _item(d: dict, i: int) -> dict:
        dt = para_data(d["data"])
        return {
            "ancora": f"n{i}", "titulo": sem_travessao(d.get("titulo_clipping", d["titulo"])),
            "titulo_original": d["titulo"], "resumo": d.get("resumo", ""), "local": d.get("local", ""),
            "agravo": d.get("agravo_final") or (d["agravos"][0] if d.get("agravos") else ""),
            "escopo": d.get("escopo_final", d["escopo"]), "fonte": d["fonte"], "sigla": d["sigla"],
            "data": d["data"], "data_fmt": data_curta(dt), "url": d["url"], "dominio": _dominio(d["url"]),
            "nota_ia": d.get("nota_ia"), "pontuacao": d["pontuacao"],
        }

    itens_dest = [_item(d, i) for i, d in enumerate(destaques, start=1)]
    principal = itens_dest[0]
    secoes = []
    for esc in ORDEM_ESCOPO:
        lista = [x for x in itens_dest[1:] if x["escopo"] == esc]
        if lista:
            secoes.append({"escopo": esc, "nome": NOME_ESCOPO[esc], "itens": lista})

    fontes_cfg = cfg["fontes"]
    edicao = {
        "titulo": cfg["publicacao"]["titulo"],
        "subtitulo": cfg["publicacao"].get("subtitulo", ""),
        "rodape": cfg["publicacao"].get("rodape", ""),
        "se_codigo": semana.codigo(se), "se_numero": f"{se.week:02d}", "se_ano": se.year,
        "rotulo": semana.rotulo(se), "periodo": periodo,
        "data_geracao": data_extenso(date.today()),
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "panorama": texto_panorama,
        "principal": principal, "secoes": secoes, "destaques": itens_dest,
        "outras": [{"titulo": o["titulo"], "fonte": o["sigla"] or o["fonte"], "data_fmt": data_curta(para_data(o["data"])),
                    "url": o["url"], "escopo": o["escopo"]} for o in outras],
        "total_coletado": len(itens),
        "fontes": sorted({f["nome"].split(" | ")[0] for f in fontes_cfg}),
        "ia": usou_ia or usou_ia_resumo,
        "contagem_escopo": {e: sum(1 for x in itens_dest if x["escopo"] == e) for e in ORDEM_ESCOPO},
    }

    renderizar_pdf(edicao, caminho)
    edicao["pdf"] = f"edicoes/{caminho.name}"
    salvar_json(DIR_EDICOES_DADOS / f"{caminho.stem.replace('clipping-', '')}.json", edicao)
    log.info("Clipping gerado: %s", caminho.relative_to(RAIZ))
    return edicao
