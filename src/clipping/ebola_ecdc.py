"""Ebola (vírus Bundibugyo) na RDC: números oficiais do ECDC.

Fonte automática: página do ECDC "Ebola disease outbreak in the Democratic Republic of the Congo",
atualizada nos dias úteis. Cada captura válida vira um registro no histórico
(data/paineis/historico/ebola_ecdc.json). A distribuição por província só é aceita se somar
exatamente o total nacional de casos e de óbitos.
Fonte manual: config/ebola_ecdc.yaml (série histórica a partir dos CDTR semanais do ECDC).
"""
from __future__ import annotations

import re
from datetime import date, datetime

from .util import DADOS, carregar_yaml, ler_json, log, normalizar, salvar_json, sem_html, sessao_http

URL_ECDC = "https://www.ecdc.europa.eu/en/ebola-outbreak-democratic-republic-congo-and-uganda"
FONTE_ECDC = "ECDC, Ebola disease outbreak in the Democratic Republic of the Congo"
ARQ_HISTORICO = DADOS / "paineis" / "historico" / "ebola_ecdc.json"
ARQ_STATUS = DADOS / "paineis" / "historico" / "ebola_ecdc_status.json"

_PALAVRAS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
             "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
             "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "no": 0, "zero": 0}
N = r"(\d{1,3}(?:[ ,]\d{3})+|\d+|" + "|".join(sorted(_PALAVRAS, key=len, reverse=True)) + r")"
_MESES = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"], start=1)}
_NOMES_PROV = {"north kivu": "Nord-Kivu", "nord-kivu": "Nord-Kivu", "nord kivu": "Nord-Kivu",
               "south kivu": "Sud-Kivu", "sud-kivu": "Sud-Kivu", "sud kivu": "Sud-Kivu",
               "haut-uele": "Haut-Uélé", "haut uele": "Haut-Uélé", "bas-uele": "Bas-Uélé", "bas uele": "Bas-Uélé",
               "sud ubangi": "Sud-Ubangi", "sud-ubangi": "Sud-Ubangi", "nord ubangi": "Nord-Ubangi",
               "nord-ubangi": "Nord-Ubangi", "ituri": "Ituri", "tshopo": "Tshopo", "kinshasa": "Kinshasa",
               "equateur": "Équateur", "tshuapa": "Tshuapa", "mongala": "Mongala", "maniema": "Maniema",
               "tanganyika": "Tanganyika", "haut-katanga": "Haut-Katanga", "kasai": "Kasaï"}


def _n(txt: str) -> int:
    return _PALAVRAS[txt] if txt in _PALAVRAS else int(re.sub(r"[ ,]", "", txt))


def _prov(nome: str) -> str:
    nome = nome.strip(" ,.").removesuffix(" province")
    return _NOMES_PROV.get(nome, nome.title())


def _data(dia: str, mes: str, ano: str | int | None, padrao: int) -> date | None:
    if mes not in _MESES:
        return None
    return date(int(ano) if ano else padrao, _MESES[mes], int(dia))


