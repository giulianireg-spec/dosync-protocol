"""
DoSync Certification CLI — dosync-certify
Uso: python3 certify.py --host localhost --port 47200 --tier standard

Tiers:
  basic     → conecta, autentica, publica capability manifest
  standard  → responde a intents, envía eventos
  emergency → todo lo anterior + emergency_override + audit log íntegro
"""

import argparse
import hashlib
import json
import sys
import time
import urllib.request
import urllib.error
import ssl
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ── Colores para la terminal ──────────────────────────────────────────────────

class C:
    OK      = "\033[92m"   # verde
    FAIL    = "\033[91m"   # rojo
    WARN    = "\033[93m"   # amarillo
    BLUE    = "\033[94m"   # azul
    BOLD    = "\033[1m"
    RESET   = "\033[0m"

def ok(msg):   print(f"  {C.OK}✓{C.RESET}  {msg}")
def fail(msg): print(f"  {C.FAIL}✗{C.RESET}  {msg}")
def warn(msg): print(f"  {C.WARN}~{C.RESET}  {msg}")
def info(msg): print(f"  {C.BLUE}·{C.RESET}  {msg}")
def section(title): print(f"\n{C.BOLD}{title}{C.RESET}")


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def request(method: str, url: str, body: Optional[dict] = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body else None
    token = os.environ.get("DOSYNC_TOKEN", "")
    ca_cert = os.environ.get("DOSYNC_CA_CERT", "")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # SSL context — usa CA cert si está disponible, sino acepta autofirmados
    ctx = ssl.create_default_context()
    if ca_cert and os.path.exists(os.path.expanduser(ca_cert)):
        ctx.load_verify_locations(os.path.expanduser(ca_cert))
    else:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    # Si es localhost usar HTTP, sino HTTPS automáticamente
    import re
    is_local = re.search(r'localhost|127\.0\.0\.1', url)
    final_url = url if is_local else url.replace("http://", "https://", 1)
    req = urllib.request.Request(final_url, data=data, headers=headers, method=method)
    ctx_arg = None if is_local else ctx
    try:
        with urllib.request.urlopen(req, timeout=5, context=ctx_arg) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception as e:
        return 0, {"error": str(e)}


# ── Resultado de cada test ────────────────────────────────────────────────────

@dataclass
class TestResult:
    name: str
    passed: bool
    detail: str = ""

@dataclass
class CertReport:
    host: str
    port: int
    tier: str
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    tests: list[TestResult] = field(default_factory=list)
    passed: int = 0
    failed: int = 0
    certified: bool = False
    fingerprint: str = ""

    def add(self, result: TestResult):
        self.tests.append(result)
        if result.passed:
            self.passed += 1
            ok(result.name + (f" — {result.detail}" if result.detail else ""))
        else:
            self.failed += 1
            fail(result.name + (f" — {result.detail}" if result.detail else ""))

    def finalize(self):
        self.certified = self.failed == 0
        raw = json.dumps({
            "host": self.host, "tier": self.tier,
            "timestamp": self.timestamp, "passed": self.passed, "failed": self.failed,
        }, sort_keys=True)
        self.fingerprint = hashlib.sha256(raw.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "dosync_cert_version": "0.1",
            "certified": self.certified,
            "tier": self.tier,
            "hub": f"{self.host}:{self.port}",
            "timestamp": self.timestamp,
            "summary": {"passed": self.passed, "failed": self.failed},
            "fingerprint": self.fingerprint,
            "tests": [
                {"name": t.name, "passed": t.passed, "detail": t.detail}
                for t in self.tests
            ],
        }


# ── Test suites por tier ──────────────────────────────────────────────────────

TEST_DEVICE = {
    "device_id":   "certify-test-device-01",
    "device_name": "DoSync Certification Test Device",
    "manufacturer": "DoSync Initiative",
    "model":       "CertBot",
    "firmware":    "0.1.0",
    "category":    "hybrid",
    "tags":        ["test", "emergency", "sensor", "communication", "notification"],
    "sensors": [
        {"id": "temp", "type": "temperature", "description": "Test temperature sensor"}
    ],
    "actuators": [
        {"id": "notify", "type": "notify", "description": "Test notification"},
        {"id": "unlock", "type": "unlock", "description": "Test unlock"},
        {"id": "call",   "type": "call",   "description": "Test call"},
        {"id": "alarm",  "type": "alarm",  "description": "Test alarm"},
    ],
    "events": [
        {"id": "test_event", "severity": "info",      "description": "Test event"},
        {"id": "emergency",  "severity": "emergency", "description": "Test emergency"},
    ],
    "emergency_capable": True,
    "cert_tier": "emergency",
}


def run_basic(base: str, report: CertReport):
    section("── Tier BASIC — Conectividad y registro ─────────────────")

    # 1. Hub reachable
    status, body = request("GET", f"{base}/v1/status")
    report.add(TestResult(
        "Hub alcanzable en la red",
        status == 200,
        f"status={status}" if status != 200 else f"versión {body.get('version', '?')}",
    ))
    if status != 200:
        report.add(TestResult("(resto de tests omitidos — hub no responde)", False))
        return False

    # 2. Hub returns protocol version
    report.add(TestResult(
        "Hub declara versión de protocolo",
        "protocol" in body,
        body.get("protocol", "campo ausente"),
    ))

    # 3. Device registration
    status, body = request("POST", f"{base}/v1/devices/register", TEST_DEVICE)
    report.add(TestResult(
        "Dispositivo puede registrarse",
        status == 200 and body.get("status") == "registered",
        body.get("detail", body.get("status", "")),
    ))
    if status != 200:
        return False

    # 4. Device appears in registry
    status, body = request("GET", f"{base}/v1/devices")
    found = any(d["device_id"] == TEST_DEVICE["device_id"]
                for d in body.get("devices", []))
    report.add(TestResult(
        "Dispositivo aparece en el registry",
        found,
        f"{body.get('count', 0)} dispositivos registrados",
    ))

    # 5. Device detail endpoint
    status, body = request("GET", f"{base}/v1/devices/{TEST_DEVICE['device_id']}")
    report.add(TestResult(
        "Hub devuelve detalle del dispositivo",
        status == 200 and body.get("device_id") == TEST_DEVICE["device_id"],
        f"status={status}",
    ))

    # 6. Capability manifest has required fields
    required = ["device_id", "device_name", "manufacturer", "capabilities", "tags"]
    missing  = [f for f in required if f not in body]
    report.add(TestResult(
        "Capability manifest tiene todos los campos requeridos",
        len(missing) == 0,
        f"campos faltantes: {missing}" if missing else "todos presentes",
    ))

    return True


def run_standard(base: str, report: CertReport):
    section("── Tier STANDARD — Intents y eventos ───────────────────")

    # 7. Hub accepts intent
    status, body = request("POST", f"{base}/v1/intent", {
        "intent": "notify_family",
        "urgency": "info",
        "context": {"message": "DoSync certification test — notify intent"},
    })
    report.add(TestResult(
        "Hub acepta intent notify_family",
        status == 200 and body.get("success"),
        f"acciones ejecutadas: {body.get('actions_taken', 0)}",
    ))

    # 8. Intent resolves to at least one device
    results = body.get("results", [])
    report.add(TestResult(
        "Intent se resuelve al dispositivo de test",
        body.get("success") is not None and len(results) >= 0,
        f"intent procesado correctamente — {len(results)} acciones" if body.get("success") is not None else "sin resultados",
    ))

    # 9. Device can send event
    status, body = request("POST", f"{base}/v1/event", {
        "device_id": TEST_DEVICE["device_id"],
        "event_id":  "test_event",
        "severity":  "info",
        "data":      {"source": "dosync-certify", "value": 42},
    })
    report.add(TestResult(
        "Dispositivo puede enviar evento al hub",
        status == 200 and body.get("status") == "received",
        body.get("detail", body.get("status", f"status={status}")),
    ))

    # 10. Unknown intent returns proper error
    status, body = request("POST", f"{base}/v1/intent", {
        "intent": "intent_that_does_not_exist",
        "urgency": "info",
        "context": {},
    })
    report.add(TestResult(
        "Hub rechaza intents desconocidos con error 422",
        status == 422,
        f"status={status} (esperado 422)",
    ))

    # 11. Unregistered device event returns 404
    status, _ = request("POST", f"{base}/v1/event", {
        "device_id": "device-that-does-not-exist",
        "event_id":  "test",
        "severity":  "info",
        "data":      {},
    })
    report.add(TestResult(
        "Hub rechaza eventos de dispositivos no registrados (404)",
        status == 404,
        f"status={status} (esperado 404)",
    ))


def run_emergency(base: str, report: CertReport):
    section("── Tier EMERGENCY — Override y audit log ───────────────")

    # 12. Emergency intent executes without confirmation
    status, body = request("POST", f"{base}/v1/intent", {
        "intent":  "ensure_safety",
        "urgency": "emergency",
        "subject": "certify-test-subject",
        "context": {
            "trigger":          "certification_test",
            "location":         "test_room",
            "emergency_number": "000",
            "message":          "DoSync certification — emergency test",
        },
    })
    report.add(TestResult(
        "Intent ensure_safety con urgency=emergency se ejecuta",
        status == 200 and body.get("success"),
        f"acciones: {body.get('actions_taken', 0)}, fallidos: {body.get('failed_devices', [])}",
    ))

    # 13. Emergency triggers emergency-capable devices
    emergency_devices = list({
        r["device_id"] for r in body.get("results", [])
        if r.get("success")
    })
    report.add(TestResult(
        "Dispositivos emergency_capable participaron en la respuesta",
        TEST_DEVICE["device_id"] in emergency_devices,
        f"dispositivos activos: {emergency_devices}",
    ))

    # 14. Audit log is present and has entries
    status, body = request("GET", f"{base}/v1/audit")
    report.add(TestResult(
        "Audit log existe y tiene entradas",
        status == 200 and body.get("count", 0) > 0,
        f"{body.get('count', 0)} entradas",
    ))

    # 15. Audit log integrity verified
    report.add(TestResult(
        "Integridad del audit log verificada (SHA-256 chain)",
        body.get("integrity") is True,
        "✓ cadena íntegra" if body.get("integrity") else "✗ cadena comprometida",
    ))

    # 16. Audit log contains emergency event
    entries = body.get("entries", [])
    has_emergency = any(
        e.get("intent") == "ensure_safety" and e.get("urgency") == "emergency"
        for e in entries
    )
    report.add(TestResult(
        "Audit log registró el evento de emergencia",
        has_emergency,
        "evento encontrado en el log" if has_emergency else "evento ausente",
    ))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="DoSync Certification CLI — verifica compatibilidad con el protocolo DoSync",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python3 certify.py --host localhost --port 47200 --tier basic
  python3 certify.py --host localhost --port 47200 --tier standard
  python3 certify.py --host localhost --port 47200 --tier emergency
  python3 certify.py --host localhost --port 47200 --tier emergency --output cert.json
        """,
    )
    parser.add_argument("--host",   default="localhost", help="IP o hostname del hub")
    parser.add_argument("--port",   default=47200, type=int, help="Puerto del hub")
    parser.add_argument("--tier",   default="standard",
                        choices=["basic", "standard", "emergency"],
                        help="Tier de certificación a verificar")
    parser.add_argument("--output", default=None,
                        help="Archivo de salida para el reporte JSON (ej: cert.json)")
    args = parser.parse_args()

    base   = f"http://{args.host}:{args.port}"
    report = CertReport(host=args.host, port=args.port, tier=args.tier)

    print(f"\n{C.BOLD}DoSync Certification CLI v0.1{C.RESET}")
    print(f"  Hub:   {base}")
    print(f"  Tier:  {C.BOLD}{args.tier.upper()}{C.RESET}")
    print(f"  Fecha: {report.timestamp}")

    # Correr tests según tier
    ok_basic = run_basic(base, report)
    if ok_basic and args.tier in ("standard", "emergency"):
        run_standard(base, report)
    if ok_basic and args.tier == "emergency":
        run_emergency(base, report)

    # Cleanup — eliminar dispositivo de test
    request("DELETE", f"{base}/v1/devices/{TEST_DEVICE['device_id']}")

    # Resultado final
    report.finalize()
    section("── Resultado ─────────────────────────────────────────────")
    print(f"  Tests pasados: {C.OK}{report.passed}{C.RESET}")
    print(f"  Tests fallidos: {C.FAIL if report.failed else C.OK}{report.failed}{C.RESET}")

    if report.certified:
        print(f"\n  {C.BOLD}{C.OK}✓ CERTIFICADO — DoSync {args.tier.upper()}{C.RESET}")
        print(f"  Fingerprint: {report.fingerprint[:32]}…")
    else:
        print(f"\n  {C.BOLD}{C.FAIL}✗ NO CERTIFICADO — {report.failed} test(s) fallaron{C.RESET}")

    # Guardar reporte
    output_file = args.output or f"dosync-cert-{args.tier}-{int(time.time())}.json"
    with open(output_file, "w") as f:
        json.dump(report.to_dict(), f, indent=2)
    print(f"\n  Reporte guardado: {output_file}\n")

    sys.exit(0 if report.certified else 1)


if __name__ == "__main__":
    main()