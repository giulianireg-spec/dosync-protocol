"""
DoSync — GPIO Adapter para Raspberry Pi 5
==========================================
Escucha el PIR HC-SR501 y el DHT22 y envía eventos al hub DoSync.

Uso:
    python3 gpio_adapter.py --hub http://192.168.100.X:47200 --token <token>

Variables de entorno (alternativa):
    DOSYNC_HUB_URL=http://192.168.100.X:47200
    DOSYNC_TOKEN=<token>
"""

import argparse
import asyncio
import json
import logging
import os
import time
import urllib.request
import urllib.error
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-20s %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("dosync.gpio")

# ── Config ────────────────────────────────────────────────────────────────────
HUB_URL   = os.environ.get("DOSYNC_HUB_URL", "http://192.168.100.1:47200")
HUB_TOKEN = os.environ.get("DOSYNC_TOKEN", "")

PIR_GPIO  = 17   # GPIO 17 — Pin 11
DHT_GPIO  = 4    # GPIO 4  — Pin 7

# Cooldown entre eventos del mismo tipo (segundos)
PIR_COOLDOWN = 10
DHT_INTERVAL = 30  # leer DHT cada 30 segundos


# ── HTTP helper ───────────────────────────────────────────────────────────────

def hub_post(path: str, body: dict) -> dict:
    """POST al hub DoSync."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {HUB_TOKEN}",
    }
    data = json.dumps(body).encode()
    req  = urllib.request.Request(
        HUB_URL + path, data=data, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        log.error("HTTP %s: %s", e.code, e.read().decode()[:200])
        return {"error": str(e)}
    except Exception as e:
        log.error("Hub error: %s", e)
        return {"error": str(e)}


def send_event(device_id: str, event_id: str, severity: str, data: dict = None):
    """Envía un evento de dispositivo al hub."""
    body = {
        "device_id": device_id,
        "event_id":  event_id,
        "severity":  severity,
        "data":      data or {},
    }
    result = hub_post("/v1/event", body)
    if "error" not in result:
        log.info("Event sent: %s → %s [%s]", device_id, event_id, severity)
    return result


def fire_intent(intent: str, urgency: str, context: dict = None):
    """Dispara un intent semántico en el hub."""
    body = {
        "intent":  intent,
        "urgency": urgency,
        "context": context or {},
    }
    result = hub_post("/v1/intent", body)
    if "error" not in result:
        actions = result.get("actions_taken", 0)
        log.info("Intent fired: %s [%s] → %d actions", intent, urgency, actions)
    return result


def register_devices():
    """Registra los dispositivos GPIO en el hub."""
    devices = [
        {
            "device_id":   "rpi-pir-01",
            "device_name": "PIR — Sensor de movimiento",
            "manufacturer": "DoSync GPIO",
            "model":       "HC-SR501",
            "firmware":    "1.0.0",
            "category":    "sensor",
            "tags":        ["sensor", "motion", "security", "emergency"],
            "sensors":     [{"id": "motion", "type": "motion_detected", "unit": "boolean"}],
            "actuators":   [],
            "emergency_capable": False,
            "cert_tier":   "basic",
        },
        {
            "device_id":   "rpi-dht22-01",
            "device_name": "DHT22 — Temperatura y Humedad",
            "manufacturer": "DoSync GPIO",
            "model":       "DHT22",
            "firmware":    "1.0.0",
            "category":    "sensor",
            "tags":        ["sensor", "climate", "temperature", "humidity"],
            "sensors":     [
                {"id": "temperature", "type": "temperature", "unit": "celsius"},
                {"id": "humidity",    "type": "humidity",    "unit": "percent"},
            ],
            "actuators":   [],
            "emergency_capable": False,
            "cert_tier":   "basic",
        },
    ]

    for device in devices:
        result = hub_post("/v1/devices/register", device)
        if "error" not in result:
            log.info("Registered: %s", device["device_id"])
        else:
            log.warning("Register failed for %s: %s",
                        device["device_id"], result["error"])


# ── PIR loop ──────────────────────────────────────────────────────────────────

async def pir_loop():
    """Escucha el PIR y dispara intents cuando detecta movimiento."""
    try:
        import lgpio
    except ImportError:
        log.error("lgpio not installed. Run: pip install lgpio")
        return

    h = lgpio.gpiochip_open(0)
    lgpio.gpio_claim_input(h, PIR_GPIO)
    log.info("PIR listener started on GPIO %d", PIR_GPIO)

    last_event  = 0
    was_moving  = False

    try:
        while True:
            val = lgpio.gpio_read(h, PIR_GPIO)
            now = time.time()

            if val and not was_moving:
                # Movimiento detectado — nuevo evento
                was_moving = True
                if now - last_event > PIR_COOLDOWN:
                    last_event = now
                    log.info("PIR: movimiento detectado")

                    # Enviar evento al hub
                    send_event(
                        device_id="rpi-pir-01",
                        event_id="motion_detected",
                        severity="info",
                        data={"timestamp": datetime.now().isoformat()},
                    )

                    # Disparar intent de presencia
                    fire_intent(
                        intent="ensure_safety",
                        urgency="emergency",
                        context={
                            "trigger":   "motion_detected",
                            "device_id": "rpi-pir-01",
                            "location":  "entrada",
                        },
                    )

            elif not val and was_moving:
                was_moving = False
                log.info("PIR: sin movimiento")

            await asyncio.sleep(0.2)

    finally:
        lgpio.gpiochip_close(h)


# ── DHT22 loop ────────────────────────────────────────────────────────────────

async def dht_loop():
    """Lee el DHT22 periódicamente y envía los datos al hub."""
    try:
        import board
        import adafruit_dht
    except ImportError:
        log.error("adafruit_dht not installed")
        return

    log.info("DHT22 reader started on GPIO %d (every %ds)", DHT_GPIO, DHT_INTERVAL)
    dht    = adafruit_dht.DHT22(board.D4, use_pulseio=False)
    errors = 0

    try:
        while True:
            try:
                temp = dht.temperature
                hum  = dht.humidity
                errors = 0

                log.info("DHT22: %.1f°C  %.1f%%", temp, hum)

                send_event(
                    device_id="rpi-dht22-01",
                    event_id="sensor_reading",
                    severity="info",
                    data={
                        "temperature": round(temp, 1),
                        "humidity":    round(hum,  1),
                        "timestamp":   datetime.now().isoformat(),
                    },
                )

                # Alerta si temperatura muy alta
                if temp > 35:
                    log.warning("DHT22: temperatura alta %.1f°C", temp)
                    fire_intent(
                        intent="alert_anomaly",
                        urgency="warning",
                        context={
                            "trigger":     "high_temperature",
                            "temperature": temp,
                            "device_id":   "rpi-dht22-01",
                        },
                    )

            except Exception as e:
                errors += 1
                log.warning("DHT22 read error (%d): %s", errors, e)

            await asyncio.sleep(DHT_INTERVAL)

    finally:
        dht.exit()


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    global HUB_URL, HUB_TOKEN
    parser = argparse.ArgumentParser(description="DoSync GPIO Adapter")
    parser.add_argument("--hub",   default=HUB_URL,   help="Hub URL")
    parser.add_argument("--token", default=HUB_TOKEN, help="API token")
    parser.add_argument("--no-dht", action="store_true", help="Skip DHT22")
    args = parser.parse_args()

    HUB_URL   = args.hub
    HUB_TOKEN = args.token

    log.info("DoSync GPIO Adapter")
    log.info("  Hub:   %s", HUB_URL)
    log.info("  PIR:   GPIO %d (Pin 11)", PIR_GPIO)
    log.info("  DHT22: GPIO %d (Pin 7)",  DHT_GPIO)

    # Registrar dispositivos
    log.info("Registering GPIO devices...")
    register_devices()

    # Arrancar loops
    tasks = [asyncio.create_task(pir_loop())]
    if not args.no_dht:
        tasks.append(asyncio.create_task(dht_loop()))

    log.info("GPIO adapter running. Ctrl+C to stop.")
    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        log.info("Stopping GPIO adapter...")
    finally:
        for task in tasks:
            task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