def extrair(texto: str, ano_padrao: int | None = None) -> dict | None:
    """Lê os números do ECDC (página ou CDTR). Devolve None se não achar o total nacional."""
    t = normalizar(sem_html(texto)).replace("’", "'")
    ano_padrao = ano_padrao or date.today().year
    # O total nacional é a frase "total of N confirmed cases, including N deaths" sobre a RDC. A página traz
    # frases iguais para outros países (Quênia, Uganda, Nigéria): vale a ocorrência cujo país citado mais
    # perto, antes dela, é a RDC.
    m = None
    for cand in re.finditer(rf"total of {N} confirmed cases?,? including {N} (?:related )?deaths?", t):
        antes_c = t[max(0, cand.start() - 300):cand.start()]
        paises = [(x.end(), x.group(0)) for x in re.finditer(
            r"\(drc\)|\bdrc\b|democratic republic of the congo|\bkenya\b|\buganda\b|\bnigeria\b|\bswitzerland\b", antes_c)]
        if paises and max(paises)[1] in ("(drc)", "drc", "democratic republic of the congo"):
            m = cand
            break
    if not m:
        return None
    casos, obitos = _n(m.group(1)), _n(m.group(2))
    if obitos > casos:
        return None

    antes = t[max(0, m.start() - 200):m.start()]
    depois = t[m.end():m.end() + 120]
    d = (re.search(r"as of (\d{1,2}) ([a-z]+)(?: (\d{4}))?", antes) or
         re.search(r"on (\d{1,2}) ([a-z]+)(?: (\d{4}))?", antes))
    ate = re.search(r"data up (?:un)?til (\d{1,2}) ([a-z]+)(?: (\d{4}))?", depois)
    data_ref = _data(*d.groups(), ano_padrao) if d else (_data(*ate.groups(), ano_padrao) if ate else None)
    if not data_ref:
        return None

    trecho = t[m.start():m.start() + 4000]
    fim = re.search(r"in kenya|monitoring cases|medically evacuated|uganda", trecho)
    rdc = trecho[:fim.start()] if fim and fim.start() > 200 else trecho

    reg = {"data_referencia": data_ref.isoformat(), "casos": casos, "obitos": obitos,
           "dados_ate": _data(*ate.groups(), data_ref.year).isoformat() if ate else None}
    for chave, padrao in (("hospitalizados", rf"{N} patients are hospitali[sz]ed"),
                          ("recuperados", rf"{N} have recovered")):
        x = re.search(padrao, rdc)
        reg[chave] = _n(x.group(1)) if x else None
    x = re.search(rf"since the previous update, {N} new confirmed cases?,? including {N} deaths?", rdc)
    reg["novos_casos"], reg["novos_obitos"] = (_n(x.group(1)), _n(x.group(2))) if x else (None, None)

    provincias = {}
    padroes = [
        rf"\bin ([a-z][a-z\- ]{{2,24}}?),? {N} cases?,? including {N} deaths?,? (?:have|has) been reported (?:from|in) {N} of {N} health zones",
        rf"\b([a-z][a-z\- ]{{2,24}}?) province remains the most affected,? with {N} cases?,? including {N} deaths?,? reported (?:from|in) {N} of {N} health zones",
        rf"\b([a-z][a-z\- ]{{2,24}}?)(?: province)?:? {N} cases?,? including {N} deaths?,? (?:reported )?(?:from|in) {N} of {N} health zones",
        rf"\bin ([a-z][a-z\- ]{{2,24}}?),? {N} cases? \(including {N} deaths?\),? (?:(?:have|has) been reported )?(?:from|in) {N} of {N} health zones",
    ]
    for i, p in enumerate(padroes):
        for x in re.finditer(p, rdc):
            bruto = re.sub(r"^(?:overall, |and |while |in )", "", x.group(1)).strip()
            if i >= 2 and bruto.removesuffix(" province") not in _NOMES_PROV:
                continue  # padrão genérico: só aceita nomes de províncias conhecidas
            nome = _prov(bruto)
            if nome not in provincias:
                provincias[nome] = {"casos": _n(x.group(2)), "obitos": _n(x.group(3)),
                                    "zs_afetadas": _n(x.group(4)), "zs_total": _n(x.group(5))}
    if provincias and (sum(p["casos"] for p in provincias.values()) != casos
                       or sum(p["obitos"] for p in provincias.values()) != obitos):
        log.warning("Ebola/ECDC: províncias (%d casos) não somam o total (%d); distribuição descartada.",
                    sum(p["casos"] for p in provincias.values()), casos)
        provincias = {}
    reg["provincias"] = provincias or None

    x = re.search(rf"overall, {N} of {N} health zones (?:are|were) (?:currently )?affected(?: across the {N} provinces)?", rdc)
    reg["zs_afetadas"], reg["zs_total"] = (_n(x.group(1)), _n(x.group(2))) if x else (None, None)
    x = re.search(rf"{N} provinces have been affected|across the {N} provinces", rdc)
    reg["n_provincias"] = _n(x.group(1) or x.group(2)) if x else (len(provincias) or None)

    x = re.search(rf"(?:in kenya,? as of [^,]{{3,30}}|as of [^,]{{3,30}},? in kenya),? a total of {N} confirmed cases?,? including {N} deaths?", t)
    reg["quenia"] = {"casos": _n(x.group(1)), "obitos": _n(x.group(2))} if x else None
    # Casos em outros países fora do Quênia (ex.: Nigéria): frase oficial do ECDC, sem reescrita
    original = re.sub(r"\s+", " ", sem_html(texto))
    x = re.search(r"On \d{1,2} [A-Z][a-z]+ 20\d\d, an Ebola case was reported in [A-Z][A-Za-z ]+\.(?: [^.]{0,200}\.)?", original)
    reg["outros_paises"] = x.group(0).strip() if x else None
    reg.update(fonte=FONTE_ECDC, url=URL_ECDC, captura="automática")
    return reg


