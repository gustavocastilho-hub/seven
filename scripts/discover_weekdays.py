"""Gera scripts/class_weekdays.json — mapa {class_id: [weekdays]}.

Estratégia: CRUZA duas fontes que já conhecemos:

  1) `/config/classes` da CloudGym → nos diz o `time` (HH:mm) e `name` de cada
     class_id.
  2) A grade textual fixa em `app.tools._GRADE_FIXA` → nos diz, para cada
     modalidade, em quais dias da semana cada `time` roda.

Bater só uma vez no `/config/classes` (já feito em prod via cache Redis) evita
rate-limit 429. E é confiável: a grade é curada pelo cliente, espelha a
realidade melhor do que tentar inferir pela API de attendance.

Saída:
    {"22775415": [1, 3], ...}   # 17:15 Bike roda Ter/Qui

Uso:
    cd clientes/seven
    python -m scripts.discover_weekdays
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.data import class_catalog as catalog  # noqa: E402
from app.services import cloudgym  # noqa: E402
from app.tools import _GRADE_FIXA  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("discover_weekdays")

OUTPUT_PATH = ROOT / "scripts" / "class_weekdays.json"

# "Seg/Qua/Sex" → [0, 2, 4]
_WD_MAP = {"Seg": 0, "Ter": 1, "Qua": 2, "Qui": 3, "Sex": 4, "Sab": 5, "Dom": 6}


def parse_grade(grade_str: str) -> dict[str, list[int]]:
    """Parseia "Seg/Qua 07:00, 18:30 | Ter/Qui 06:00, 08:15, 17:15 | Sex 07:00"
    e retorna {time: [weekdays]}. Times em HH:mm."""
    out: dict[str, set[int]] = {}
    for segment in grade_str.split("|"):
        segment = segment.strip()
        if not segment:
            continue
        # "Seg/Qua 07:00, 18:30"
        m = re.match(r"([A-Za-z/]+)\s+(.+)", segment)
        if not m:
            continue
        dias_raw, horas_raw = m.group(1), m.group(2)
        wds: list[int] = []
        for token in dias_raw.split("/"):
            token = token.strip()
            if token in _WD_MAP:
                wds.append(_WD_MAP[token])
        horas = [h.strip() for h in horas_raw.split(",")]
        for hora in horas:
            # normaliza "07:00" → "07:00"
            hora = hora[:5]
            out.setdefault(hora, set()).update(wds)
    return {k: sorted(v) for k, v in out.items()}


async def main() -> None:
    classes = await cloudgym.list_classes(force=True)
    log.info("Carregadas %d classes da CloudGym", len(classes))

    # grade por modalidade canônica: {canon: {time: [wds]}}
    grade_by_canon = {canon: parse_grade(txt) for canon, txt in _GRADE_FIXA.items()}

    existing: dict[str, list[int]] = {}
    if OUTPUT_PATH.exists():
        try:
            existing = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    # mapa canon → nome API uppercase
    api_to_canon = {v: k for k, v in catalog.API_NAME.items()}

    unmapped: list[tuple[str, str, str]] = []
    for c in classes:
        cid = str(c.get("id"))
        name = (c.get("name") or "").strip().upper()
        time_raw = (c.get("time") or "")
        hora = time_raw[:5] if len(time_raw) >= 5 else time_raw
        canon = api_to_canon.get(name)
        if not canon:
            # nome fora do mapa (pode ser modalidade nova/sem catálogo)
            continue
        grade = grade_by_canon.get(canon, {})
        wds = grade.get(hora)
        if wds is None:
            unmapped.append((cid, name, hora))
            continue
        existing[cid] = sorted(set(wds))

    OUTPUT_PATH.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    log.info("Salvo em %s (%d IDs mapeados)", OUTPUT_PATH, len(existing))

    if unmapped:
        log.warning("%d classes sem match na grade textual (hora não listada):",
                    len(unmapped))
        for cid, name, hora in unmapped[:20]:
            log.warning("   %s  %s  %s", cid, name, hora)
        log.warning("Revise _GRADE_FIXA em app/tools.py se algum desses deve rodar.")


if __name__ == "__main__":
    asyncio.run(main())
