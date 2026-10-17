"""Read-only activation probe. Prints status/counts, never credentials or account balances.

Run from backend: .venv/bin/python tools/check_exness.py
"""
from __future__ import annotations
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import load_settings
from app.market.exness import ExnessClient, ExnessError, timestamp


async def probe(client, instrument):
    client.configure_limits()
    if instrument not in client.instruments():
        raise ExnessError("Configured instrument is unavailable to this API account.")
    details=client.conditions(instrument)
    rows=client.candles(instrument,'M1',start=int(time.time()),count=-min(10,client.max_candles))
    print(json.dumps({'history_received':len(rows),'point_digits':details.get('point_digits'),
                      'forming_bar':bool(rows and rows[-1][-1]==0)}))
    async with client.ticks_connection() as ws:
        await ws.send(json.dumps({'id':'read-only-probe','subscribe':{'event':'ticks','instruments':[instrument]}}))
        async with asyncio.timeout(30):
            async for raw in ws:
                tick=json.loads(raw)
                if isinstance(tick,dict) and tick.get('instrument')==instrument and 'timestamp' in tick:
                    print(json.dumps({'tick_received':True,'tick_age_seconds':round(time.time()-timestamp(tick['timestamp']),1)}))
                    return
                if isinstance(tick,dict) and ('error' in tick or 'error_message' in tick):
                    raise ExnessError('Tick subscription rejected; check API permissions.')


def main():
    settings=load_settings()
    if not settings.exness_enabled:
        print(json.dumps({'status':'needs_setup','missing':settings.exness_missing}))
        return 2
    client=None
    try:
        key=settings.exness_private_key_file.read_bytes() if settings.exness_private_key_file else settings.exness_private_key.encode()
        client=ExnessClient(settings.exness_base_url,settings.exness_api_key,key,settings.exness_account_id,
                            alignment=settings.exness_alignment,price=settings.exness_price_type)
        asyncio.run(probe(client,settings.exness_instruments[0]))
        print(json.dumps({'status':'history_and_tick_probe_passed'}))
        return 0
    except ExnessError as exc:
        print(json.dumps({'status':'failed','error':str(exc)}))
        return 1
    except TimeoutError:
        print(json.dumps({'status':'tick_timeout','message':'No tick within 30 seconds; check market session and permissions.'}))
        return 1
    except Exception:
        print(json.dumps({'status':'failed','message':'Connection or local key-file failure; no credentials printed.'}))
        return 1
    finally:
        if client:client.close()


if __name__=='__main__':
    raise SystemExit(main())
