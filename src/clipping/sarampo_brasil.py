"""Sarampo no Brasil: captura dos números oficiais do Ministério da Saúde.

Fonte automática: página "Situação Epidemiológica do Sarampo" (gov.br/saude), que traz o total
de casos confirmados no ano até uma semana epidemiológica e a distribuição por UF.
Fonte manual: config/sarampo_brasil.yaml (informes, notas e correções).

Cada captura válida vira um registro no histórico (data/paineis/historico/sarampo_brasil.json),
o que forma a curva acumulada ao longo das semanas. Uma captura só é aceita se a soma das UFs
bater com o total; caso contrário, nada é gravado e o painel segue com o último registro válido.
"""
from __future__ import annotations

import re
from datetime import date

from epiweeks import Week, Year

from .mapas import _ufs
from .util import DADOS, carregar_yaml, ler_json, log, normalizar, salvar_json, sem_html, sessao_http

URL_MS = "https://www.gov.br/saude/pt-br/assuntos/saude-de-a-a-z/s/sarampo/situacao-epidemiologica"
FONTE_MS = "Ministério da Saúde, Situação Epidemiológica do Sarampo"
ARQ_HISTORICO = DADOS / "paineis" / "historico" / "sarampo_brasil.json"
ARQ_STATUS = DADOS / "paineis" / "historico" / "sarampo_brasil_status.json"

_NUM = r"(\d{1,3}(?:\.\d{3})*|\d+)"
_PCT = r"\(\s*\d+(?:[.,]\d+)?\s*%\s*\)"


def _int(txt: str) -> int:
    return int(txt.replace(".", ""))


def extrair(texto: str, ano: int) -> dict | None:
    """Extrai do texto da página o total do ano, a SE de referência e os casos por UF."""
    t = normalizar(sem_html(texto))
    m = re.search(rf"\bem {ano}\s*,?\s*(?:ate a (?:semana epidemiologica|se)\s*(?:n[.ºo]*\s*)?(\d{{1,2}})\s*,?\s*)?"
                  rf"[^.]{{0,40}}?foram (?:confirmados|registrados) {_NUM} casos", t)
    if not m:
        return None
    casos = _int(m.group(2))
    if m.group(1):
        se = int(m.group(1))
    else:  # sem SE explícita: considera a última SE encerrada na data da captura
        se = Week.fromdate(date.today()).week - 1 if date.today().year == ano else Year(ano).totalweeks()
        se = max(se, 1)
    # Trecho do ano: até o início do parágrafo de outro ano
    resto = t[m.end():]
    fim = re.search(rf"\bem (?!{ano})(20\d\d)\b", resto)
    trecho = t[m.start():m.end() + (fim.start() if fim else 3000)]

    ufs = {}
    for sigla, p in _ufs().items():
        nome = re.escape(normalizar(p["n"])) + r"(?!\s+do\s+(?:sul|norte))"
        depois = re.search(rf"{_NUM}\s*{_PCT}[^\d%;]{{0,50}}?\b(?:n[oa]s?|em|d[oa])\s+"
                           rf"(?:estado\s+d[eo]s?\s+)?{nome}\b", trecho)
        antes = re.search(rf"\b{nome}\b[^.;%]{{0,80}}?{_NUM}\s*{_PCT}", trecho)
        achado = depois or antes
        if achado:
            ufs[sigla] = _int(achado.group(1))
    obitos = 0 if re.search(rf"em {ano}[^.]{{0,80}}nenhum obito|sem (?:registro de )?obitos", trecho) else None
    m_ob = re.search(rf"{_NUM} obitos?", trecho)
    if m_ob:
        obitos = _int(m_ob.group(1))

    if ufs and sum(ufs.values()) != casos:
        log.warning("Sarampo/MS: soma das UFs (%d) difere do total (%d); UFs descartadas.", sum(ufs.values()), casos)
        ufs = {}
    return {
        "data_referencia": Week(ano, se).enddate().isoformat(),
        "se": se, "casos": casos, "obitos": obitos, "ufs": ufs or None,
        "fonte": FONTE_MS, "url": URL_MS, "captura": "automática",
    }


def _status(ok: bool, motivo: str, **extra) -> None:
    """Registra o resultado da última tentativa de captura (para diagnóstico)."""
    from datetime import datetime
    salvar_json(ARQ_STATUS, {"quando": datetime.now().isoformat(timespec="minutes"), "ok": ok,
                             "motivo": motivo, "url": URL_MS, **extra})


def capturar(ano: int) -> dict | None:
    """Baixa a página do MS e grava o registro no histórico, se for novo."""
    try:
        r = sessao_http().get(URL_MS, timeout=40)
        r.raise_for_status()
    except Exception as e:  # rede fora do ar não pode derrubar o site
        log.warning("Sarampo/MS: página indisponível (%s).", e)
        _status(False, f"página indisponível: {str(e)[:200]}")
        return None
    reg = extrair(r.text, ano)
    if not reg:
        log.warning("Sarampo/MS: não foi possível ler o total de %d na página.", ano)
        t = sem_html(r.text)
        i = normalizar(t).find(f"em {ano}")
        _status(False, "texto da página não reconhecido", http=r.status_code, tamanho=len(r.text),
                trecho=t[max(0, i - 300):i + 900] if i >= 0 else t[:1200])
        return None
    historico = ler_json(ARQ_HISTORICO, [])
    chave = (reg["data_referencia"], reg["casos"])
    if not any((h["data_referencia"], h["casos"]) == chave for h in historico):
        reg["capturado_em"] = date.today().isoformat()
        historico.append(reg)
        salvar_json(ARQ_HISTORICO, sorted(historico, key=lambda h: h["data_referencia"]))
        log.info("Sarampo/MS: novo registro SE %d, %d casos.", reg["se"], reg["casos"])
    _status(True, "ok", se=reg["se"], casos=reg["casos"], ufs=reg["ufs"])
    return reg


def registros(capturar_agora: bool = True) -> tuple[int, list[dict]]:
    """Registros oficiais (manuais + capturados), do mais antigo ao mais recente.

    Quando há dois registros com a mesma data de referência, vale o manual (correção)."""
    cfg = carregar_yaml("sarampo_brasil.yaml")
    ano = int(cfg.get("ano", date.today().year))
    if capturar_agora:
        capturar(ano)
    por_data: dict[str, dict] = {}
    for h in ler_json(ARQ_HISTORICO, []):
        if h["data_referencia"].startswith(str(ano)):
            por_data[h["data_referencia"]] = h
    for m in cfg.get("registros", []):
        m = dict(m)
        m["data_referencia"] = str(m["data_referencia"])
        m["captura"] = "manual"
        por_data[m["data_referencia"]] = m
    lista = sorted(por_data.values(), key=lambda r: r["data_referencia"])
    for r in lista:
        if not r.get("se"):
            d = date.fromisoformat(r["data_referencia"])
            r["se"] = Week.fromdate(d).week
    return ano, lista
