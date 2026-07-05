from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.worker.sender import human_delay_seconds, process_one_message

logger = logging.getLogger(__name__)


async def worker_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    logger.info(
        'Worker started (delay %s–%s sec between SMS)',
        settings.send_delay_min,
        settings.send_delay_max,
    )
    while not stop_event.is_set():
        try:
            worked = await process_one_message()
            if worked:
                delay = human_delay_seconds()
                logger.info('Pause %.0f sec before next SMS', delay)
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=delay)
                    break
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(settings.worker_poll_sec)
        except Exception:
            logger.exception('Worker error')
            await asyncio.sleep(settings.worker_poll_sec)
