"""
Catálogo estático de aulas da CloudGym (unit 2751 — Seven Academia).

Extraído do nó `id_aula` do fluxo n8n `agenda horario seven.json`. Cada modalidade
tem vários `class_id` — um por slot fixo (dia da semana + hora) da grade.

A descoberta de qual ID corresponde a qual (dia, hora) é feita pelo script
`scripts/test_cloudgym.py`, que gera `scripts/class_discovery.json`.
"""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Optional

# ---------------- Mapping principal ----------------

CLASS_IDS_BY_MODALITY: dict[str, list[int]] = {
    "muay_thai": [
        22241580, 17468573, 18713494, 18713493, 23321895, 23321894,
        18713528, 18713529, 18713540, 18713539, 18713514, 18713515,
    ],
    "seven_bike": [
        22775332, 22775331, 22775346, 22775347, 22775348, 22775399,
        22775400, 22775415, 22775414, 22775437, 22775436,
    ],
    "seven_cross": [
        17468232, 17468234, 22230667, 23579959, 17468415, 18957362,
        18957361, 17468694, 17468695, 21730459,
    ],
    "seven_pump": [
        18395067, 9968306, 18433531, 17540216, 17482885, 9968301,
        17586251, 17586252, 9968407, 18399314,
    ],
    "fitdance": [17468401, 17468402, 17468616, 17468617],
    "bike_move": [22775487, 22775474, 22775473],
    "muay_thai_kids": [19235802, 19235803],
    "seven_mais_bike": [23579965],
}

# Nome amigável (usado em mensagens para a Zoe / recepção)
DISPLAY_NAME: dict[str, str] = {
    "muay_thai": "Muay Thai Feminino",
    "seven_bike": "Seven Bike",
    "seven_cross": "Seven Cross",
    "seven_pump": "Seven Pump",
    "fitdance": "Fit Dance",
    "bike_move": "Bike Move",
    "muay_thai_kids": "Muay Thai Kids",
    "seven_mais_bike": "Seven Mais - Seven Bike",
}

# Aliases normalizados (sem acento, lowercase) → chave canônica
ALIASES: dict[str, str] = {
    "cross": "seven_cross",
    "seven cross": "seven_cross",
    "crossfit": "seven_cross",
    "bike": "seven_bike",
    "seven bike": "seven_bike",
    "rpm": "seven_bike",
    "spinning": "seven_bike",
    "bike move": "bike_move",
    "pump": "seven_pump",
    "seven pump": "seven_pump",
    "muay": "muay_thai",
    "muay thai": "muay_thai",
    "muaythai": "muay_thai",
    "muay thai feminino": "muay_thai",
    "muay thai kids": "muay_thai_kids",
    "muay kids": "muay_thai_kids",
    "fitdance": "fitdance",
    "fit dance": "fitdance",
    "danca": "fitdance",
    "dança": "fitdance",
}

# Capacidade máxima por modalidade (informativo — CloudGym é a verdade)
CAPACITY: dict[str, int] = {
    "seven_bike": 20,
    "bike_move": 20,
    "seven_cross": 12,
    "fitdance": 25,
    "muay_thai": 18,
    "muay_thai_kids": 10,
    "seven_pump": 20,
}

# Plano "trial" (aula experimental): só agenda se member.plan == TRIAL_PLAN_ID
TRIAL_PLAN_ID = "218281"
# planExtId usado no cadastro via POST /customer
TRIAL_PLAN_EXT_ID = "i4udpk54"

# Mapeia modalidade canônica → nome em UPPERCASE como vem de /config/classes.
API_NAME: dict[str, str] = {
    "muay_thai": "MUAY THAI",
    "seven_bike": "SEVEN BIKE",
    "seven_cross": "SEVEN CROSS",
    "seven_pump": "SEVEN PUMP",
    "fitdance": "FITDANCE",
    "bike_move": "BIKE MOVE",
    "muay_thai_kids": "MUAY THAI KIDS",
    "seven_mais_bike": "SEVEN MAIS",
}

# Weekdays (0=seg, 6=dom) em que cada modalidade roda. Fonte: grade oficial
# listada em app/prompt.py (CATALOGO_HORARIOS). Usado como hard-filter em
# lista_horarios — se o dia pedido não bater, a tool retorna erro sem bater na API.
WEEKDAYS_BY_MODALITY: dict[str, set[int]] = {
    "seven_cross": {0, 2, 4},              # Seg, Qua, Sex
    "muay_thai": {0, 1, 2, 3},             # Seg, Ter, Qua, Qui
    "muay_thai_kids": {0, 2},              # Seg, Qua
    "fitdance": {0, 1, 2, 3},              # Seg, Ter, Qua, Qui
    "bike_move": {1, 3, 4},                # Ter, Qui, Sex
    "seven_bike": {0, 1, 2, 3, 4},         # Seg-Sex
    "seven_pump": {0, 1, 2, 3, 4},         # Seg-Sex
    "seven_mais_bike": {5},                # Sábado (Seven Mais)
}


# ---------------- Discovery (ID → modalidade, weekday, hora) ----------------

_DISCOVERY_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "class_discovery.json"
_discovery_cache: Optional[dict[str, dict]] = None


def _load_discovery() -> dict[str, dict]:
    global _discovery_cache
    if _discovery_cache is not None:
        return _discovery_cache
    if _DISCOVERY_PATH.exists():
        try:
            _discovery_cache = json.loads(_DISCOVERY_PATH.read_text(encoding="utf-8"))
        except Exception:
            _discovery_cache = {}
    else:
        _discovery_cache = {}
    return _discovery_cache


def get_class_meta(class_id: int | str) -> dict:
    """Retorna {modalidade, startTime, weekday} se descoberto; {} caso contrário."""
    return _load_discovery().get(str(class_id), {})


# ---------------- Helpers ----------------

def _normalize(s: str) -> str:
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def resolve_modality(q: str) -> Optional[str]:
    """Resolve um termo livre para a chave canônica. Retorna None se não casar."""
    qn = _normalize(q)
    if not qn:
        return None
    # match exato
    if qn in ALIASES:
        return ALIASES[qn]
    # substring bidirecional
    for alias, canon in ALIASES.items():
        if alias in qn or qn in alias:
            return canon
    # chave canônica crua
    if qn.replace(" ", "_") in CLASS_IDS_BY_MODALITY:
        return qn.replace(" ", "_")
    return None


def ids_for_modality(q: str) -> list[int]:
    canon = resolve_modality(q)
    if not canon:
        return []
    return CLASS_IDS_BY_MODALITY.get(canon, [])


def ids_for_modality_and_weekday(q: str, weekday: int) -> list[int]:
    """Se houver discovery, filtra só os IDs cujo weekday bate. Senão, retorna todos."""
    canon = resolve_modality(q)
    if not canon:
        return []
    all_ids = CLASS_IDS_BY_MODALITY.get(canon, [])
    discovery = _load_discovery()
    filtered = []
    for cid in all_ids:
        meta = discovery.get(str(cid))
        if not meta or meta.get("weekday") is None:
            filtered.append(cid)  # sem info → mantém (paga o custo de checar)
        elif int(meta["weekday"]) == weekday:
            filtered.append(cid)
    return filtered or all_ids
