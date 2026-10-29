"""Mesmo evento em notícias diferentes (ex.: G1 regional e G1 nacional, ou G1 e Metrópoles sobre o mesmo caso).

Duas notícias tratam do mesmo evento quando: citam o mesmo agravo principal; foram publicadas com até
JANELA_DIAS de diferença; não citam estados diferentes; e as palavras distintivas do título (sem o nome do
agravo, que já é comum às duas) se sobrepõem o bastante. A sobreposição é ponderada pela raridade da palavra
na base (nomes de cidades e de hospitais pesam mais que palavras comuns) e medida sobre o título mais curto,
para que "Casos de Ebola passam de 10 mil no Congo" case com a versão mais longa da mesma matéria.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date

from .util import carregar_yaml, normalizar

JANELA_DIAS = 4
LIMIAR = 0.4

_PALAVRAS_VAZIAS = set("""a o as os de da do das dos e em no na nos nas para por com um uma que se ao the of in and to
for on at is are from with by after la le les el los las des du del en et y un une su sur pour par con como mais than
over what who how why new apos sobre entre ate this that its it has have will be as an or ser sao esta sua seu nesta
neste desta deste dia diz dizem segundo ja tem ter veja saiba entenda caso casos case cases anos registra confirma
confirmado confirmada confirmados""".split())
_OBITO = {"morte", "mortes", "morre", "morrem", "morreu", "morreram", "morto", "mortos", "obito", "obitos",
          "death", "deaths", "dies", "died"}
# Palavras que descrevem o tipo de evento ou a pessoa, comuns a eventos diferentes: não bastam para casar
_GENERICAS = {"obito", "menina", "menino", "crianca", "bebe", "homem", "mulher", "paciente", "idoso", "idosa", "jovem",
              "adolescente", "suspeita", "suspeito", "surto", "investiga", "investigada", "investigado", "secretaria",
              "saude", "estadual", "municipal", "prefeitura", "hospital", "cidade", "regiao", "alerta", "mai", "mil",
              "aula", "suspensa", "suspenso", "suspende", "suspendem", "escola", "creche", "interior", "estado"}
_UF = {"ac": ["acre"], "al": ["alagoas"], "ap": ["amapa"], "am": ["amazonas", "manaus"], "ba": ["bahia", "salvador"],
       "ce": ["ceara", "fortaleza"], "df": ["distrito federal", "brasilia"], "es": ["espirito santo"],
       "go": ["goias", "goiania"], "ma": ["maranhao", "sao luis"], "mt": ["mato grosso", "cuiaba"],
       "ms": ["mato grosso do sul", "campo grande"], "mg": ["minas gerais", "belo horizonte"], "pa": ["estado do para", "belem"],
       "pb": ["paraiba", "joao pessoa"], "pr": ["parana", "curitiba"], "pe": ["pernambuco", "recife"],
       "pi": ["piaui", "teresina"], "rj": ["rio de janeiro"], "rn": ["rio grande do norte", "natal"],
       "rs": ["rio grande do sul", "porto alegre"], "ro": ["rondonia", "porto velho"], "rr": ["roraima", "boa vista"],
       "sc": ["santa catarina", "florianopolis"], "sp": ["sao paulo"], "se": ["sergipe", "aracaju"],
       "to": ["tocantins", "palmas"]}
# Siglas que também são palavras comuns ficam de fora da busca pela sigla (só valem pelo nome do estado)
_SIGLAS_SEGURAS = {"ba", "df", "mg", "mt", "pb", "pr", "rj", "rn", "rs", "sc", "sp"}


def _termos_agravo() -> set:
    """Palavras dos nomes curtos de agravo (até duas palavras): não distinguem um evento de outro do mesmo agravo."""
    out = set()
    for a in carregar_yaml("criterios.yaml")["agravos"]:
        for t in a["termos"]:
            t = str(t).rstrip("*")
            if len(t.split()) <= 2:
                out.update(re.findall(r"[a-z0-9]+", normalizar(t)))
    return out


def _palavras(titulo: str, agravo_termos: set) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9]+", normalizar(titulo)):
        if not w.isdigit() and (len(w) <= 2 or w in _PALAVRAS_VAZIAS or w in agravo_termos):
            continue
        if w in _OBITO:
            w = "obito"
        elif len(w) > 4 and w.endswith("s"):
            w = w[:-1]
        out.append(w)
    return out


def _ufs(titulo: str) -> set:
    t = " " + re.sub(r"[^a-z0-9]+", " ", normalizar(titulo)) + " "
    r = {uf for uf, nomes in _UF.items() if any(f" {n} " in t for n in nomes) or (uf in _SIGLAS_SEGURAS and f" {uf} " in t)}
    if "mato grosso do sul" in t:
        r.discard("mt")
    return r


class Eventos:
    """Índice das notícias já guardadas, para reconhecer uma nova notícia sobre o mesmo evento."""

    def __init__(self, noticias: list[dict]):
        self._agt = _termos_agravo()
        self._df = Counter(w for n in noticias for w in set(_palavras(n["titulo"], self._agt)))
        self._n = max(len(noticias), 1)
        self._itens = []
        for n in noticias:
            self.adicionar(n["titulo"], (n.get("agravos") or [""])[0], n["data"][:10], n.get("sigla") or n.get("fonte", ""))

    def _vetor(self, titulo: str) -> dict:
        return {w: math.log((self._n + 1) / (self._df[w] + 1)) + 1 for w in set(_palavras(titulo, self._agt))}

    def adicionar(self, titulo: str, agravo: str, data: str, fonte: str) -> None:
        self._itens.append((self._vetor(titulo), _ufs(titulo), agravo, date.fromisoformat(data), titulo, fonte))

    def igual(self, titulo: str, agravo: str, data: str) -> tuple[str, str] | None:
        """Devolve (título, fonte) da notícia já guardada sobre o mesmo evento, ou None."""
        if not agravo:
            return None
        v, ufs, d = self._vetor(titulo), _ufs(titulo), date.fromisoformat(data)
        if not v:
            return None
        melhor, achado = 0.0, None
        for v2, ufs2, ag2, d2, t2, f2 in self._itens:
            if ag2 != agravo or abs((d - d2).days) > JANELA_DIAS or not v2:
                continue
            if ufs and ufs2 and not ufs & ufs2:
                continue  # estados diferentes: eventos diferentes
            n1, n2 = {w for w in v if w.isdigit()}, {w for w in v2 if w.isdigit()}
            if n1 and n2 and not n1 & n2:
                continue  # números diferentes (idade, quantidade de casos): eventos diferentes
            compartilhadas = v.keys() & v2.keys()
            if not compartilhadas - _GENERICAS:
                continue  # só têm em comum palavras genéricas ("menina", "morte", "suspeita")
            comum = sum(v[w] for w in compartilhadas)
            s = comum / min(sum(v.values()), sum(v2.values()))
            if s > melhor:
                melhor, achado = s, (t2, f2)
        return achado if melhor >= LIMIAR else None
