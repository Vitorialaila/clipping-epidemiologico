"""Leitura de relatórios públicos do Power BI ("Publicar na Web"), como o Painel de Exantemáticas do MS.

Um relatório publicado tem um link app.powerbi.com/view?r=<base64>, cujo conteúdo traz a chave do recurso
(k) e o locatário (t). Os dados são lidos pela mesma interface que o visualizador do Power BI usa para
desenhar os gráficos: descobre-se o cluster do locatário, baixa-se a estrutura do relatório (páginas,
visuais e as consultas de cada visual) e executa-se a consulta desejada no modelo.
"""
from __future__ import annotations

import base64
import json
import re

from .util import log, sessao_http


def _chaves(url: str) -> tuple[str, str]:
    r = re.search(r"[?&]r=([^&#]+)", url).group(1)
    r += "=" * (-len(r) % 4)
    d = json.loads(base64.b64decode(r))
    return d["k"], d["t"]


class RelatorioPublico:
    def __init__(self, url: str, timeout: int = 60):
        self.chave, self.locatario = _chaves(url)
        self.url = url
        self.timeout = timeout
        self.s = sessao_http()
        self.s.headers.update({"X-PowerBI-ResourceKey": self.chave, "Content-Type": "application/json;charset=UTF-8",
                               "Origin": "https://app.powerbi.com", "Referer": "https://app.powerbi.com/"})
        self.api = self._cluster()
        self._estrutura = None

    def _cluster(self) -> str:
        """Endereço da API do cluster onde o relatório está hospedado."""
        try:
            r = self.s.get(f"https://api.powerbi.com/public/routing/cluster/{self.locatario}", timeout=self.timeout)
            r.raise_for_status()
            uri = r.json()["FixedClusterUri"]
        except Exception:  # noqa: BLE001  (alternativa: o próprio HTML do visualizador)
            html = self.s.get(self.url, timeout=self.timeout).text
            uri = re.search(r"resolvedClusterUri\s*=\s*'([^']+)'", html).group(1)
        uri = uri.rstrip("/")
        return re.sub(r"-redirect(\.|$)", r"-api\1", uri).replace("://wabi-", "://wabi-", 1)

    def estrutura(self) -> dict:
        if self._estrutura is None:
            r = self.s.get(f"{self.api}/public/reports/{self.chave}/modelsAndExploration?preferReadOnlySession=true",
                           timeout=self.timeout)
            r.raise_for_status()
            self._estrutura = r.json()
        return self._estrutura

    @property
    def modelo(self) -> int:
        return self.estrutura()["models"][0]["id"]

    def esquema(self) -> dict:
        r = self.s.post(f"{self.api}/public/reports/conceptualschema",
                        json={"modelIds": [self.modelo], "userPreferredLocale": "pt-BR"}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def visuais(self) -> list[dict]:
        """Páginas e visuais do relatório, com o título e a consulta (prototypeQuery) de cada um."""
        out = []
        for sec in self.estrutura()["exploration"]["sections"]:
            for vc in sec.get("visualContainers", []):
                try:
                    cfg = json.loads(vc.get("config", "{}"))
                except ValueError:
                    continue
                sv = cfg.get("singleVisual") or {}
                titulo = ""
                try:
                    titulo = sv["vcObjects"]["title"][0]["properties"]["text"]["expr"]["Literal"]["Value"].strip("'")
                except (KeyError, IndexError, TypeError):
                    pass
                filtros = vc.get("filters")
                out.append({"pagina": sec.get("displayName"), "tipo": sv.get("visualType"), "titulo": titulo,
                            "consulta": sv.get("prototypeQuery"),
                            "filtros_visual": json.loads(filtros) if isinstance(filtros, str) and filtros else None,
                            "filtros_pagina": json.loads(sec["filters"]) if sec.get("filters") else None})
        return out

    def consultar(self, consulta: dict, top: int = 5000) -> dict:
        """Executa uma consulta semântica (formato prototypeQuery) e devolve a resposta bruta."""
        selecao = list(range(len(consulta.get("Select", []))))
        corpo = {"version": "1.0.0", "modelId": self.modelo, "cancelQueries": [], "queries": [{
            "Query": {"Commands": [{"SemanticQueryDataShapeCommand": {
                "Query": consulta,
                "Binding": {"Primary": {"Groupings": [{"Projections": selecao}]},
                            "DataReduction": {"DataVolume": 4, "Primary": {"Top": {"Count": top}}}, "Version": 1},
                "ExecutionMetricsKind": 1}}]},
            "QueryId": ""}]}
        r = self.s.post(f"{self.api}/public/reports/querydata?synchronous=true", json=corpo, timeout=self.timeout)
        r.raise_for_status()
        return r.json()


def linhas(resposta: dict) -> list[list]:
    """Converte a resposta compactada do Power BI (DSR) em linhas simples.

    Cada linha traz os valores na ordem das colunas do Select. O formato comprime valores repetidos
    ("R": bitmask de colunas repetidas em relação à linha anterior), nulos ("Ø") e dicionários de
    valores ("ValueDicts")."""
    ds = resposta["results"][0]["result"]["data"]["dsr"]["DS"][0]
    dicts = ds.get("ValueDicts", {})
    ph = ds["PH"][0]
    chave = next(k for k in ph if k.startswith("DM"))
    out, anterior, colunas = [], [], None
    for linha in ph[chave]:
        if "S" in linha:
            colunas = linha["S"]
        valores = list(linha.get("C", []))
        rep, nul = linha.get("R", 0), linha.get("Ø", 0)
        atual = []
        for i, col in enumerate(colunas):
            if rep >> i & 1:
                atual.append(anterior[i] if i < len(anterior) else None)
            elif nul >> i & 1:
                atual.append(None)
            else:
                v = valores.pop(0) if valores else None
                if "DN" in col and isinstance(v, int):
                    v = dicts[col["DN"]][v]
                atual.append(v)
        out.append(atual)
        anterior = atual
    return out


def reconhecer(url: str) -> dict:
    """Estrutura resumida do relatório, para escolher a consulta certa: páginas, visuais e tabelas do modelo."""
    rel = RelatorioPublico(url)
    info = {"api": rel.api, "modelo": rel.modelo, "visuais": []}
    try:
        esq = rel.esquema()
        info["tabelas"] = {e["Name"]: [p["Name"] for p in e.get("Properties", [])]
                           for s in esq.get("schemas", []) for e in s.get("schema", {}).get("Entities", [])}
    except Exception as e:  # noqa: BLE001
        info["tabelas_erro"] = str(e)[:300]
    for v in rel.visuais():
        item = {k: v[k] for k in ("pagina", "tipo", "titulo")}
        item["consulta"] = v["consulta"]
        item["filtros_visual"] = v["filtros_visual"]
        if v["consulta"] and v["tipo"] not in ("slicer", "textbox", "image", "shape", "actionButton"):
            try:
                item["amostra"] = linhas(rel.consultar(v["consulta"], top=40))[:40]
            except Exception as e:  # noqa: BLE001
                item["amostra_erro"] = str(e)[:300]
        info["visuais"].append(item)
    paginas = {}
    for v in rel.visuais():
        paginas.setdefault(v["pagina"], v["filtros_pagina"])
    info["paginas"] = paginas
    log.info("Power BI: %d visuais reconhecidos.", len(info["visuais"]))
    return info
