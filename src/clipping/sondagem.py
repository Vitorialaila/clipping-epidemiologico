"""Sondagem temporária das fontes dos painéis (ECDC e InfoGripe), executada no GitHub Actions.

Grava em data/paineis/historico/sondagem.json o que as fontes devolvem, para escrever os leitores
sobre o formato real. Remover depois que os painéis estiverem prontos.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from .util import DADOS, salvar_json, sem_html, sessao_http

URL_ECDC = "https://www.ecdc.europa.eu/en/ebola-outbreak-democratic-republic-congo-and-uganda"
GITLAB = "https://gitlab.fiocruz.br"
PROJETOS = ["marcelo.gomes/infogripe", "mave/repo"]


def sondar() -> None:
    s = sessao_http()
    out = {}
    try:
        r = s.get(URL_ECDC, timeout=40)
        sopa = BeautifulSoup(r.text, "html.parser")
        tabelas = []
        for t in sopa.find_all("table"):
            linhas = [[c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])] for tr in t.find_all("tr")]
            tabelas.append(linhas)
        texto = sem_html(r.text)
        i = texto.find("Since the")
        out["ecdc"] = {"http": r.status_code, "tabelas": tabelas,
                       "texto_inicio": texto[texto.find("Ebola"):][:200],
                       "trecho": texto[max(0, i - 1500):i + 2500] if i >= 0 else texto[:4000],
                       "links_csv": [a.get("href") for a in sopa.find_all("a") if a.get("href", "").lower().endswith((".csv", ".xlsx"))]}
    except Exception as e:
        out["ecdc"] = {"erro": str(e)[:300]}

    out["infogripe"] = {}
    for proj in PROJETOS:
        pid = proj.replace("/", "%2F")
        for caminho in ("Dados/InfoGripe", "Dados"):
            url = f"{GITLAB}/api/v4/projects/{pid}/repository/tree?path={caminho}&per_page=100"
            try:
                r = s.get(url, timeout=40)
                corpo = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:300]
                if isinstance(corpo, list):
                    corpo = [x.get("name") for x in corpo]
                out["infogripe"][f"{proj}:{caminho}"] = {"http": r.status_code, "itens": corpo}
            except Exception as e:
                out["infogripe"][f"{proj}:{caminho}"] = {"erro": str(e)[:300]}
    # Cabeçalho dos arquivos mais prováveis (primeiras linhas)
    candidatos = [
        "marcelo.gomes/infogripe/-/raw/master/Dados/InfoGripe/serie_temporal_com_estimativas_recentes_sem_filtro_febre.csv",
        "marcelo.gomes/infogripe/-/raw/master/Dados/InfoGripe/casos_semanais_fx_etaria_virus_sem_filtro_febre.csv",
        "marcelo.gomes/infogripe/-/raw/master/Dados/InfoGripe/estados_e_pais_serie_estimativas_tendencia_sem_filtro_febre.csv",
        "marcelo.gomes/infogripe/-/raw/master/Dados/InfoGripe/valores_esperados_por_localidade.csv",
    ]
    out["infogripe_arquivos"] = {}
    for c in candidatos:
        try:
            r = s.get(f"{GITLAB}/{c}", timeout=60, stream=True)
            linhas = []
            if r.status_code == 200:
                for n, linha in enumerate(r.iter_lines(decode_unicode=True)):
                    linhas.append(linha[:400])
                    if n >= 4:
                        break
            out["infogripe_arquivos"][c] = {"http": r.status_code, "tamanho": r.headers.get("content-length"),
                                            "inicio": linhas}
            r.close()
        except Exception as e:
            out["infogripe_arquivos"][c] = {"erro": str(e)[:300]}
    salvar_json(DADOS / "paineis" / "historico" / "sondagem.json", out)
