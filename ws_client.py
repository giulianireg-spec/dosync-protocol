"""
DoSync — WebSocket test client
Escucha eventos en tiempo real del hub.

Uso:
    PYTHONPATH=. python3 ws_client.py --token <tu-token>
    PYTHONPATH=. python3 ws_client.py  # si auth está deshabilitado
"""
import argparse
import asyncio
import json
import sys

try:
    import websockets
except ImportError:
    print("Install: pip install websockets")
    sys.exit(1)


class C:
    GREEN  = "\033[92m"
    RED    = "\033[91m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    TEAL   = "\033[96m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RESET  = "\033[0m"

EVENT_COLORS = {
    "device_event":     C.TEAL,
    "intent_executed":  C.GREEN,
    "phase_executed":   C.YELLOW,
    "presence_updated": C.BLUE,
    "connected":        C.BOLD,
    "ping":             C.DIM,
}

async def listen(host: str, port: int, token: str):
    url = f"ws://{host}:{port}/ws"
    if token:
        url += f"?token={token}"

    print(f"\n{C.BOLD}DoSync WebSocket Client{C.RESET}")
    print(f"  Connecting to {url}...")
    print(f"  Press Ctrl+C to disconnect\n")

    try:
        async with websockets.connect(url) as ws:
            async for message in ws:
                event = json.loads(message)
                etype = event.get("type", "?")
                data  = event.get("data", {})

                if etype == "ping":
                    continue  # silenciar pings

                color = EVENT_COLORS.get(etype, C.RESET)
                print(f"  {color}▶ {etype}{C.RESET}")

                if etype == "connected":
                    print(f"    {C.DIM}devices={data.get('devices')} "
                          f"protocol={data.get('protocol')}{C.RESET}")
                elif etype == "device_event":
                    print(f"    {C.DIM}device={data.get('device_id')} "
                          f"event={data.get('event_id')} "
                          f"[{data.get('severity')}]{C.RESET}")
                elif etype == "intent_executed":
                    success = data.get('success', False)
                    icon = "✓" if success else "✗"
                    print(f"    {icon} intent={data.get('intent')} "
                          f"[{data.get('urgency')}] "
                          f"actions={data.get('actions')}")
                elif etype == "presence_updated":
                    print(f"    {C.DIM}occupied={data.get('occupied')} "
                          f"confidence={data.get('occ_confidence', 0):.0%}{C.RESET}")
                else:
                    print(f"    {C.DIM}{json.dumps(data)[:80]}{C.RESET}")

    except websockets.exceptions.ConnectionClosedError as e:
        if e.code == 4001:
            print(f"\n  {C.RED}✗ Unauthorized — provide a valid token with --token{C.RESET}\n")
        else:
            print(f"\n  {C.RED}✗ Connection closed: {e}{C.RESET}\n")
    except ConnectionRefusedError:
        print(f"\n  {C.RED}✗ Cannot connect — is the hub running?{C.RESET}\n")
    except KeyboardInterrupt:
        print(f"\n  {C.DIM}Disconnected.{C.RESET}\n")


def main():
    parser = argparse.ArgumentParser(
        description="DoSync WebSocket client — listens for real-time events"
    )
    parser.add_argument("--host",  default="localhost")
    parser.add_argument("--port",  default=47200, type=int)
    parser.add_argument("--token", default="", help="API key token")
    args = parser.parse_args()
    asyncio.run(listen(args.host, args.port, args.token))

if __name__ == "__main__":
    main()
