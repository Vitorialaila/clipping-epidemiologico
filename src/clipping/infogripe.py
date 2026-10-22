"""SRAG: dados do InfoGripe (Fiocruz/MAVE e GT-Influenza do Ministério da Saúde).

Os arquivos são publicados no repositório público do InfoGripe no GitHub
(github.com/infogripe/boletim_infogripe, pasta Dados/InfoGripe), atualizado a cada boletim.
Usa o recorte "sem filtro de febre" (definição de caso de SRAG do Ministério da Saúde).
"""
from __future__ import annotations

import csv
import io

from epiweeks import Week

from .util import log, sessao_http

BASE = "https://raw.githubusercontent.com/infogripe/boletim_infogripe/main/Dados/InfoGripe/"
ARQ_TENDENCIA = "estados_e_pais_serie_estimativas_tendencia_sem_filtro_febre.csv"
ARQ_VIRUS = "estados_e_pais_serie_estimativas_virus_sem_filtro_febre.csv"
ARQ_INTENSIDADE = "estados_intensidade_sem_filtro_febre.csv"
URL_REPO = "https://github.com/infogripe/boletim_infogripe"
URL_NOTA_TENDENCIA = URL_REPO + "/blob/main/Boletins%20do%20InfoGripe/Nota_tecnica_indicadores_de_tendencia.pdf"

# Nota técnica do InfoGripe (indicadores de tendência): 5 categorias pela probabilidade de a
# declividade ser positiva ou negativa nas últimas 6 (longo prazo) ou 3 (curto prazo) semanas.
TENDENCIA = {
    1.0: ("Aumento", "probabilidade alta de crescimento (≥ 95%)"),
    0.5: ("Aumento", "probabilidade leve de crescimento (75% a 95%)"),
    0.0: ("Estável", "estabilidade ou oscilação"),
    -0.5: ("Queda", "probabilidade leve de queda (75% a 95%)"),
    -1.0: ("Queda", "probabilidade alta de queda (≥ 95%)"),
}
NIVEIS = ["Segurança", "Baixo risco", "Alerta", "Risco", "Alto risco"]


def _num(v: str):
    v = (v or "").strip().strip('"')
    if v in ("", "NA", "NaN"):
        return None
    return float(v.replace(",", "."))


def _baixar(nome: str) -> list[dict]:
    r = sessao_http().get(BASE + nome, timeout=90)
    r.raise_for_status()
    texto = r.content.decode("utf-8-sig")
    if texto.lstrip().startswith("<"):
        raise ValueError(f"{nome}: resposta não é CSV")
    return list(csv.DictReader(io.StringIO(texto), delimiter=";"))


def _semana(ano: int, se: int) -> dict:
    w = Week(ano, se)
    return {"ano": ano, "se": se, "inicio": w.startdate().isoformat(), "fim": w.enddate().isoformat()}


def serie(linhas: list[dict], uf: str, semanas: int) -> list[dict]:
    """Série semanal de casos de SRAG (notificados e estimados, com IC 95%) de uma UF ou do Brasil (BR)."""
    sel = [l for l in linhas if l["DS_UF_SIGLA"] == uf and l["escala"] == "casos"]
    sel.sort(key=lambda l: (int(l["Ano epidemiológico"]), int(l["Semana epidemiológica"])))
    out = []
    for l in sel[-semanas:]:
        d = _semana(int(l["Ano epidemiológico"]), int(l["Semana epidemiológica"]))
        d.update(notificados=_num(l["Casos semanais reportados até a última atualização"]),
                 estimados=_num(l["casos estimados"]), ic95i=_num(l["IC95I"]), ic95s=_num(l["IC95S"]),
                 tend_lp=_num(l["tendência de longo prazo"]), tend_cp=_num(l["tendência de curto prazo"]))
        out.append(d)
    return out


def serie_virus(linhas: list[dict], uf: str, semanas: int) -> dict[str, list[dict]]:
    sel = [l for l in linhas if l["DS_UF_SIGLA"] == uf]
    chaves = sorted({(int(l["Ano.epidemiológico"]), int(l["Semana.epidemiológica"])) for l in sel})[-semanas:]
    validas = set(chaves)
    out: dict[str, list[dict]] = {}
    for l in sel:
        k = (int(l["Ano.epidemiológico"]), int(l["Semana.epidemiológica"]))
        if k in validas:
            d = _semana(*k)
            d.update(estimados=_num(l["casos.estimados"]), li=_num(l["LI"]), ls=_num(l["LS"]))
            out.setdefault(l["virus"], []).append(d)
    for v in out.values():
        v.sort(key=lambda d: (d["ano"], d["se"]))
    return out


def coletar(semanas: int = 52) -> dict | None:
    """Baixa os arquivos do InfoGripe e devolve os recortes usados no painel (Brasil e DF)."""
    try:
        tend = _baixar(ARQ_TENDENCIA)
        virus = _baixar(ARQ_VIRUS)
        inten = _baixar(ARQ_INTENSIDADE)
    except Exception as e:
        log.warning("InfoGripe: dados indisponíveis (%s).", e)
        return None
    br, df = serie(tend, "BR", semanas), serie(tend, "DF", semanas)
    if not br or br[-1]["estimados"] is None:
        log.warning("InfoGripe: série do Brasil vazia ou sem estimativa.")
        return None
    niveis = {l["DS_UF_SIGLA"]: {"nivel": l["intensidade"], "ano": int(l["ano"]), "se": int(l["semana_epi"])}
              for l in inten if l.get("intensidade")}
    return {"br": br, "df": df, "virus_br": serie_virus(virus, "BR", 26), "niveis": niveis}
