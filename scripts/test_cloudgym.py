"""
Teste manual e discovery dos endpoints da CloudGym.

Valida os 4 fluxos cobertos pelo agente n8n `agenda horario seven.json`:
  1. busca_cliente           (GET v2 /v1/member?phone=+55...)
  2. busca_disponibilidade   (GET v1 /admin/classattendancelist/{unit}/{date}/{class_id})
  3. cadastra_cliente        (POST v1 /customer)
  4. cria_agendamento        (POST /v1/classattendance — testa host v1 e v2)

Como usar:
  cd clientes/seven
  python -m scripts.test_cloudgym --phone 5541998765432 --nome "TESTE ZOE" --data 2026-04-20

Flags:
  --phone     Telefone do lead de teste (DDI+DDD+numero, só dígitos).
  --nome      Nome completo a usar em create_customer.
  --data      Data yyyy-MM-dd para busca_disponibilidade / cria_agendamento.
  --modalidade Modalidade para discovery/agendamento (default: muay_thai).
  --only-discovery  Só roda discovery (busca_disponibilidade), pula cadastro+agendamento.
  --no-attend       Roda cadastro mas NÃO cria agendamento.

Saída: imprime cada passo + grava `scripts/class_discovery.json` com
{class_id: {modalidade, startTime, weekday}}.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# garante importar do pacote raiz
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.data import class_catalog as catalog  # noqa: E402
from app.services import cloudgym  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("test_cloudgym")

DISCOVERY_PATH = ROOT / "scripts" / "class_discovery.json"


def banner(msg: str) -> None:
    print("\n" + "=" * 72)
    print(f"  {msg}")
    print("=" * 72)


async def test_tokens() -> None:
    banner("1. AUTH — tokens v1 e v2")
    v1 = await cloudgym._get_v1_token()
    v2 = await cloudgym._get_v2_token()
    print(f"[v1] token (prefixo): {v1[:30]}... len={len(v1)}")
    print(f"[v2] token (prefixo): {v2[:30]}... len={len(v2)}")
    if not v1 or not v2:
        raise RuntimeError("Falha ao obter tokens.")


async def test_find_member(phone: str) -> dict | None:
    banner(f"2. busca_cliente por telefone {phone}")
    formatted = cloudgym.format_phone_br(phone)
    print(f"[+] query phone={formatted}")
    members = await cloudgym.find_member_by_phone(phone)
    print(f"[+] retorno: {len(members)} membro(s)")
    for m in members[:3]:
        mid = m.get("memberid") or m.get("memberId") or m.get("id")
        plan = m.get("plan") or m.get("planId")
        nome = m.get("name") or m.get("fullName")
        print(f"   memberid={mid}  plan={plan}  nome={nome}")
    return members[0] if members else None


async def test_availability(data_str: str, modalidade: str) -> list[dict]:
    banner(f"3. busca_disponibilidade — modalidade={modalidade}  data={data_str}")
    ids = catalog.ids_for_modality(modalidade)
    if not ids:
        print(f"[!] Nenhum class_id no catálogo para '{modalidade}'")
        return []
    print(f"[+] testando {len(ids)} IDs em paralelo")
    results = await asyncio.gather(
        *[cloudgym.get_class_availability(data_str, str(cid)) for cid in ids],
        return_exceptions=True,
    )
    encontrados: list[dict] = []
    for cid, r in zip(ids, results):
        if isinstance(r, Exception):
            print(f"   {cid}: erro {r}")
            continue
        items = r if isinstance(r, list) else r.get("items") or []
        if not items:
            continue
        first = items[0]
        hora = first.get("startTime") or first.get("hora") or "?"
        vagas = first.get("availableSlots") or first.get("vagas")
        print(f"   {cid}: startTime={hora}  vagas={vagas}")
        encontrados.append({"class_id": cid, "startTime": hora, "vagas": vagas})
    return encontrados


def save_discovery(found: list[dict], modalidade: str, data_str: str) -> None:
    if not found:
        return
    current: dict[str, dict] = {}
    if DISCOVERY_PATH.exists():
        try:
            current = json.loads(DISCOVERY_PATH.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    weekday = datetime.strptime(data_str, "%Y-%m-%d").weekday()
    for f in found:
        cid = str(f["class_id"])
        entry = current.get(cid, {})
        entry["modalidade"] = modalidade
        entry["startTime"] = f["startTime"]
        # grava set de weekdays encontrados
        wds = set(entry.get("weekdays", []))
        wds.add(weekday)
        entry["weekdays"] = sorted(wds)
        # compat: se só 1 weekday, expõe como int em "weekday"
        entry["weekday"] = weekday if len(wds) == 1 else None
        current[cid] = entry
    DISCOVERY_PATH.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[+] discovery salvo em {DISCOVERY_PATH} ({len(current)} IDs mapeados)")


async def test_create_customer(nome: str, phone: str) -> str | None:
    banner(f"4. cadastra_cliente — nome='{nome}' phone={phone}")
    try:
        result = await cloudgym.create_customer(nome, phone)
        print(f"[+] resposta: {json.dumps(result, indent=2, ensure_ascii=False)[:800]}")
        mid = result.get("memberid") or result.get("memberId") or result.get("id")
        return str(mid) if mid else None
    except Exception as e:
        print(f"[!] erro: {e}")
        return None


async def _try_attend(host_base: str, memberid: int, data_str: str, class_id: int) -> dict:
    import httpx

    token = await cloudgym._get_v1_token()
    url = f"{host_base}/v1/classattendance"
    headers = {"accept": "application/json", "Authorization": f"Bearer {token}"}
    payload = {"memberid": int(memberid), "date": data_str, "class_id": int(class_id)}
    client = cloudgym._get_client()
    print(f"   POST {url} payload={payload}")
    resp = await client.post(url, headers=headers, json=payload)
    print(f"   -> {resp.status_code}  body[:200]={resp.text[:200]}")
    return {"status": resp.status_code, "body": resp.text, "host": host_base}


async def test_create_attendance(memberid: str, data_str: str, class_id: int) -> None:
    banner(f"5. cria_agendamento — memberid={memberid} data={data_str} class_id={class_id}")
    print("[+] Testando host v1 e host v2 com mesmo payload {memberid, date, class_id}")
    hosts = [settings.CLOUDGYM_V2_BASE, settings.CLOUDGYM_V1_BASE]
    results = []
    for h in hosts:
        try:
            r = await _try_attend(h, int(memberid), data_str, class_id)
            results.append(r)
            if 200 <= r["status"] < 300:
                print(f"[✓] Host vencedor: {h}")
                print(f"    Atualize .env: CLOUDGYM_ATTENDANCE_BASE={h}")
                return
        except Exception as e:
            print(f"[!] Falha em {h}: {e}")
            results.append({"host": h, "error": str(e)})
    print("[✗] Nenhum host retornou 2xx. Ver respostas acima.")


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--phone", required=True, help="DDI+DDD+numero (só dígitos)")
    p.add_argument("--nome", default="TESTE ZOE")
    p.add_argument("--data", required=True, help="yyyy-MM-dd")
    p.add_argument("--modalidade", default="muay_thai")
    p.add_argument("--only-discovery", action="store_true")
    p.add_argument("--no-attend", action="store_true")
    args = p.parse_args()

    await test_tokens()

    member = await test_find_member(args.phone)

    found = await test_availability(args.data, args.modalidade)
    save_discovery(found, catalog.resolve_modality(args.modalidade) or args.modalidade, args.data)

    if args.only_discovery:
        print("\n[only-discovery] encerrando.")
        return

    memberid = None
    if member:
        memberid = member.get("memberid") or member.get("memberId") or member.get("id")
        print(f"\n[+] Cliente já existe (memberid={memberid}) — pulando cadastro.")
    else:
        memberid = await test_create_customer(args.nome, args.phone)

    if args.no_attend:
        print("\n[no-attend] agendamento pulado.")
        return

    if not memberid or not found:
        print("\n[!] Sem memberid ou sem class_id disponível — não tentando agendamento.")
        return

    class_id = found[0]["class_id"]
    await test_create_attendance(str(memberid), args.data, class_id)


if __name__ == "__main__":
    asyncio.run(main())
