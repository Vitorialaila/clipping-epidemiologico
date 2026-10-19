"""Geração semanal do clipping (seleção, resumos e PDF)."""
from __future__ import annotations

import time
from collections import Counter
from datetime import date, datetime, timezone
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import fotos, ia, icones, mapas, semana
from .pontuar import limiares
from .util import normalizar
from .util import (ARQ_NOTICIAS, DIR_EDICOES_DADOS, DIR_EDICOES_PDF, DOCS, NOME_ESCOPO, ORDEM_ESCOPO, RAIZ,
                   TEMPLATES, carregar_yaml, data_curta, data_extenso, ler_json, log, para_data,
                   salvar_json, sem_travessao, sessao_http)


def _texto_completo(url: str, s) -> tuple[str, str]:
    """Retorna (texto extraído, HTML bruto) da página da notícia."""
    try:
        import trafilatura
        r = s.get(url, timeout=40)
        r.raise_for_status()
        return trafilatura.extract(r.text, include_comments=False, include_tables=True) or "", r.text
    except Exception as erro:  # noqa: BLE001
        log.warning("Não foi possível ler o texto de %s: %s", url, erro)
        return "", ""


def _dominio(url: str) -> str:
    return urlsplit(url).netloc.replace("www.", "")


_VAZIAS = set("""a o as os de da do das dos e em no na nos nas um uma para por com sem sobre apos ate entre que
se sua seu mais menos neste nesta este esta esse essa pela pelo pelas pelos ao aos the of in and to for on at
with from after by is are was new novo nova novos novas casos caso cases case saude health brasil brazil""".split())


def _palavras(titulo: str) -> set[str]:
    return {w for w in normalizar(titulo).replace("-", " ").split()
            if (len(w) >= 4 or w.isdigit()) and w not in _VAZIAS}


def _agrupar_eventos(candidatos: list[dict]) -> None:
    """Sem IA: notícias do mesmo agravo que compartilham ao menos duas palavras significativas no título
    (ex.: \"alagamentos na 26 de Setembro\") são tratadas como o mesmo evento; fica a de maior nota."""
    vistos: list[tuple[str, set[str], str]] = []
    for c in candidatos:
        pal = _palavras(c["titulo"])
        for agravo, pal_ref, chave in vistos:
            if agravo == c["agravo_final"] and len(pal & pal_ref) >= 2:
                c["evento"] = chave
                break
        else:
            vistos.append((c["agravo_final"], pal, c["evento"]))


def _na_cota(c: dict, escopos: set, agravos: set, termos: list[str] = ()) -> bool:
    if escopos and (c.get("escopo_final") or c["escopo"]) in escopos:
        return True
    if termos and any(t in normalizar(c["titulo"]) for t in termos):
        return True
    return bool(agravos) and (c["agravo_final"] in agravos or bool(agravos & set(c.get("agravos", []))))


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

    if not usou_ia:
        _agrupar_eventos(candidatos)

    destaques, eventos, por_agravo = [], set(), Counter()
    grupos = {a: (g["nome"], int(g.get("maximo", max_agravo))) for g in c_clip.get("grupos", []) for a in g["agravos"]}

    def _grupo(c) -> tuple[str, int]:
        return grupos.get(c["agravo_final"], (c["agravo_final"], max_agravo))

    def _cabe(c) -> bool:
        nome, teto = _grupo(c)
        return c["evento"] not in eventos and por_agravo[nome] < teto and c not in destaques

    def _incluir(c) -> None:
        destaques.append(c)
        eventos.add(c["evento"])
        por_agravo[_grupo(c)[0]] += 1

    # 1) Vagas garantidas por tema (DF, vírus respiratórios, clima e desastres)
    for cota in c_clip.get("cotas", []):
        escopos, agravos = set(cota.get("escopo", [])), set(cota.get("agravos", []))
        termos = [normalizar(t) for t in cota.get("termos", [])]
        alvo = int(cota.get("minimo", 1))
        atendidos = sum(1 for d in destaques if _na_cota(d, escopos, agravos, termos))
        for c in candidatos:
            if atendidos >= alvo or len(destaques) >= n_dest:
                break
            if _na_cota(c, escopos, agravos, termos) and _cabe(c):
                _incluir(c)
                atendidos += 1
    # 2) Demais vagas pela ordem de relevância
    for c in candidatos:
        if len(destaques) >= n_dest:
            break
        if _cabe(c):
            _incluir(c)
    ordem = {id(c): i for i, c in enumerate(candidatos)}
    destaques.sort(key=lambda c: ordem[id(c)])

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


