import asyncio
import json
import logging
from typing import Callable, Awaitable

import aio_pika

from app.config import settings

logger = logging.getLogger(__name__)


async def publish(message: dict) -> None:
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        await channel.declare_queue(settings.RABBITMQ_QUEUE, durable=True)
        await channel.default_exchange.publish(
            aio_pika.Message(
                body=json.dumps(message).encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=settings.RABBITMQ_QUEUE,
        )
        logger.info("Mensagem publicada na fila %s", settings.RABBITMQ_QUEUE)


async def consume(callback: Callable[[dict], Awaitable[None]]) -> None:
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    channel = await connection.channel()
    # prefetch_count alto + despacho concorrente: sem isso, o debounce Redis
    # nunca agrupa msgs do mesmo lead, porque cada callback (sleep + Gemini)
    # segura o iterator por ~40s antes da próxima msg ser consumida.
    await channel.set_qos(prefetch_count=settings.RABBITMQ_PREFETCH)
    queue = await channel.declare_queue(settings.RABBITMQ_QUEUE, durable=True)

    logger.info(
        "Consumindo fila %s (prefetch=%d) ...",
        settings.RABBITMQ_QUEUE, settings.RABBITMQ_PREFETCH,
    )

    async def _handle(message: aio_pika.IncomingMessage) -> None:
        async with message.process():
            try:
                body = json.loads(message.body.decode())
                logger.info("Mensagem recebida da fila: %s", str(body)[:200])
                await callback(body)
            except Exception:
                logger.exception("Erro ao processar mensagem da fila")

    async with queue.iterator() as queue_iter:
        async for message in queue_iter:
            asyncio.create_task(_handle(message))
