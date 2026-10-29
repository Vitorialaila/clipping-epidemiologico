"""Pontuação de relevância epidemiológica por regras explícitas (config/criterios.yaml)."""
from __future__ import annotations

import re
from datetime import date
from functools import lru_cache

from .util import carregar_yaml, normalizar


def _padrao(termos: list[str]) -> re.Pattern:
    """Termos exatos; um asterisco no fim indica prefixo (ex.: "shigel*" casa shigella, shigelose, shigellosis)."""
    partes = set()
    for t in termos:
        t = normalizar(str(t)).strip()
        partes.add(re.escape(t[:-1]) + r"\w*" if t.endswith("*") else re.escape(t))
    partes = sorted(partes, key=len, reverse=True)
    return re.compile(r"(?<![\w-])(" + "|".join(partes) + r")(?![\w])")


@lru_cache(maxsize=1)
def _criterios():
    c = carregar_yaml("criterios.yaml")
    agravos = [(a["nome"], float(a["peso"]), _padrao(a["termos"])) for a in c["agravos"]]
    eventos = [(float(e["peso"]), _padrao(e["termos"])) for e in c["eventos"]]
    ride = c.get("ride_df", [])
    locais = []
    for nome, v in c["localidades"].items():
        termos = list(v["termos"])
        if nome == "RIDE":
            for m in ride:
                termos += [m["nome"]] + m.get("termos", [])
        locais.append((nome, float(v["peso"]), _padrao(termos)))
    ajustes = [(float(a["peso"]), _padrao(a["termos"])) for a in c.get("ajustes", [])]
    exclusoes = _padrao(c["exclusoes"]) if c.get("exclusoes") else None
    return c, agravos, eventos, locais, ajustes, exclusoes


@lru_cache(maxsize=1)
def _exterior():
    termos = _criterios()[0].get("exterior")
    return _padrao(termos) if termos else None


@lru_cache(maxsize=1)
def _categorias():
    c = _criterios()[0]
    cat = c.get("categorias", {})
    info = {a["nome"]: (a.get("lnnc", "nao"), a.get("lnnc_obito")) for a in c["agravos"]}
    return info, {k: _padrao(v) for k, v in cat.items()}


_NUM_CASOS = re.compile(r"(?<![\w-])(\d[\d.,]*|um|uma|dois|duas|tres|quatro|cinco|seis|sete|oito|nove|dez|onze|doze|"
                        r"vinte|trinta|cem|mil|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:[a-z-]+\s+){0,2}?"
                        r"(?:casos|cases|obitos|mortes|deaths|incendios|focos)(?![\w])")
def _no_original(titulo: str, termo: str) -> str:
    """Devolve o trecho do título original (com acentos e maiúsculas) que corresponde ao termo normalizado."""
    import unicodedata
    norm, mapa = [], []
    for i, ch in enumerate(titulo):
        for c in unicodedata.normalize("NFKD", ch):
            if not unicodedata.combining(c):
                norm.append(c.lower())
                mapa.append(i)
    k = "".join(norm).find(termo)
    return titulo[mapa[k]:mapa[k + len(termo) - 1] + 1] if k >= 0 and termo else termo


# "caso de sarampo", "casos suspeitos de raiva", "suspeita de meningite" ("em caso de" fica de fora: é orientação)
_ANTES_AGRAVO = re.compile(r"(?<!em )(?<![\w-])(casos? suspeitos? de|casos? confirmados? de|casos? de|suspeitas? de|suspeitos? de)"
                           r"\s+(?:[a-z-]+\s+)?$")
# "measles case", "caso de sarampo congênito" já coberto acima; aqui o caso vem depois do agravo
_DEPOIS_AGRAVO = re.compile(r"^\s+(?:[a-z-]+\s+)?(case|cases|patient|patients|infection|infections)(?![\w])")


