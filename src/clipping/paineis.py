"""Painéis de vigilância com gráficos interativos e dados reais de fontes fidedignas."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Any

import requests
import plotly.graph_objects as go
from .util import log, ler_json, salvar_json

PAINEIS_DIR = Path(__file__).parent.parent.parent / "data" / "paineis"
PAINEIS_DIR.mkdir(parents=True, exist_ok=True)


class PainelVigilancia:
    """Base para painéis de vigilância com dados reais."""

    def __init__(self, nome: str, sigla: str, descricao: str = ""):
        self.nome = nome
        self.sigla = sigla
        self.descricao = descricao
        self.dados = {
            "nome": nome,
            "sigla": sigla,
            "descricao": descricao,
            "atualizado_em": None,
            "fonte_dados": "",
            "serie_temporal": [],
            "distribuicao_geografica": [],
            "indicadores": {},
            "grafico_html": "",
        }

    def coletar(self) -> bool:
        """Coleta dados. Retorna True se bem-sucedido."""
        raise NotImplementedError

    def gerar_grafico_serie(self) -> dict:
        """Gera dados do gráfico da série temporal em formato JSON para Plotly."""
        if not self.dados.get("serie_temporal"):
            return {}

        try:
            serie = self.dados["serie_temporal"]

            # Preparar dados
            if "semana" in serie[0]:
                x = [d.get("semana", i) for i, d in enumerate(serie)]
                titulo_x = "Semana Epidemiológica"
            elif "período" in serie[0]:
                x = [d.get("período", "") for d in serie]
                titulo_x = "Período"
            elif "data" in serie[0]:
                x = [d.get("data", "") for d in serie]
                titulo_x = "Data"
            else:
                return {}

            # Gráfico de série temporal
            fig = go.Figure()

            # Casos (uma linha por chave casos*, ex.: casos_rdc, casos_uganda)
            cores = ["#0A3570", "#B8921A", "#2F7F7A"]
            chaves_casos = [k for k in serie[0] if k.startswith("casos")]
            for i, chave in enumerate(chaves_casos):
                casos = [d.get(chave, 0) for d in serie]
                rotulo = "Casos" if chave == "casos" else "Casos " + {"americas": "Américas", "rdc": "RDC", "uganda": "Uganda"}.get(chave.split("_", 1)[1], chave.split("_", 1)[1])
                fig.add_trace(go.Scatter(
                    x=x, y=casos,
                    name=rotulo,
                    mode="lines+markers",
                    line=dict(color=cores[i % len(cores)], width=3),
                    marker=dict(size=8),
                    hovertemplate="<b>%{x}</b><br>" + rotulo + ": %{y}<extra></extra>"
                ))

            # Óbitos
            if "obitos" in serie[0] or "óbitos" in serie[0]:
                obitos_key = "obitos" if "obitos" in serie[0] else "óbitos"
                obitos = [d.get(obitos_key, 0) for d in serie]
                fig.add_trace(go.Scatter(
                    x=x, y=obitos,
                    name="Óbitos",
                    mode="lines+markers",
                    line=dict(color="#A3262A", width=3),
                    marker=dict(size=8),
                    hovertemplate="<b>%{x}</b><br>Óbitos: %{y}<extra></extra>"
                ))

            fig.update_layout(
                title=dict(text=f"<b>{self.nome} - Série Temporal</b>", font=dict(size=16)),
                xaxis_title=titulo_x,
                yaxis_title="Casos/Óbitos",
                hovermode="x unified",
                template="plotly_white",
                height=400,
                font=dict(family="Inter, system-ui, sans-serif", size=12),
                plot_bgcolor="rgba(245,245,245,1)",
                margin=dict(l=50, r=50, t=70, b=50)
            )

            if titulo_x == "Data":
                fig.update_xaxes(tickformat="%d/%m/%Y", hoverformat="%d/%m/%Y")

            # Retornar dados em formato serializável
            fig_dict = fig.to_dict()
            return {
                "data": fig_dict.get("data", []),
                "layout": fig_dict.get("layout", {}),
                "div_id": f"grafico_{self.sigla.lower()}"
            }
        except Exception as e:
            log.warning(f"Erro ao gerar gráfico de série: {e}")
            return {}

    def gerar_grafico_distribuicao(self) -> dict:
        """Gera dados do gráfico de distribuição geográfica em formato JSON para Plotly."""
        if not self.dados.get("distribuicao_geografica"):
            return {}

        try:
            dist = self.dados["distribuicao_geografica"]

            # Extrair dados
            locais = [d.get("país") or d.get("estado") for d in dist]
            casos = [d.get("casos", 0) for d in dist]

            fig = go.Figure(data=[
                go.Bar(
                    x=locais,
                    y=casos,
                    marker=dict(color="#0A3570"),
                    text=casos,
                    textposition="auto",
                    hovertemplate="<b>%{x}</b><br>Casos: %{y}<extra></extra>"
                )
            ])

            fig.update_layout(
                title=dict(text=f"<b>{self.nome} - Distribuição Geográfica</b>", font=dict(size=16)),
                xaxis_title="Localidade",
                yaxis_title="Casos",
                template="plotly_white",
                height=350,
                showlegend=False,
                font=dict(family="Inter, system-ui, sans-serif", size=12),
                plot_bgcolor="rgba(245,245,245,1)",
                margin=dict(l=50, r=50, t=70, b=100)
            )

            fig.update_xaxes(tickangle=-45)

            # Retornar dados em formato serializável
            fig_dict = fig.to_dict()
            return {
                "data": fig_dict.get("data", []),
                "layout": fig_dict.get("layout", {}),
                "div_id": f"dist_{self.sigla.lower()}"
            }
        except Exception as e:
            log.warning(f"Erro ao gerar gráfico de distribuição: {e}")
            return {}

    def salvar(self) -> None:
        """Salva o painel em JSON com dados dos gráficos."""
        self.dados["atualizado_em"] = datetime.now().isoformat()

        # Gerar dados dos gráficos (JSON-serializáveis), na ordem de exibição
        if "graficos" not in self.dados:
            self.dados["graficos"] = [g for g in (self.gerar_grafico_serie(), self.gerar_grafico_distribuicao()) if g]

        arq = PAINEIS_DIR / f"{self.sigla.lower()}.json"
        salvar_json(arq, self.dados)
        log.info(f"Painel {self.nome} salvo com gráficos")


# Cores do site
NAVY, OURO, CINZA_UF, CINZA_TXT = "#0A3570", "#B8921A", "#E3E6EB", "#6A7282"
CLASSES_MAPA = [  # (mínimo, rótulo, cor)
    (0, "Sem casos", CINZA_UF),
    (1, "1 a 4", "#B9C9E0"),
    (5, "5 a 19", "#6F8FBF"),
    (20, "20 a 99", "#2B5A97"),
    (100, "100 ou mais", NAVY),
]
LAYOUT_BASE = dict(
    template="plotly_white",
    font=dict(family="Inter, system-ui, sans-serif", size=12, color="#3A4352"),
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    hoverlabel=dict(font=dict(family="Inter, system-ui, sans-serif")),
)


def _data_br(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def _area(anel) -> float:
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(anel, anel[1:] + anel[:1])) / 2



def ufs_brasil() -> dict:
    from .mapas import _ufs
    return _ufs()


def mapa_ufs(classes: dict, ordem: list[str], titulo: str, div_id: str, rotulos: dict | None = None) -> dict:
    """Mapa do Brasil por UF (malha IBGE local, desenhada como polígonos no Plotly).

    classes: {sigla: (rótulo da classe, cor, texto do hover)}; ordem: ordem das classes na legenda."""
    import math
    kx = math.cos(math.radians(-14))
    fig = go.Figure()
    vistas = set()
    for sigla, p in ufs_brasil().items():
        nome_classe, cor, texto = classes.get(sigla, ("Sem dado", CINZA_UF, "sem dado"))
        xs, ys = [], []
        for anel in p["p"]:
            if abs(_area(anel)) < 0.02 and len(p["p"]) > 1:
                continue  # ilhotas: não pesam no desenho
            for lon, lat in anel + anel[:1]:
                xs.append(round(lon * kx, 3)); ys.append(round(lat, 3))
            xs.append(None); ys.append(None)
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines", fill="toself", fillcolor=cor, line=dict(color="#FFFFFF", width=0.8),
            hoveron="fills", hoverinfo="text", text=f"<b>{p['n']}</b><br>{texto}",
            name=nome_classe, legendgroup=nome_classe, showlegend=nome_classe not in vistas,
            legendrank=ordem.index(nome_classe) if nome_classe in ordem else 99))
        vistas.add(nome_classe)
    for sigla, txt in (rotulos or {}).items():
        if sigla in ufs_brasil():
            lon, lat = ufs_brasil()[sigla]["c"]
            fig.add_annotation(x=lon * kx, y=lat, text=txt, showarrow=False, font=dict(size=11, color=NAVY),
                               bgcolor="rgba(255,255,255,.85)", bordercolor=NAVY, borderwidth=1, borderpad=2)
    fig.update_layout(
        **LAYOUT_BASE, title=dict(text=titulo, font=dict(size=15, color=NAVY)),
        height=460, margin=dict(l=10, r=10, t=70, b=10), hovermode="closest",
        legend=dict(orientation="h", yanchor="top", y=0.02, xanchor="left", x=0.0, font=dict(size=11),
                    bgcolor="rgba(255,255,255,.8)"),
        xaxis=dict(visible=False, fixedrange=True),
        yaxis=dict(visible=False, scaleanchor="x", scaleratio=1, fixedrange=True))
    f = fig.to_dict()
    return {"data": f["data"], "layout": f["layout"], "div_id": div_id, "largura": "meia",
            "config": {"scrollZoom": False, "displayModeBar": False}}


class PainelSarampoBrasil(PainelVigilancia):
    """Sarampo no Brasil com números oficiais do Ministério da Saúde."""

    def __init__(self):
        super().__init__(
            nome="Sarampo no Brasil",
            sigla="SARAMPO_BR",
            descricao="Casos confirmados de sarampo no ano, por UF, segundo o Ministério da Saúde",
        )
        self.dados["aba"] = "Sarampo Brasil"

    def _mapa(self, ufs: dict, ref: str) -> dict:
        classes = {}
        for sigla in ufs_brasil():
            n = ufs.get(sigla, 0)
            c = [c for c in CLASSES_MAPA if n >= c[0]][-1]
            classes[sigla] = (c[1], c[2], f"{n} caso{'s' if n != 1 else ''} confirmado{'s' if n != 1 else ''}")
        rotulos = {s: f"<b>{s}</b> {n}" for s, n in ufs.items() if n}
        return mapa_ufs(classes, [c[1] for c in CLASSES_MAPA], f"<b>Casos confirmados por UF</b><br><sup>até {ref}</sup>",
                        "mapa_sarampo_br", rotulos)

    def _barras(self, ufs: dict, ref: str) -> dict:
        from .mapas import _ufs
        nomes = {s: p["n"] for s, p in _ufs().items()}
        ordem = sorted((n, s) for s, n in ufs.items() if n)
        total = sum(ufs.values()) or 1
        fig = go.Figure(go.Bar(
            y=[nomes.get(s, s) for _, s in ordem], x=[n for n, _ in ordem], orientation="h",
            marker=dict(color=NAVY), text=[f"{n} ({n / total:.0%})".replace(".", ",") for n, _ in ordem],
            textposition="outside", cliponaxis=False,
            hovertemplate="<b>%{y}</b><br>%{x} casos<extra></extra>",
        ))
        fig.update_layout(
            **LAYOUT_BASE,
            title=dict(text=f"<b>Distribuição por UF</b><br><sup>até {ref}</sup>", font=dict(size=15, color=NAVY)),
            height=460, margin=dict(l=10, r=40, t=70, b=40), showlegend=False, bargap=0.45,
            xaxis=dict(title="Casos confirmados", gridcolor="#EEE9DD", zeroline=False,
                       range=[0, max(ufs.values() or [1]) * 1.45]),
            yaxis=dict(automargin=True),
        )
        f = fig.to_dict()
        return {"data": f["data"], "layout": f["layout"], "div_id": "barras_sarampo_br", "largura": "meia",
                "config": {"displayModeBar": False}}

    def _curva(self, regs: list[dict], ano: int) -> dict:
        x = [f"SE {r['se']}<br><sup>{_data_br(r['data_referencia'])[:5]}</sup>" for r in regs]
        y = [r["casos"] for r in regs]
        fig = go.Figure(go.Scatter(
            x=x, y=y, mode="lines+markers+text", line=dict(color=NAVY, width=3), marker=dict(size=9, color=NAVY),
            text=y, textposition="top center", textfont=dict(color=NAVY, size=12),
            customdata=[[r.get("fonte", "")] for r in regs],
            hovertemplate="<b>%{x}</b><br>%{y} casos acumulados<br><i>%{customdata[0]}</i><extra></extra>",
        ))
        fig.update_layout(
            **LAYOUT_BASE,
            title=dict(text=f"<b>Casos acumulados em {ano}</b><br><sup>por data de referência das publicações do MS</sup>",
                       font=dict(size=15, color=NAVY)),
            height=340, margin=dict(l=50, r=30, t=80, b=50), showlegend=False,
            xaxis=dict(type="category", showgrid=False),
            yaxis=dict(title="Casos acumulados", gridcolor="#EEE9DD", zeroline=False, range=[0, max(y) * 1.25]),
        )
        f = fig.to_dict()
        return {"data": f["data"], "layout": f["layout"], "div_id": "curva_sarampo_br", "largura": "inteira",
                "config": {"displayModeBar": False}}

    def coletar(self) -> bool:
        from . import sarampo_brasil
        try:
            ano, regs = sarampo_brasil.registros()
        except Exception as e:
            log.warning(f"Erro ao reunir registros de sarampo no Brasil: {e}")
            return False
        if not regs:
            log.warning("Sarampo no Brasil: nenhum registro oficial disponível.")
            return False

        atual = regs[-1]
        com_ufs = next((r for r in reversed(regs) if r.get("ufs")), None)
        com_obitos = next((r for r in reversed(regs) if r.get("obitos") is not None), None)
        ref = _data_br(atual["data_referencia"])
        ufs = com_ufs["ufs"] if com_ufs else {}
        ref_ufs = _data_br(com_ufs["data_referencia"]) if com_ufs else ref

        self.dados["nome"] = f"Sarampo no Brasil, {ano}"
        self.dados["descricao"] = (f"Casos confirmados de sarampo no Brasil em {ano}, segundo o Ministério da Saúde. "
                                   f"Dados até {ref} (SE {atual['se']}).")
        self.dados["kpis"] = [
            {"valor": f"{atual['casos']:,}".replace(",", "."), "rotulo": f"casos confirmados em {ano}"},
            {"valor": str(com_obitos["obitos"]) if com_obitos else "n/d",
             "rotulo": "óbitos" if not com_obitos or com_obitos is atual
             else f"óbitos (até {_data_br(com_obitos['data_referencia'])})"},
            {"valor": str(sum(1 for n in ufs.values() if n)), "rotulo": "UFs com casos"},
            {"valor": f"SE {atual['se']}", "rotulo": f"dados até {ref}"},
        ]
        self.dados["graficos"] = [
            self._mapa(ufs, ref_ufs) if ufs else None,
            self._barras(ufs, ref_ufs) if ufs else None,
            self._curva(regs, ano) if len(regs) > 1 else None,
        ]
        self.dados["graficos"] = [g for g in self.dados["graficos"] if g]
        notas = []
        if atual.get("nota"):
            notas.append(atual["nota"])
        if atual.get("origem"):
            notas.append(f"Fonte de infecção: {atual['origem']}.")
        if com_ufs is not atual and ufs:
            notas.append(f"A distribuição por UF refere-se a {ref_ufs}, última publicação do MS com detalhamento por UF.")
        self.dados["notas"] = notas
        vistas, fontes = set(), []
        for r in reversed(regs):
            if r.get("url") and r["url"] not in vistas:
                vistas.add(r["url"])
                fontes.append({"nome": r.get("fonte", r["url"]), "url": r["url"]})
        self.dados["fontes"] = fontes
        self.dados["fonte_dados"] = "; ".join(f["nome"] for f in fontes)
        self.dados["registros"] = regs
        self.salvar()
        return True



def _semana_media(regs: list[dict]) -> list[dict]:
    """Média semanal de novos casos/óbitos entre publicações consecutivas (pela data de corte dos dados)."""
    pontos = []
    for a, b in zip(regs, regs[1:]):
        da = date.fromisoformat(a.get("dados_ate") or a["data_referencia"])
        db = date.fromisoformat(b.get("dados_ate") or b["data_referencia"])
        dias = (db - da).days
        if dias <= 0:
            continue
        pontos.append({"inicio": da, "fim": db, "dias": dias,
                       "casos_sem": (b["casos"] - a["casos"]) * 7 / dias,
                       "obitos_sem": (b["obitos"] - a["obitos"]) * 7 / dias})
    return pontos


class PainelEbolaECDC(PainelVigilancia):
    """Ebola (vírus Bundibugyo) na RDC com números oficiais do ECDC."""

    def __init__(self):
        super().__init__(nome="Ebola na RDC, 2026", sigla="EBOLA",
                         descricao="Doença pelo vírus Bundibugyo na República Democrática do Congo, segundo o ECDC")
        self.dados["aba"] = "Ebola"

    def _acumulados(self, regs):
        x = [r["data_referencia"] for r in regs]
        fontes = [[r.get("fonte", "")] for r in regs]
        fig = go.Figure()
        for chave, nome, cor in (("casos", "Casos confirmados", NAVY), ("obitos", "Óbitos entre confirmados", "#A3262A")):
            fig.add_trace(go.Scatter(x=x, y=[r[chave] for r in regs], name=nome, mode="lines+markers",
                                     line=dict(color=cor, width=3), marker=dict(size=7), customdata=fontes,
                                     hovertemplate="%{x|%d/%m/%Y}<br><b>%{y:,}</b> " + nome.lower() +
                                                   "<br><i>%{customdata[0]}</i><extra></extra>"))
        fig.update_layout(**LAYOUT_BASE, height=380, margin=dict(l=60, r=20, t=80, b=40), hovermode="x unified",
                          title=dict(text="<b>Casos e óbitos acumulados</b><br><sup>confirmados, por data de publicação</sup>",
                                     font=dict(size=15, color=NAVY)),
                          legend=dict(orientation="h", y=-0.15, x=0, font=dict(size=11)),
                          xaxis=dict(tickformat="%d/%m", hoverformat="%d/%m/%Y", showgrid=False),
                          separators=",.", yaxis=dict(gridcolor="#EEE9DD", tickformat=",.0f", rangemode="tozero"))
        f = fig.to_dict()
        return {"data": f["data"], "layout": f["layout"], "div_id": "acum_ebola", "largura": "inteira",
                "config": {"displayModeBar": False}}

    def _novos(self, medias):
        from datetime import timedelta
        rot = [f"{m['inicio']:%d/%m} a {m['fim']:%d/%m}" for m in medias]
        fig = go.Figure(go.Bar(x=[(m["inicio"] + timedelta(days=m["dias"] / 2)).isoformat() for m in medias],
                               width=[m["dias"] * 86400000 * 0.9 for m in medias],
                               y=[round(m["casos_sem"]) for m in medias],
                               marker=dict(color=NAVY), customdata=[[r, m["dias"]] for r, m in zip(rot, medias)],
                               hovertemplate="%{customdata[0]} (%{customdata[1]} dias)<br><b>%{y:,}</b> novos casos por semana"
                                             "<extra></extra>"))
        fig.update_layout(**LAYOUT_BASE, height=360, margin=dict(l=50, r=20, t=80, b=40), showlegend=False,
                          title=dict(text="<b>Novos casos por semana</b><br><sup>média entre publicações consecutivas do ECDC</sup>",
                                     font=dict(size=15, color=NAVY)),
                          xaxis=dict(tickformat="%d/%m", showgrid=False),
                          yaxis=dict(gridcolor="#EEE9DD", separatethousands=True, rangemode="tozero"), bargap=0.3)
        f = fig.to_dict()
        return {"data": f["data"], "layout": f["layout"], "div_id": "novos_ebola", "largura": "meia",
                "config": {"displayModeBar": False}}

    def _zonas(self, regs):
        rz = [r for r in regs if r.get("zs_afetadas")]
        fig = go.Figure(go.Scatter(
            x=[r["data_referencia"] for r in rz], y=[r["zs_afetadas"] for r in rz], mode="lines+markers+text",
            line=dict(color=OURO, width=3, shape="hv"), marker=dict(size=8, color=OURO),
            text=[str(r["zs_afetadas"]) for r in rz], textposition="top center", textfont=dict(color="#7A6112", size=11),
            customdata=[[r.get("zs_total") or "", r.get("n_provincias") or ""] for r in rz],
            hovertemplate="%{x|%d/%m/%Y}<br><b>%{y}</b> de %{customdata[0]} zonas de saúde<br>%{customdata[1]} províncias"
                          "<extra></extra>"))
        prov = [(r["data_referencia"], r["n_provincias"]) for r in rz if r.get("n_provincias")]
        mudancas = [p for i, p in enumerate(prov) if i == 0 or p[1] != prov[i - 1][1]]
        for d, n in mudancas:
            fig.add_annotation(x=d, y=0, yref="paper", text=f"{n} prov.", showarrow=False, yanchor="bottom",
                               font=dict(size=10, color=CINZA_TXT), bgcolor="rgba(255,255,255,.8)")
        topo = max(r["zs_afetadas"] for r in rz)
        fig.update_layout(**LAYOUT_BASE, height=360, margin=dict(l=50, r=20, t=80, b=40), showlegend=False,
                          title=dict(text="<b>Zonas de saúde afetadas</b><br><sup>e número de províncias com casos</sup>",
                                     font=dict(size=15, color=NAVY)),
                          xaxis=dict(tickformat="%d/%m", showgrid=False),
                          yaxis=dict(gridcolor="#EEE9DD", range=[0, topo * 1.25]))
        f = fig.to_dict()
        return {"data": f["data"], "layout": f["layout"], "div_id": "zonas_ebola", "largura": "meia",
                "config": {"displayModeBar": False}}

    def _provincias(self, reg):
        itens = sorted(reg["provincias"].items(), key=lambda kv: kv[1]["casos"])
        nomes = [k for k, _ in itens]
        fig = go.Figure(go.Bar(
            y=nomes, x=[v["casos"] for _, v in itens], orientation="h", marker=dict(color=NAVY),
            text=[f"{v['casos']:,} casos · {v['obitos']:,} óbito{'s' if v['obitos'] != 1 else ''} · {v['zs_afetadas']}/{v['zs_total']} ZS".replace(",", ".")
                  for _, v in itens],
            textposition="outside", cliponaxis=False,
            hovertemplate="<b>%{y}</b><br>%{x:,} casos confirmados<extra></extra>"))
        fig.update_layout(**LAYOUT_BASE, height=60 + 42 * len(itens), margin=dict(l=10, r=20, t=80, b=40), showlegend=False,
                          title=dict(text=f"<b>Casos confirmados por província</b><br><sup>até {_data_br(reg['data_referencia'])}; "
                                          "ZS = zonas de saúde afetadas / total da província</sup>",
                                     font=dict(size=15, color=NAVY)),
                          xaxis=dict(type="log", title="casos confirmados (escala logarítmica)", gridcolor="#EEE9DD",
                                     range=[0, 5.45], tickvals=[1, 10, 100, 1000, 10000],
                                     ticktext=["1", "10", "100", "1.000", "10.000"]),
                          yaxis=dict(automargin=True), bargap=0.35)
        f = fig.to_dict()
        return {"data": f["data"], "layout": f["layout"], "div_id": "prov_ebola", "largura": "inteira",
                "config": {"displayModeBar": False}}

    def coletar(self) -> bool:
        from . import ebola_ecdc
        try:
            regs = ebola_ecdc.registros()
        except Exception as e:
            log.warning(f"Erro ao reunir registros do Ebola: {e}")
            return False
        if not regs:
            return False
        atual = regs[-1]
        com_prov = next((r for r in reversed(regs) if r.get("provincias")), None)
        ref = _data_br(atual["data_referencia"])
        letal = atual["obitos"] / atual["casos"] * 100
        fmt = lambda n: f"{n:,}".replace(",", ".")
        self.dados["descricao"] = (f"Doença pelo vírus Bundibugyo na República Democrática do Congo. Casos e óbitos confirmados "
                                   f"segundo o ECDC, até {ref}.")
        self.dados["kpis"] = [
            {"valor": fmt(atual["casos"]), "rotulo": "casos confirmados"},
            {"valor": fmt(atual["obitos"]), "rotulo": "óbitos entre confirmados"},
            {"valor": f"{letal:.1f}%".replace(".", ","), "rotulo": "letalidade entre confirmados"},
            {"valor": f"{atual['zs_afetadas']}/{atual['zs_total']}" if atual.get("zs_afetadas") else "n/d",
             "rotulo": f"zonas de saúde afetadas ({atual.get('n_provincias') or '?'} províncias)"},
        ]
        if atual.get("quenia"):
            q = atual["quenia"]
            self.dados["kpis"].append({"valor": fmt(q["casos"]),
                                       "rotulo": f"casos confirmados no Quênia ({q['obitos']} óbito{'s' if q['obitos'] != 1 else ''})"})
        medias = _semana_media(regs)
        self.dados["graficos"] = [g for g in (
            self._acumulados(regs),
            self._novos(medias) if medias else None,
            self._zonas(regs) if sum(1 for r in regs if r.get("zs_afetadas")) > 1 else None,
            self._provincias(com_prov) if com_prov else None,
        ) if g]

        notas = []
        if len(medias) >= 2:
            a, b = medias[-2], medias[-1]
            var = (b["casos_sem"] - a["casos_sem"]) / a["casos_sem"] * 100 if a["casos_sem"] else 0
            notas.append(f"Média de novos casos confirmados por semana: {fmt(round(b['casos_sem']))} entre "
                         f"{b['inicio']:%d/%m} e {b['fim']:%d/%m}, ante {fmt(round(a['casos_sem']))} entre "
                         f"{a['inicio']:%d/%m} e {a['fim']:%d/%m} ({var:+.0f}%). Cálculo a partir dos acumulados "
                         f"publicados pelo ECDC.".replace("+-", "-"))
        rz = [r for r in regs if r.get("zs_afetadas")]
        if len(rz) > 1:
            p0, p1 = rz[0], rz[-1]
            notas.append(f"Expansão geográfica: as zonas de saúde afetadas passaram de {p0['zs_afetadas']} "
                         f"({_data_br(p0['data_referencia'])}) para {p1['zs_afetadas']} ({_data_br(p1['data_referencia'])}), "
                         f"e as províncias com casos, de {p0.get('n_provincias')} para {p1.get('n_provincias')}.")
        q = atual.get("quenia")
        if q:
            notas.append(f"Quênia: {q['casos']} casos confirmados, incluindo {q['obitos']} óbito{'s' if q['obitos'] != 1 else ''} "
                         f"(ECDC, até {ref}); o primeiro caso foi importado da RDC.")
        if atual.get("novos_casos") is not None:
            notas.append(f"Na última atualização do ECDC: {fmt(atual['novos_casos'])} novos casos confirmados e "
                         f"{fmt(atual['novos_obitos'])} óbitos.")
        notas.append("Segundo o ECDC, os dados da RDC sobre casos e óbitos confirmados estão em revisão e harmonização contínuas.")
        self.dados["notas"] = notas
        vistas, fontes = set(), []
        for r in reversed(regs):
            nome = "ECDC, página do surto (atualizada nos dias úteis)" if r.get("captura") == "automática" else r.get("fonte")
            if r.get("url") and r["url"] not in vistas and len(fontes) < 3:
                vistas.add(r["url"])
                fontes.append({"nome": nome, "url": r["url"]})
        self.dados["fontes"] = fontes
        self.dados["fonte_dados"] = "ECDC"
        self.dados["registros"] = regs
        self.salvar()
        return True



NIVEIS_ORDEM = ["Segurança", "Baixo risco", "Alerta", "Risco", "Alto risco", "Sem classificação"]
CORES_NIVEL = {"Segurança": "#CFE6D6", "Baixo risco": "#F1E3A6", "Alerta": "#E7AE3C", "Risco": "#D4602A",
               "Alto risco": "#9E2424", "Sem classificação": CINZA_UF}
CORES_VIRUS = {"SARS2": ("SARS-CoV-2", "#A3262A"), "VSR": ("VSR", NAVY), "FLU_A": ("Influenza A", OURO),
               "FLU_B": ("Influenza B", "#2F7F7A"), "RINO": ("Rinovírus", "#8C96A6"), "METAP": ("Metapneumovírus", "#6F8FBF")}


def _fmt(n: float) -> str:
    return f"{round(n):,}".replace(",", ".")


class PainelSRAG(PainelVigilancia):
    """SRAG no Brasil e no DF com dados do InfoGripe (Fiocruz e Ministério da Saúde)."""

    def __init__(self):
        super().__init__(nome="SRAG no Brasil", sigla="SRAG",
                         descricao="Síndrome respiratória aguda grave, segundo o InfoGripe")
        self.dados["aba"] = "SRAG"

    @staticmethod
    def _serie(s: list[dict], titulo: str, div_id: str, largura: str, altura: int) -> dict:
        x = [d["inicio"] for d in s]
        rot = [f"SE {d['se']}/{d['ano']}" for d in s]
        ultimas = [d for d in s if d["ic95i"] is not None]
        fig = go.Figure()
        fig.add_trace(go.Bar(x=x, y=[d["notificados"] for d in s], name="Notificados até a atualização",
                             marker=dict(color="#C9D3E3"), customdata=rot,
                             hovertemplate="%{customdata}<br>%{y:,.0f} notificados<extra></extra>"))
        if ultimas:
            xs = [d["inicio"] for d in ultimas]
            fig.add_trace(go.Scatter(x=xs + xs[::-1], y=[d["ic95s"] for d in ultimas] + [d["ic95i"] for d in ultimas][::-1],
                                     fill="toself", fillcolor="rgba(10,53,112,.15)", line=dict(width=0), mode="lines",
                                     name="IC 95% da estimativa", hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=x, y=[d["estimados"] for d in s], name="Casos estimados (nowcasting)", mode="lines",
                                 line=dict(color=NAVY, width=3), customdata=[[r, d["ic95i"] or d["estimados"], d["ic95s"] or d["estimados"]]
                                                                              for r, d in zip(rot, s)],
                                 hovertemplate="%{customdata[0]}<br><b>%{y:,.0f}</b> estimados"
                                               "<br>IC 95%: %{customdata[1]:,.0f} a %{customdata[2]:,.0f}<extra></extra>"))
        fig.update_layout(**LAYOUT_BASE, title=dict(text=titulo, font=dict(size=15, color=NAVY)), height=altura,
                          margin=dict(l=55, r=20, t=80, b=40), hovermode="x unified", bargap=0.15,
                          legend=dict(orientation="h", y=-0.12, x=0, font=dict(size=11)),
                          separators=",.",
                          xaxis=dict(tickformat="%d/%m/%y", showgrid=False, hoverformat="%d/%m/%Y"),
                          yaxis=dict(gridcolor="#EEE9DD", rangemode="tozero", separatethousands=True))
        f = fig.to_dict()
        return {"data": f["data"], "layout": f["layout"], "div_id": div_id, "largura": largura,
                "config": {"displayModeBar": False}}

    @staticmethod
    def _virus(v: dict) -> dict:
        fig = go.Figure()
        for chave, (nome, cor) in CORES_VIRUS.items():
            s = v.get(chave)
            if not s:
                continue
            fig.add_trace(go.Scatter(x=[d["inicio"] for d in s], y=[d["estimados"] for d in s], name=nome, mode="lines",
                                     line=dict(color=cor, width=2.5), customdata=[f"SE {d['se']}/{d['ano']}" for d in s],
                                     hovertemplate="%{customdata}: <b>%{y:,.0f}</b> " + nome + "<extra></extra>"))
        fig.update_layout(**LAYOUT_BASE, height=380, margin=dict(l=55, r=20, t=80, b=40), hovermode="x unified",
                          title=dict(text="<b>Casos de SRAG por vírus, Brasil</b><br><sup>casos estimados por semana de início "
                                          "de sintomas, últimas 26 semanas</sup>", font=dict(size=15, color=NAVY)),
                          legend=dict(orientation="h", y=-0.12, x=0, font=dict(size=11)), separators=",.",
                          xaxis=dict(tickformat="%d/%m/%y", showgrid=False, hoverformat="%d/%m/%Y"),
                          yaxis=dict(gridcolor="#EEE9DD", rangemode="tozero", separatethousands=True))
        f = fig.to_dict()
        return {"data": f["data"], "layout": f["layout"], "div_id": "virus_srag", "largura": "inteira",
                "config": {"displayModeBar": False}}

    def coletar(self) -> bool:
        from . import infogripe as ig
        d = ig.coletar()
        if not d:
            return False
        br, df = d["br"], d["df"]
        ub, ud = br[-1], df[-1] if df else None
        lp_val, lp_txt = ig.TENDENCIA.get(ub["tend_lp"], ("n/d", "sem classificação"))
        cp_val, cp_txt = ig.TENDENCIA.get(ub["tend_cp"], ("n/d", "sem classificação"))
        niveis = d["niveis"]
        se_nivel = max((n["se"] for n in niveis.values()), default=None)
        em_alerta = sorted(s for s, n in niveis.items() if n["nivel"] in ("Alerta", "Risco", "Alto risco"))

        self.dados["nome"] = f"SRAG no Brasil e no Distrito Federal"
        self.dados["descricao"] = (f"Casos de síndrome respiratória aguda grave por semana de início de sintomas, "
                                   f"segundo o InfoGripe. Última semana: SE {ub['se']}/{ub['ano']} "
                                   f"({_data_br(ub['inicio'])[:5]} a {_data_br(ub['fim'])}).")
        self.dados["kpis"] = [
            {"valor": _fmt(ub["estimados"]),
             "rotulo": f"casos estimados no Brasil, SE {ub['se']}; IC 95%: {_fmt(ub['ic95i'])} a {_fmt(ub['ic95s'])}"},
            {"valor": lp_val, "rotulo": f"longo prazo, Brasil: {lp_txt.split(' (')[0]}"},
            {"valor": cp_val, "rotulo": f"curto prazo, Brasil: {cp_txt.split(' (')[0]}"},
        ]
        if ud:
            df_lp = ig.TENDENCIA.get(ud["tend_lp"], ("n/d", ""))[0].lower()
            self.dados["kpis"].append({"valor": _fmt(ud["estimados"]),
                                       "rotulo": f"casos estimados no DF, SE {ud['se']}; longo prazo: {df_lp}"})
        self.dados["kpis"].append({"valor": str(len(em_alerta)),
                                   "rotulo": f"UFs em alerta ou risco ({', '.join(em_alerta) or 'nenhuma'})"})

        classes = {}
        for sigla in ufs_brasil():
            n = niveis.get(sigla)
            nivel = n["nivel"] if n else "Sem classificação"
            classes[sigla] = (nivel, CORES_NIVEL.get(nivel, CINZA_UF), f"nível de atividade: {nivel.lower()}")
        mapa = mapa_ufs(classes, NIVEIS_ORDEM, f"<b>Nível de atividade de SRAG por UF</b><br><sup>SE {se_nivel}/{ub['ano']}, "
                                               f"segundo o InfoGripe</sup>", "mapa_srag",
                        {s: f"<b>{s}</b>" for s in em_alerta})
        self.dados["graficos"] = [
            self._serie(br, "<b>Casos semanais de SRAG, Brasil</b><br><sup>por semana de início de sintomas, últimas 52 semanas</sup>",
                        "serie_srag_br", "inteira", 400),
            mapa,
            self._serie(df[-26:], "<b>Casos semanais de SRAG, DF</b><br><sup>Distrito Federal, últimas 26 semanas</sup>",
                        "serie_srag_df", "meia", 460) if df else None,
            self._virus(d["virus_br"]) if d["virus_br"] else None,
        ]
        self.dados["graficos"] = [g for g in self.dados["graficos"] if g]
        sem_nivel = sorted(s for s in ufs_brasil() if s not in niveis)
        self.dados["notas"] = [
            "As semanas mais recentes têm notificação incompleta; a linha mostra a estimativa do InfoGripe corrigida pelo atraso "
            "de digitação (nowcasting), com intervalo de 95%.",
            "Tendência, conforme nota técnica do InfoGripe: probabilidade de crescimento ou de queda dos casos semanais nas últimas "
            "6 semanas (longo prazo) ou 3 semanas (curto prazo); leve a partir de 75%, alta a partir de 95%.",
            "Nível de atividade: compara a incidência de SRAG das duas últimas semanas com a série histórica do período posterior "
            "à vacinação contra a COVID-19."
            + (f" Sem classificação nesta semana: {', '.join(sem_nivel)}." if sem_nivel else ""),
        ]
        self.dados["fontes"] = [
            {"nome": "InfoGripe (Fiocruz e GT-Influenza/Ministério da Saúde)", "url": "http://info.gripe.fiocruz.br"},
            {"nome": "dados abertos do InfoGripe", "url": ig.URL_REPO},
            {"nome": "nota técnica sobre os indicadores de tendência", "url": ig.URL_NOTA_TENDENCIA},
        ]
        self.dados["fonte_dados"] = "InfoGripe"
        self.salvar()
        return True


def gerar_paineis() -> None:
    """Gera todos os painéis de vigilância."""
    log.info("Gerando painéis de vigilância...")

    paineis = [
        PainelSRAG(),
        PainelSarampoBrasil(),
        PainelEbolaECDC(),
    ]

    sucesso = 0
    for painel in paineis:
        if painel.coletar():
            sucesso += 1

    log.info(f"Painéis gerados: {sucesso}/{len(paineis)}")


def listar_paineis() -> list[dict]:
    """Lista todos os painéis disponíveis."""
    paineis = []
    for arq in PAINEIS_DIR.glob("*.json"):
        try:
            p = ler_json(arq, {})
            paineis.append(p)
        except Exception as e:
            log.warning(f"Erro ao ler {arq}: {e}")
    ordem = {"SRAG": 0, "SARAMPO_BR": 1, "EBOLA": 2}
    return sorted(paineis, key=lambda p: (ordem.get(p.get("sigla", ""), 99), p.get("nome", "")))