def _status(ok: bool, motivo: str, **extra) -> None:
    salvar_json(ARQ_STATUS, {"quando": datetime.now().isoformat(timespec="minutes"), "ok": ok,
                             "motivo": motivo, "url": URL_ECDC, **extra})


def capturar() -> dict | None:
    try:
        r = sessao_http().get(URL_ECDC, timeout=40)
        r.raise_for_status()
    except Exception as e:
        log.warning("Ebola/ECDC: página indisponível (%s).", e)
        _status(False, f"página indisponível: {str(e)[:200]}")
        return None
    reg = extrair(r.text)
    texto = sem_html(r.text)
    i = texto.find("total of")
    trecho = texto[max(0, i - 200):i + 3000] if i >= 0 else texto[:3000]
    if not reg:
        log.warning("Ebola/ECDC: texto da página não reconhecido.")
        _status(False, "texto da página não reconhecido", http=r.status_code, trecho=trecho)
        return None
    historico = ler_json(ARQ_HISTORICO, [])
    anteriores = [r for r in registros(capturar_agora=False) if r["data_referencia"] < reg["data_referencia"]]
    if anteriores and reg["casos"] < 0.95 * max(r["casos"] for r in anteriores):
        maior = max(r["casos"] for r in anteriores)
        log.warning("Ebola/ECDC: captura recusada (%d casos, abaixo do acumulado anterior de %d).", reg["casos"], maior)
        _status(False, f"captura recusada: {reg['casos']} casos é menor que o acumulado anterior ({maior})",
                trecho=trecho)
        return None
    # Um registro por data de referência: a captura mais recente substitui a anterior da mesma data
    # (assim uma melhoria no leitor, como o Quênia, também chega aos registros já gravados)
    novo = not any(h["data_referencia"] == reg["data_referencia"] for h in historico)
    reg["capturado_em"] = date.today().isoformat()
    historico = [h for h in historico if h["data_referencia"] != reg["data_referencia"]] + [reg]
    salvar_json(ARQ_HISTORICO, sorted(historico, key=lambda h: h["data_referencia"]))
    if novo:
        log.info("Ebola/ECDC: novo registro %s, %d casos.", reg["data_referencia"], reg["casos"])
    _status(True, "ok", **{k: reg[k] for k in ("data_referencia", "casos", "obitos", "zs_afetadas")},
            provincias_ok=bool(reg["provincias"]), trecho=trecho)
    return reg


def registros(capturar_agora: bool = True) -> list[dict]:
    """Registros oficiais do ECDC (CDTR manuais + capturas da página), do mais antigo ao mais recente."""
    if capturar_agora:
        capturar()
    por_data: dict[str, dict] = {h["data_referencia"]: h for h in ler_json(ARQ_HISTORICO, [])}
    for m in (carregar_yaml("ebola_ecdc.yaml") or {}).get("registros", []):
        m = dict(m)
        m["data_referencia"] = str(m["data_referencia"])
        m["captura"] = "manual"
        por_data.setdefault(m["data_referencia"], m)  # a captura da página prevalece na mesma data
    return sorted(por_data.values(), key=lambda r: r["data_referencia"])
