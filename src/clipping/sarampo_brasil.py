"""Sarampo no Brasil: captura dos números oficiais do Ministério da Saúde.

Fonte principal: Painel de Exantemáticas do CNIE/MS (Power BI público), filtrado para sarampo.
Fonte complementar: página "Situação Epidemiológica do Sarampo" (gov.br/saude), que traz o total
de casos confirmados no ano até uma semana epidemiológica e a distribuição por UF.
Fonte manual: config/sarampo_brasil.yaml (informes, notas e correções).

Cada captura válida vira um registro no histórico (data/paineis/historico/sarampo_brasil.json),
o que forma a curva acumulada ao longo das semanas. Uma captura só é aceita se a soma das UFs
bater com o total; caso contrário, nada é gravado e o painel segue com o último registro válido.
"""
from __future__ import annotations

import json
import re
from datetime import date

from epiweeks import Week, Year

from .mapas import _ufs
from .util import DADOS, carregar_yaml, ler_json, log, normalizar, salvar_json, sem_html, sessao_http

URL_MS = "https://www.gov.br/saude/pt-br/assuntos/saude-de-a-a-z/s/sarampo/situacao-epidemiologica"
FONTE_MS = "Ministério da Saúde, Situação Epidemiológica do Sarampo"
ARQ_HISTORICO = DADOS / "paineis" / "historico" / "sarampo_brasil.json"
ARQ_STATUS = DADOS / "paineis" / "historico" / "sarampo_brasil_status.json"
# Painel de Exantemáticas do CNIE/MS (Power BI público), linkado na página de situação do sarampo
URL_PAINEL = ("https://app.powerbi.com/view?r=eyJrIjoiNjYxM2M1MjItY2M0Ny00YzJiLThmYzktMGM2MzAwMmE2NGI5IiwidCI6IjlhNTU0"
              "YWQzLWI1MmItNDg2Mi1hMzZmLTg0ZDg5MWU1YzcwNSJ9")
URL_PAINEL_PAGINA = "https://www.gov.br/saude/pt-br/composicao/svsa/cnie/painel-exantematicas"
FONTE_PAINEL = "Ministério da Saúde, Painel de Exantemáticas (CNIE)"
ARQ_STATUS_PAINEL = DADOS / "paineis" / "historico" / "sarampo_painel_status.json"

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


