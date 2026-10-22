"""Linha de comando.

Exemplos:
  python -m clipping tudo                 # coleta, clipping da última SE fechada (se faltar) e site
  python -m clipping coletar
  python -m clipping clipping --se 202640 # gera a edição de uma SE específica
  python -m clipping clipping --se 202640 --forcar   # nova versão (-v2), sem apagar a anterior
  python -m clipping site
  python -m clipping repontuar            # recalcula a pontuação após editar criterios.yaml
"""
from __future__ import annotations

import argparse
import sys

from .util import configurar_log, log


def _pode_gerar_edicao() -> bool:
    """Na execução automática (de hora em hora), a edição da semana só sai a partir de domingo às 6h
    (Brasília), para dar tempo de as fontes publicarem as notícias de sábado."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    agora = datetime.now(ZoneInfo("America/Sao_Paulo"))
    return not (agora.weekday() == 6 and agora.hour < 6)


def main() -> int:
    configurar_log()
    p = argparse.ArgumentParser(prog="clipping", description="Clipping epidemiológico automático")
    p.add_argument("etapa", choices=["tudo", "coletar", "clipping", "site", "repontuar", "testar-ia", "boletim-baixar", "boletim"], nargs="?", default="tudo")
    p.add_argument("--se", help="Semana epidemiológica no formato AAAASS (ex.: 202640)")
    p.add_argument("--edicao", help="Edição do boletim no formato AAAA-SS (ex.: 2026-40)")
    p.add_argument("--forcar", action="store_true", help="Gera nova versão mesmo se a edição já existir")
    a = p.parse_args()

    from . import coletar, gerar_clipping, gerar_site, paineis

    if a.etapa in ("boletim-baixar", "boletim"):
        from . import boletim_slides
        boletim_slides.baixar(a.edicao)
        if a.etapa == "boletim":
            from . import boletim
            boletim.gerar(a.edicao, a.forcar)
            paineis.gerar_paineis()
            gerar_site.gerar()
        return 0

    if a.etapa == "testar-ia":
        from . import ia
        return 0 if ia.testar() else 1

    if a.etapa in ("tudo", "coletar"):
        resumo = coletar.coletar()
        if resumo["fontes_erro"]:
            log.warning("Fontes com falha nesta execução: %s", ", ".join(resumo["fontes_erro"]))
    if a.etapa == "repontuar":
        coletar.repontuar()
    if a.etapa == "clipping" or (a.etapa == "tudo" and _pode_gerar_edicao()):
        gerar_clipping.gerar(a.se, a.forcar)
    if a.etapa in ("tudo", "coletar", "clipping", "site", "repontuar"):
        paineis.gerar_paineis()
        gerar_site.gerar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
