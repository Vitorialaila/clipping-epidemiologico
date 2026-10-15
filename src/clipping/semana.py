"""Semana epidemiológica (SE), no padrão adotado pelo Ministério da Saúde.

A SE começa no domingo e termina no sábado. A SE 1 de cada ano é a que
contém pelo menos quatro dias de janeiro (mesma regra do CDC/MMWR).
"""
from __future__ import annotations

from datetime import date

from epiweeks import Week

from .util import MESES


def se_da_data(d: date) -> Week:
    return Week.fromdate(d, system="cdc")


def ultima_se_fechada(hoje: date | None = None) -> Week:
    hoje = hoje or date.today()
    return se_da_data(hoje) - 1


def de_codigo(codigo: str) -> Week:
    """Aceita '202640', '2026-40' ou '2026/40'."""
    limpo = "".join(c for c in str(codigo) if c.isdigit())
    if len(limpo) != 6:
        raise ValueError(f"SE inválida: {codigo!r}. Use o formato AAAASS, por exemplo 202640.")
    return Week(int(limpo[:4]), int(limpo[4:]), system="cdc")


def codigo(se: Week) -> str:
    return f"{se.year}-{se.week:02d}"


def rotulo(se: Week) -> str:
    return f"SE {se.week:02d}/{se.year}"


def periodo_extenso(se: Week) -> str:
    ini, fim = se.startdate(), se.enddate()
    if ini.year != fim.year:
        return (f"{ini.day} de {MESES[ini.month - 1]} de {ini.year} "
                f"a {fim.day} de {MESES[fim.month - 1]} de {fim.year}")
    if ini.month != fim.month:
        return f"{ini.day} de {MESES[ini.month - 1]} a {fim.day} de {MESES[fim.month - 1]} de {fim.year}"
    return f"{ini.day} a {fim.day} de {MESES[fim.month - 1]} de {fim.year}"


def contem(se: Week, d: date) -> bool:
    return se.startdate() <= d <= se.enddate()
