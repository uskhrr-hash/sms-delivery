from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.worker.sender import has_queued_messages, human_delay_seconds, process_one_message

logger = logging.getLogger(__name__)


async def worker_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    logger.info(
        'Worker started (delay %s–%s sec between consecutive SMS)',
        settings.send_delay_min,
        settings.send_delay_max,
    )
    chain_active = False

    while not stop_event.is_set():
        try:
            if not has_queued_messages():
                chain_active = False
                await asyncio.sleep(settings.worker_poll_sec)
                continue

            # Пауза только перед 2-й, 3-й… СМС подряд (после простоя — сразу)
            if chain_active:
                delay = human_delay_seconds()
                logger.info('Pause %.0f sec before next SMS in chain', delay)
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=delay)
                    break
                except asyncio.TimeoutError:
                    pass

            worked = await process_one_message()
            if worked:
                chain_active = has_queued_messages()
            else:
                chain_active = False
                await asyncio.sleep(settings.worker_poll_sec)
        except Exception:
            logger.exception('Worker error')
            chain_active = False
            await asyncio.sleep(settings.worker_poll_sec)