def _data_painel(txt) -> date | None:
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", str(txt or ""))
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def capturar_painel(ano: int) -> dict | None:
    """Lê o Painel de Exantemáticas do MS (Power BI público), só sarampo: casos confirmados no ano
    (agravo final = sarampo, classificação final = confirmado), por UF de residência e por semana de
    início do exantema, e os casos suspeitos de sarampo em investigação.

    A captura só é aceita se a soma por UF (incluindo os casos sem UF informada) bater com o total e se o
    total não cair mais de 10% em relação à captura anterior do painel (reclassificações pequenas são normais)."""
    from datetime import datetime
    from . import powerbi as pb

    def status(ok, motivo, **extra):
        salvar_json(ARQ_STATUS_PAINEL, {"quando": datetime.now().isoformat(timespec="minutes"), "ok": ok,
                                        "motivo": motivo, "url": URL_PAINEL_PAGINA, **extra})
    try:
        rel = pb.RelatorioPublico(URL_PAINEL)
        E = {"t": "tb_exantemáticas"}
        ano_f = [pb.filtro_em("t", "NU_ANO", [ano])]
        conf = ano_f + [pb.filtro_em("t", "AGRAVO", ["Sarampo"]),
                        pb.filtro_em("t", "Classificacao_Final_Grafico", ["Confirmado"])]
        q = lambda cols, where: pb.linhas(rel.consultar(pb.consulta(
            E, [pb.coluna("t", c) for c in cols] + [pb.contagem("t", "ID")], where)))
        # O total sai da contagem por classificação (consulta independente da contagem por UF, para conferir a soma)
        por_classif = {r[0]: r[1] for r in q(["Classificacao_Final_Grafico"], ano_f + [pb.filtro_em("t", "AGRAVO", ["Sarampo"])])}
        total = por_classif.get("Confirmado") or 0
        por_uf = {r[0]: r[1] for r in q(["UF_RESIDENCIA"], conf)}
        por_se = {int(r[0]): r[1] for r in q(["semana_epi.SE"], conf) if r[0] is not None}
        investigacao = {r[0]: r[1] for r in q(["Classificacao_Final_Grafico"], ano_f + [
            pb.filtro_em("t", "CS_SUSPEIT", ["Sarampo"])])}.get("Em investigação")
        datas = pb.linhas(rel.consultar(pb.consulta({"m": "medidas"}, [pb.medida("m", "Dados Ate"),
                                                                        pb.medida("m", "Ultima Atualizacao")])))[0]
    except Exception as e:  # noqa: BLE001  (painel fora do ar ou mudou de estrutura)
        log.warning("Sarampo/painel do MS: leitura falhou (%s).", e)
        status(False, f"leitura falhou: {str(e)[:300]}")
        return None
    dados_ate, atualizado = _data_painel(datas[0]), _data_painel(datas[1])
    ignorada = sum(n for u, n in por_uf.items() if not re.fullmatch(r"[A-Z]{2}", str(u or "")))
    ufs = {u: n for u, n in por_uf.items() if re.fullmatch(r"[A-Z]{2}", str(u or ""))}
    historico = ler_json(ARQ_HISTORICO, [])
    anteriores = [h for h in historico if h.get("captura") == "painel" and h["data_referencia"].startswith(str(ano))]
    if not dados_ate or not total:
        status(False, "painel sem data de corte ou sem casos", total=total, datas=datas)
        return None
    if sum(ufs.values()) + ignorada != total:
        status(False, "soma por UF não bate com o total", total=total, por_uf=por_uf)
        return None
    if anteriores and total < 0.9 * max(h["casos"] for h in anteriores):
        status(False, "total caiu mais de 10% em relação à captura anterior", total=total)
        return None
    reg = {"data_referencia": dados_ate.isoformat(), "se": Week.fromdate(dados_ate).week, "casos": total,
           "obitos": None, "ufs": dict(sorted(ufs.items(), key=lambda kv: -kv[1])), "uf_ignorada": ignorada,
           "curva_se": {str(k): v for k, v in sorted(por_se.items())}, "em_investigacao": investigacao,
           "painel_atualizado_em": atualizado.isoformat() if atualizado else None,
           "fonte": FONTE_PAINEL, "url": URL_PAINEL_PAGINA, "captura": "painel",
           "capturado_em": date.today().isoformat()}
    # Mesma data de corte: a leitura mais recente substitui (o painel reclassifica casos)
    historico = [h for h in historico if not (h.get("captura") == "painel" and h["data_referencia"] == reg["data_referencia"])]
    historico.append(reg)
    salvar_json(ARQ_HISTORICO, sorted(historico, key=lambda h: h["data_referencia"]))
    status(True, "ok", dados_ate=reg["data_referencia"], casos=total, ufs=por_uf, em_investigacao=investigacao,
           atualizado=reg["painel_atualizado_em"])
    log.info("Sarampo/painel do MS: %d casos confirmados até %s.", total, reg["data_referencia"])
    return reg


def registros(capturar_agora: bool = True) -> tuple[int, list[dict]]:
    """Registros oficiais (manuais + capturados), do mais antigo ao mais recente.

    Quando há dois registros com a mesma data de referência, vale o manual (correção)."""
    cfg = carregar_yaml("sarampo_brasil.yaml")
    ano = int(cfg.get("ano", date.today().year))
    if capturar_agora:
        capturar(ano)
        capturar_painel(ano)
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