def _caso_do_agravo(titulo_n: str, nomes: set) -> str | None:
    """Ocorrência expressa como caso ou suspeita de um agravo de notificação imediata citado no título."""
    for nome, _, padrao in _criterios()[1]:
        if nome not in nomes:
            continue
        for m in padrao.finditer(titulo_n):
            antes = _ANTES_AGRAVO.search(titulo_n[max(0, m.start() - 40):m.start()])
            if antes:
                return antes.group(1) + " " + m.group(1)
            depois = _DEPOIS_AGRAVO.search(titulo_n[m.end():m.end() + 30])
            if depois:
                return m.group(1) + " " + depois.group(1)
    return None


ROTULO_ESCOPO = {"DF": "DF e RIDE", "Brasil": "Brasil", "Américas": "Américas", "Internacional": "internacional"}


def categoria(titulo_n: str, corpo: str, nomes_agravos: list[str], escopo: str, fonte: dict,
              local: bool, rebaixar: bool = False, no_titulo: set | None = None, titulo: str = "") -> tuple[str, str]:
    """Classifica a notícia em alerta, atencao ou monitoramento e diz por quê.

    Alerta: comunicado de canal oficial de alerta; agravo de notificação imediata (Portaria GM/MS nº 6.734/2025)
    com ocorrência; óbito por agravo cujo óbito é de notificação imediata; ou ocorrência de agravo no DF/RIDE.
    Atenção: agravo de notificação semanal (ou fora da lista) com ocorrência; desastre com impacto na população;
    demais comunicados dos canais oficiais. Monitoramento: o restante que passa do corte do feed."""
    def motivo(*partes):
        return " · ".join(p for p in partes if p)

    orig = lambda termo: _no_original(titulo, termo) if titulo else termo

    def notif(a):
        return {"imediata": f"{a[0]} (notificação imediata)", "semanal": f"{a[0]} (notificação semanal)"}.get(a[1], a[0])

    info, pad = _categorias()
    # Ocorrência e impacto são lidos no TÍTULO: é ele que diz se a notícia relata um evento; o corpo cita
    # surtos e óbitos também em matérias explicativas, de contexto ou de prevenção
    busca = lambda chave: pad[chave].search(titulo_n) if chave in pad else None
    m_oc, m_num = busca("ocorrencia"), _NUM_CASOS.search(titulo_n)
    ocorrencia = (m_oc.group(1) if m_oc else None) or (m_num.group(0) if m_num else None)
    obito = busca("obito")
    impacto = busca("impacto_desastre") or (m_num if m_num and re.search(r"incendios|focos", m_num.group(0)) else None)
    previsao = busca("previsao")
    if previsao and impacto and not re.search(r"\b(?:provoca|provocam|causa|causam|deixa|deixam|atinge|atingem|registra|"
                                              r"registram|desabou|desabam|alagou|alagam|transborda|transbordou)\b", titulo_n):
        impacto = None  # aviso ou orientação sobre risco, sem impacto relatado
    situacao = busca("situacao")
    titulo_alerta = busca("titulo_alerta")

    agravos = [(n, *info.get(n, ("nao", None))) for n in nomes_agravos]
    doencas = [a for a in agravos if a[1] != "desastre"]
    # Notificação imediata e óbito só contam para agravos citados no TÍTULO (uma menção de passagem no corpo,
    # como "gripe aviária" numa notícia sobre Newcastle, não define a categoria)
    titulo_ok = (lambda a: a[0] in no_titulo) if no_titulo is not None else (lambda a: True)
    imediatas = [a for a in doencas if a[1] == "imediata" and titulo_ok(a)]
    obito_imediato = [a for a in doencas if a[2] == "imediata" and titulo_ok(a)]
    if no_titulo is not None:  # o agravo do título vem primeiro no motivo
        doencas.sort(key=lambda a: a[0] not in no_titulo)
    desastres = [a for a in agravos if a[1] == "desastre"]
    minima = fonte.get("categoria_minima")
    onde = ROTULO_ESCOPO.get(escopo, escopo)
    if rebaixar and not minima:  # estudo, institucional, explicativo ou relato de anos anteriores
        base = notif(doencas[0]) if (doencas := [a for a in agravos if a[1] != "desastre"]) else (agravos[0][0] if agravos else None)
        return "monitoramento", " · ".join(p for p in (base, "estudo, orientação ou relato retrospectivo", onde) if p)

    if minima == "alerta" or (minima == "atencao" and titulo_alerta):
        return "alerta", motivo("comunicado oficial de alerta", notif(doencas[0]) if doencas else None, onde)
    if imediatas and not ocorrencia:
        ocorrencia = _caso_do_agravo(titulo_n, {a[0] for a in imediatas})
    if imediatas and ocorrencia:
        return "alerta", motivo(notif(imediatas[0]), orig(ocorrencia), onde)
    if obito_imediato and obito:
        return "alerta", motivo(f"{obito_imediato[0][0]} (óbito de notificação imediata)", orig(obito.group(1)), onde)
    if local and doencas and ocorrencia:
        return "alerta", motivo(notif(doencas[0]), orig(ocorrencia), "DF e RIDE")
    if local and desastres and impacto:
        return "alerta", motivo(desastres[0][0], orig(impacto.group(0)), "DF e RIDE")
    if minima == "atencao":
        return "atencao", motivo("comunicado oficial", notif(doencas[0]) if doencas else None, onde)
    if doencas and ocorrencia:
        return "atencao", motivo(notif(doencas[0]), orig(ocorrencia), onde)
    if desastres and impacto:
        return "atencao", motivo(desastres[0][0], orig(impacto.group(0)), onde)
    if imediatas and situacao:
        return "atencao", motivo(notif(imediatas[0]), f"situação ou risco ({orig(situacao.group(1))})", onde)
    if doencas:
        return "monitoramento", motivo(notif(doencas[0]), "sem ocorrência relatada no título", onde)
    return "monitoramento", motivo(desastres[0][0] if desastres else None,
                                   "aviso, previsão ou sem impacto relatado" if desastres else None, onde)