def renderizar_pdf(edicao: dict, caminho, base_img: str = "docs/") -> None:
    from weasyprint import HTML

    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    html = env.get_template("clipping.html").render(**edicao, ordem_escopo=ORDEM_ESCOPO, nome_escopo=NOME_ESCOPO,
                                                    icone=icones.svg, base_img=base_img)
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
    id_edicao = caminho.stem.replace("clipping-", "")
    dir_img = DIR_EDICOES_PDF / "img" / id_edicao
    for i, d in enumerate(destaques, start=1):
        texto, html = _texto_completo(d["url"], s)
        resumo = None
        if ia.disponivel():
            try:
                resumo = ia.resumir(d, texto)
                usou_ia_resumo = True
            except Exception as erro:  # noqa: BLE001
                log.warning("Resumo por IA falhou para %s: %s", d["url"], erro)
        if not resumo or not resumo.get("resumo"):
            resumo = {"titulo": d["titulo"], "resumo": ia.resumo_extrativo(texto or d.get("descricao", ""), titulo=d["titulo"]),
                      "local": "", "agravo": d["agravo_final"], "paises": [], "uf": ""}
            if d.get("idioma", "pt") != "pt":
                # Sem IA, notícias em outro idioma passam por tradução automática para o português
                resumo["titulo"] = ia.traduzir(resumo["titulo"])
                resumo["resumo"] = ia.traduzir(resumo["resumo"])
        d.update({
            "titulo_clipping": resumo["titulo"] or d["titulo"],
            "resumo": resumo["resumo"],
            "local": resumo.get("local", ""),
            "agravo_final": resumo.get("agravo") or d["agravo_final"],
        })
        # Localização (países ISO3 e UF): informada pela IA ou identificada no texto
        paises, uf = mapas.validar(resumo.get("paises"), resumo.get("uf"))
        if not paises and not uf:
            paises, uf = mapas.localizar(" ".join([d["titulo"], d.get("descricao", ""), resumo.get("local", ""),
                                                   (texto or "")[:1500]]), d["escopo_final"])
        if uf and "BRA" not in paises:
            paises.insert(0, "BRA")
        d["paises"], d["uf"] = paises, uf
        if not d.get("local"):
            d["local"] = mapas.nome_pais(paises[0]) if len(paises) == 1 else ""
        # Imagem: foto com licença verificada > mapa de localização > pictograma
        d["icone"] = icones.icone_para(d["agravo_final"], d["titulo"])
        foto = fotos.obter(d["url"], html, dir_img / f"n{i}", s)
        if foto:
            d["visual"] = {"tipo": "foto", "src": foto["arquivo"].relative_to(DOCS).as_posix(),
                           "credito": foto["credito"], "proporcao": foto["proporcao"]}
        else:
            svg = mapas.mapa_local(paises, uf, d["escopo_final"])
            if svg:
                arq = dir_img / f"n{i}.svg"
                arq.parent.mkdir(parents=True, exist_ok=True)
                arq.write_text(svg, encoding="utf-8")
                d["visual"] = {"tipo": "mapa", "src": arq.relative_to(DOCS).as_posix()}
            else:
                d["visual"] = {"tipo": "icone"}
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
            "nota_ia": d.get("nota_ia"), "pontuacao": d["pontuacao"], "n": i,
            "paises": d.get("paises", []), "uf": d.get("uf", ""), "icone": d.get("icone", "alerta"),
            "visual": d.get("visual", {"tipo": "icone"}),
        }

    itens_dest = [_item(d, i) for i, d in enumerate(destaques, start=1)]
    eventos = [{"n": x["n"], "paises": x["paises"], "uf": x["uf"], "escopo": x["escopo"]} for x in itens_dest]
    arq_mapa = dir_img / "mapa-semana.svg"
    arq_mapa.parent.mkdir(parents=True, exist_ok=True)
    arq_mapa.write_text(mapas.mapa_semana(eventos), encoding="utf-8")
    arq_mapa_escuro = dir_img / "mapa-semana-escuro.svg"
    arq_mapa_escuro.write_text(mapas.mapa_semana(eventos, tema="escuro"), encoding="utf-8")
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
        "instituicoes": cfg["publicacao"].get("instituicoes", []),
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
        "mapa": arq_mapa.relative_to(DOCS).as_posix(),
        "mapa_escuro": arq_mapa_escuro.relative_to(DOCS).as_posix(),
    }

    renderizar_pdf(edicao, caminho)
    edicao["pdf"] = f"edicoes/{caminho.name}"
    salvar_json(DIR_EDICOES_DADOS / f"{caminho.stem.replace('clipping-', '')}.json", edicao)
    log.info("Clipping gerado: %s", caminho.relative_to(RAIZ))
    return edicao
