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


_EXTENSO = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7,
            "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13, "catorze": 14, "quatorze": 14,
            "quinze": 15, "dezesseis": 16, "dezessete": 17, "dezoito": 18, "dezenove": 19, "vinte": 20}
_QTD = r"(\d{1,3}(?:\.\d{3})*|" + "|".join(sorted(_EXTENSO, key=len, reverse=True)) + r")"
_MESES = {m: i for i, m in enumerate(["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto",
                                       "setembro", "outubro", "novembro", "dezembro"], start=1)}


def _int(txt: str) -> int:
    return _EXTENSO[txt] if txt in _EXTENSO else int(txt.replace(".", ""))


def _nome_uf(nome: str) -> str:
    # "Mato Grosso" não pode casar com "Mato Grosso do Sul"; "São Paulo" não pode casar com "São Paulo de Olivença"
    return re.escape(normalizar(nome)) + r"(?!\s+do\s+(?:sul|norte))(?!\s+de\s+[a-z])\b"


def _ufs_percentual(trecho: str) -> dict:
    """Redação com percentuais: '36 (85,7%) casos em São Paulo' ou 'São Paulo, com 36 (85,7%)'."""
    ufs = {}
    for sigla, p in _ufs().items():
        nome = _nome_uf(p["n"])
        depois = re.search(rf"{_NUM}\s*{_PCT}[^\d%;]{{0,50}}?\b(?:n[oa]s?|em|d[oa])\s+"
                           rf"(?:estado\s+d[eo]s?\s+)?{nome}", trecho)
        antes = re.search(rf"\b{nome}[^.;%]{{0,80}}?{_NUM}\s*{_PCT}", trecho)
        achado = depois or antes
        if achado:
            ufs[sigla] = _int(achado.group(1))
    return ufs


def _ufs_por_mencao(trecho: str) -> dict:
    """Redação corrida: 'O estado de São Paulo concentrou 41 casos [...] No Acre, foram registrados dois casos'.

    Cada menção a uma UF abre um segmento até a próxima menção (ou o fim da frase); vale o primeiro
    'N caso(s)' do segmento, e só a primeira contagem encontrada para cada UF."""
    mencoes = []
    for sigla, p in _ufs().items():
        for m in re.finditer(rf"\b{_nome_uf(p['n'])}", trecho):
            mencoes.append((m.start(), m.end(), sigla))
    mencoes.sort()
    ufs = {}
    for i, (_, fim, sigla) in enumerate(mencoes):
        limite = mencoes[i + 1][0] if i + 1 < len(mencoes) else len(trecho)
        segmento = re.split(r"\.\s", trecho[fim:limite], maxsplit=1)[0]
        m = re.search(rf"\b{_QTD}\s+(?:\([^)]*\)\s*)?casos?\b", segmento)
        if m and sigla not in ufs:
            ufs[sigla] = _int(m.group(1))
    return ufs


def extrair(texto: str, ano: int) -> dict | None:
    """Extrai do texto da página o total do ano, a data/SE de referência, os casos por UF e as frases sobre surtos."""
    original = sem_html(texto)
    t = normalizar(original)
    m = re.search(rf"\bem {ano}\s*,?\s*"
                  rf"(?:ate (?:a (?:semana epidemiologica|se)\s*(?:n[.ºo]*\s*)?(\d{{1,2}})|(\d{{1,2}}) de ([a-z]+)(?: de {ano})?)\s*,?\s*)?"
                  rf"[^.]{{0,40}}?foram (?:confirmados|registrados) {_NUM} casos", t)
    if not m:
        return None
    casos = _int(m.group(4))
    if m.group(1):  # "até a semana epidemiológica 38"
        se = int(m.group(1))
        data_ref = Week(ano, se).enddate()
    elif m.group(2) and m.group(3) in _MESES:  # "até 19 de outubro"
        data_ref = date(ano, _MESES[m.group(3)], int(m.group(2)))
        se = Week.fromdate(data_ref).week
    else:  # sem referência explícita: última SE encerrada na data da captura
        semana = Week.fromdate(date.today()) - 1 if date.today().year == ano else Week(ano, Year(ano).totalweeks())
        se, data_ref = semana.week, semana.enddate()

    # Trecho do ano: até o parágrafo de outro ano ("Em 2025 ,"), no máximo 3.000 caracteres
    resto = t[m.end():]
    fim = re.search(rf"\bem (?!{ano})(20\d\d)\s*,", resto)
    trecho = t[m.start():m.end() + min(fim.start() if fim else 3000, 3000)]

    ufs = {}
    for metodo in (_ufs_percentual, _ufs_por_mencao):
        candidato = metodo(trecho)
        if candidato and sum(candidato.values()) == casos:
            ufs = candidato
            break
    if not ufs:
        log.warning("Sarampo/MS: casos por UF não conferem com o total (%d); UFs não usadas.", casos)

    obitos = 0 if re.search(rf"nenhum obito|sem (?:registro de )?obitos", trecho) else None
    m_ob = re.search(r"(?<![\d.])(\d{1,3})\s*(?:\([^)]*\)\s*)?obitos?\b(?! em)", trecho)
    if m_ob and int(m_ob.group(1)) <= casos:
        obitos = int(m_ob.group(1))

    # Frases do MS sobre surtos e origem dos casos (texto oficial, sem reescrita)
    i = t.find(m.group(0))
    frases = re.split(r"(?<=\.)\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕ])", original[i:i + len(trecho)])
    notas = [f.strip() for f in frases[1:] if re.search(r"surto|importad|fonte de infec", normalizar(f))
             and not normalizar(f).startswith("importante")][:3]
    return {
        "data_referencia": data_ref.isoformat(),
        "se": se, "casos": casos, "obitos": obitos, "ufs": ufs or None,
        "nota": " ".join(notas) or None,
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
    t = sem_html(r.text)
    i = normalizar(t).find(f"em {ano}")
    _status(True, "ok", se=reg["se"], casos=reg["casos"], ufs=reg["ufs"], obitos=reg["obitos"],
            trecho=t[i:i + 1500] if i >= 0 else "")
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