def limiares() -> tuple[float, float]:
    c = _criterios()[0]
    return float(c.get("limiar_feed", 4)), float(c.get("limiar_clipping", 5))


def pontuar(titulo: str, descricao: str, fonte: dict, ano_referencia: int | None = None) -> dict:
    """Retorna pontuação, agravos identificados, escopo geográfico e termos de evento."""
    c, agravos, eventos, locais, ajustes, exclusoes = _criterios()
    titulo_n = normalizar(titulo)
    # Fontes cujo resumo traz o sumário da edição inteira (ex.: MMWR) são avaliadas só pelo título
    corpo = "" if fonte.get("so_titulo") else normalizar(descricao)
    if exclusoes:  # expressões em sentido figurado ou fora de contexto ("lei seca", "cortina de fumaça")
        titulo_n = exclusoes.sub(" ", titulo_n)
        corpo = exclusoes.sub(" ", corpo)
    texto = titulo_n + " " + corpo
    canal_alerta = bool(fonte.get("canal_alerta"))

    achados = []
    for nome, peso, padrao in agravos:
        no_titulo = bool(padrao.search(titulo_n))
        if no_titulo:
            achados.append((peso + 0.5, nome))
        elif padrao.search(texto):
            # Citado só no corpo do texto: pesa menos (evita falsos positivos, como menções de passagem)
            achados.append((peso * 0.5, nome))
    achados.sort(reverse=True)
    nomes_agravos = [n for _, n in achados]

    pts_evento, termos_evento, evento_titulo = 0.0, [], 0.0
    for peso, padrao in eventos:
        m = padrao.search(texto)
        if m:
            pts_evento += peso
            termos_evento.append(m.group(1))
            if padrao.search(titulo_n):
                evento_titulo += peso
    pts_evento = min(pts_evento, 6.0)

    # Sem agravo reconhecido, só entra se o próprio título trouxer um evento forte (ex.: "alagamentos",
    # "surto"); termos soltos no corpo ("tendência", "investigação") não bastam
    if not achados and evento_titulo < 2.5 and not canal_alerta:
        return {"pontuacao": 0.0, "agravos": [], "escopo": fonte.get("escopo_padrao", "Internacional"),
                "termos_evento": termos_evento, "categoria": "monitoramento", "motivo": ""}

    pts = float(fonte.get("peso", 1))
    if achados:
        pts += achados[0][0] + min(0.5 * (len(achados) - 1), 1.0)
    pts += pts_evento

    # O escopo padrão da fonte (ex.: MS = Brasil) só é substituído por uma localidade mais próxima
    escopo = fonte.get("escopo_padrao") or "Internacional"
    pts_local = next((p for n, p, _ in locais if n == escopo), 0.0)
    for nome, peso, padrao in locais:
        if peso > pts_local and padrao.search(texto):
            escopo, pts_local = nome, peso
    # Fonte brasileira (escopo padrão "Brasil") noticiando outro país: o título cita um país estrangeiro e
    # nenhuma localidade brasileira (ex.: G1 sobre a França ou os EUA)
    if escopo == "Brasil" and fonte.get("escopo_padrao") == "Brasil":
        pad_loc = {n: pd for n, _, pd in locais}
        if not any(pad_loc[n].search(titulo_n) for n in ("DF", "Brasil") if n in pad_loc):
            if "Américas" in pad_loc and pad_loc["Américas"].search(titulo_n):
                escopo, pts_local = "Américas", next(p for n, p, _ in locais if n == "Américas")
            elif _exterior() and _exterior().search(titulo_n):
                escopo, pts_local = "Internacional", 0.0
    pts += pts_local

    # Penalidades olham só o título: é ele que diz do que a matéria trata; o corpo de notícias sobre eventos
    # costuma trazer jargão institucional ("strengthen", "pesquisadores") que não muda a natureza da notícia
    rebaixar = False
    # Resposta de emergência no título (COE, centro de emergência): é evento, mesmo que cite pesquisa ou orientação
    resposta = re.search(r"\b(centro de (operacoes de )?emergencia|coe|emergencia em saude publica|gabinete de crise sanitaria)\b", titulo_n)
    for peso, padrao in ([] if resposta else ajustes):
        if padrao.search(titulo_n):
            pts += peso
            rebaixar = True

    # Relato retrospectivo: o título cita apenas anos já encerrados (ex.: "January 2024-June 2025" em 2026)
    anos = [int(a) for a in re.findall(r"(?<!\d)(20\d{2})(?!\d)", titulo)]
    if anos and max(anos) < (ano_referencia or date.today().year):
        pts += float(c.get("penalidade_retrospectivo", -4))
        rebaixar = True

    # Canais oficiais de alerta (DON da OMS, alertas da OPAS, atualizações e avaliações do ECDC): cada item
    # é, por definição, um evento; nunca ficam abaixo do piso, salvo relatos retrospectivos
    if canal_alerta and not (anos and max(anos) < (ano_referencia or date.today().year)):
        pts = max(pts, float(c.get("piso_canal_alerta", 6)))

    local = escopo == "DF" or bool(municipios(titulo, descricao))
    no_titulo = {nome for nome, _, padrao in agravos if padrao.search(titulo_n)}
    cat, motivo = categoria(titulo_n, corpo, nomes_agravos, escopo, fonte, local, rebaixar, no_titulo, titulo)
    return {"pontuacao": round(max(pts, 0.0), 2), "agravos": nomes_agravos[:3], "escopo": escopo,
            "termos_evento": termos_evento, "categoria": cat, "motivo": motivo}


@lru_cache(maxsize=1)
def _municipios():
    c = _criterios()[0]
    df = next(v for n, v in c["localidades"].items() if n == "DF")
    lista = [("Distrito Federal", _padrao(df["termos"]))]
    for m in c.get("ride_df", []):
        lista.append((f'{m["nome"]} ({m["uf"]})', _padrao([m["nome"]] + m.get("termos", []))))
    return lista


def municipios(titulo: str, descricao: str = "") -> list[str]:
    """Municípios da RIDE-DF (incluindo o Distrito Federal) citados na notícia."""
    texto = normalizar(titulo) + " " + normalizar(descricao)
    return [nome for nome, padrao in _municipios() if padrao.search(texto)]
