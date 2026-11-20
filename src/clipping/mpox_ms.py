"""Mpox no Brasil: Painel de Mpox do CNIE/Ministério da Saúde (Power BI público, dados do e-SUS Sinan).

Lê, para o ano corrente, os casos por classificação final (confirmado, provável, suspeito), por UF de
residência e por semana epidemiológica. Cada leitura válida vira um registro em
data/paineis/historico/mpox_ms.json. A leitura só é aceita se a soma por UF bater com o total e se o
total não cair mais de 10% em relação à leitura anterior; caso contrário, o painel do site segue com o
último registro válido e o motivo fica em mpox_ms_status.json.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from epiweeks import Week

from . import powerbi as pb
from .util import DADOS, ler_json, log, salvar_json

URL_PAINEL = ("https://app.powerbi.com/view?r=eyJrIjoiYTU1Zjg4OGUtNjA1ZC00NTBkLTg1NzAtZjljOTNmMmNkM2ZiIiwidCI6IjlhNTU0YWQz"
              "LWI1MmItNDg2Mi1hMzZmLTg0ZDg5MWU1YzcwNSJ9")
URL_PAGINA = "https://www.gov.br/saude/pt-br/composicao/svsa/cnie/painel-mpox"
FONTE = "Ministério da Saúde, Painel de Mpox (CNIE)"
ARQ_HISTORICO = DADOS / "paineis" / "historico" / "mpox_ms.json"
ARQ_STATUS = DADOS / "paineis" / "historico" / "mpox_ms_status.json"
CLASSES = ["Confirmado", "Provável", "Suspeito"]


def _status(ok: bool, motivo: str, **extra) -> None:
    salvar_json(ARQ_STATUS, {"quando": datetime.now().isoformat(timespec="minutes"), "ok": ok, "motivo": motivo,
                             "url": URL_PAGINA, **extra})


def _data(v) -> date | None:
    if isinstance(v, (int, float)):
        return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=v)).date()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(v or ""))
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", str(v or ""))
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def capturar(ano: int) -> dict | None:
    ent = {"t": "MPX_TRATADO", "c": "CLASSI_FIN_AJ", "i": "IBGE", "m": "medidas"}
    ano_f = [pb.filtro_em("t", "ANO", [ano])]
    cont = pb.medida("m", "contagem_casos")
    try:
        rel = pb.RelatorioPublico(URL_PAINEL)
        q = lambda sel, where=ano_f: pb.linhas(rel.consultar(pb.consulta(ent, sel, where), top=5000))
        por_classe = {r[0]: r[1] or 0 for r in q([pb.coluna("c", "Nome_CLASSI_FIN"), cont])}
        uf = q([pb.coluna("c", "Nome_CLASSI_FIN"), pb.coluna("i", "UF_SIGLA"), cont],
               ano_f + [pb.filtro_em("c", "Nome_CLASSI_FIN", CLASSES)])
        se = q([pb.coluna("c", "Nome_CLASSI_FIN"), pb.coluna("t", "SEMANA"), cont],
               ano_f + [pb.filtro_em("c", "Nome_CLASSI_FIN", CLASSES)])
        datas = q([{"Aggregation": {"Expression": {"Column": {"Expression": {"SourceRef": {"Source": "t"}},
                                                               "Property": "DT_NOTIFIC"}}, "Function": f},
                    "Name": n} for f, n in ((3, "min"), (4, "max"))])
        # Conferência do significado do campo SEMANA: datas de notificação de algumas semanas
        conf_semana = q([pb.coluna("t", "SEMANA"),
                         {"Aggregation": {"Expression": {"Column": {"Expression": {"SourceRef": {"Source": "t"}},
                                                                    "Property": "DT_NOTIFIC"}}, "Function": 3}, "Name": "min"},
                         {"Aggregation": {"Expression": {"Column": {"Expression": {"SourceRef": {"Source": "t"}},
                                                                    "Property": "DT_NOTIFIC"}}, "Function": 4}, "Name": "max"}])
    except Exception as e:  # noqa: BLE001  (painel fora do ar ou mudou de estrutura)
        log.warning("Mpox/painel do MS: leitura falhou (%s).", e)
        _status(False, f"leitura falhou: {str(e)[:300]}")
        return None

    dados_ate = _data(datas[0][1]) if datas and len(datas[0]) > 1 else None
    tot = {c: por_classe.get(c, 0) for c in CLASSES}
    ufs = {c: {} for c in CLASSES}
    sem_uf = {c: 0 for c in CLASSES}
    for classe, sigla, n in uf:
        if classe not in ufs or not n:
            continue
        if sigla and re.fullmatch(r"[A-Z]{2}", sigla):
            ufs[classe][sigla] = ufs[classe].get(sigla, 0) + n
        else:
            sem_uf[classe] += n
    curva = {c: {} for c in CLASSES}
    for classe, semana, n in se:
        if classe in curva and semana is not None and n:
            curva[classe][str(int(semana))] = n
    diag = {"por_classe": por_classe, "datas": datas,
            "semana_vs_notificacao": [[s, str(_data(a)), str(_data(b))] for s, a, b in conf_semana][-8:]}
    if not dados_ate or not tot["Confirmado"]:
        _status(False, "painel sem data de notificação ou sem casos confirmados", **diag)
        return None
    for c in ("Confirmado", "Provável"):
        if sum(ufs[c].values()) + sem_uf[c] != tot[c]:
            _status(False, f"soma por UF não bate com o total ({c})", ufs=ufs, sem_uf=sem_uf, **diag)
            return None
    historico = ler_json(ARQ_HISTORICO, [])
    anteriores = [h for h in historico if h["data_referencia"].startswith(str(ano))]
    if anteriores and tot["Confirmado"] < 0.9 * max(h["confirmados"] for h in anteriores):
        _status(False, "total de confirmados caiu mais de 10% em relação à leitura anterior", **diag)
        return None
    reg = {"data_referencia": dados_ate.isoformat(), "se": Week.fromdate(dados_ate).week, "ano": ano,
           "confirmados": tot["Confirmado"], "provaveis": tot["Provável"], "suspeitos": tot["Suspeito"],
           "ufs": ufs, "sem_uf": sem_uf, "curva_se": curva, "fonte": FONTE, "url": URL_PAGINA,
           "capturado_em": date.today().isoformat()}
    historico = [h for h in historico if h["data_referencia"] != reg["data_referencia"]] + [reg]
    salvar_json(ARQ_HISTORICO, sorted(historico, key=lambda h: h["data_referencia"]))
    _status(True, "ok", confirmados=reg["confirmados"], provaveis=reg["provaveis"], suspeitos=reg["suspeitos"],
            dados_ate=reg["data_referencia"], **diag)
    log.info("Mpox/painel do MS: %d confirmados e %d prováveis até %s.", reg["confirmados"], reg["provaveis"],
             reg["data_referencia"])
    return reg


def ultimo(ano: int | None = None, capturar_agora: bool = True) -> dict | None:
    ano = ano or date.today().year
    if capturar_agora:
        capturar(ano)
    regs = [h for h in ler_json(ARQ_HISTORICO, []) if h["data_referencia"].startswith(str(ano))]
    return regs[-1] if regs else None
