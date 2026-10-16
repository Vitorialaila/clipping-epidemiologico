"""Baixa os slides exportados do Canva para data/boletins/<edicao>/slides/.

Os links de exportação do Canva expiram em poucas horas; por isso o download é feito
logo após a exportação, e as imagens ficam guardadas no repositório.
"""
from __future__ import annotations

import re
import time

from .util import DADOS, ler_json, log, sessao_http

DIR_BOLETINS = DADOS / "boletins"


def baixar(edicao: str) -> int:
    pasta = DIR_BOLETINS / edicao
    exp = ler_json(pasta / "exportacao.json", {})
    destino = pasta / "slides"
    destino.mkdir(parents=True, exist_ok=True)
    s = sessao_http()
    baixados = 0
    for url in exp.get("urls", []):
        m = re.search(r"/(\d{4})-\d+\.(png|jpg)", url)
        if not m:
            continue
        arq = destino / f"{int(m.group(1)):02d}.{m.group(2)}"
        if arq.exists() and arq.stat().st_size > 1000:
            continue
        for tentativa in range(3):
            try:
                r = s.get(url, timeout=60)
                r.raise_for_status()
                arq.write_bytes(r.content)
                baixados += 1
                break
            except Exception as erro:  # noqa: BLE001
                log.warning("Falha ao baixar o slide %s (tentativa %d): %s", arq.name, tentativa + 1, erro)
                time.sleep(3)
    total = len(list(destino.glob("*.png")))
    log.info("Boletim %s: %d slides baixados agora, %d disponíveis.", edicao, baixados, total)
    return baixados
