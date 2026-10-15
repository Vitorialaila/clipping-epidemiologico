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


def main() -> int:
    configurar_log()
    p = argparse.ArgumentParser(prog="clipping", description="Clipping epidemiológico automático")
    p.add_argument("etapa", choices=["tudo", "coletar", "clipping", "site", "repontuar"], nargs="?", default="tudo")
    p.add_argument("--se", help="Semana epidemiológica no formato AAAASS (ex.: 202640)")
    p.add_argument("--forcar", action="store_true", help="Gera nova versão mesmo se a edição já existir")
    a = p.parse_args()

    from . import coletar, gerar_clipping, gerar_site

    if a.etapa in ("tudo", "coletar"):
        resumo = coletar.coletar()
        if resumo["fontes_erro"]:
            log.warning("Fontes com falha nesta execução: %s", ", ".join(resumo["fontes_erro"]))
    if a.etapa == "repontuar":
        coletar.repontuar()
    if a.etapa in ("tudo", "clipping"):
        gerar_clipping.gerar(a.se, a.forcar)
    if a.etapa in ("tudo", "coletar", "clipping", "site", "repontuar"):
        gerar_site.gerar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
