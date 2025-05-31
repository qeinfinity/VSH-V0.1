# InfluxDB Writer
import asyncio
import logging
from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync

logger = logging.getLogger(__name__)

class InfluxWriter:
    def __init__(self, config):
        self.url = config.get('influxdb', 'url')
        self.token = config.get('influxdb', 'token')
        self.org = config.get('influxdb', 'org')
        self.bucket = config.get('influxdb', 'bucket')
        self.flush_interval_ms = config.getint('influxdb', 'flush_interval_ms', fallback=500)
        self.client: InfluxDBClientAsync | None = None
        self.write_api = None

    async def connect(self):
        logger.info(f'Connecting to InfluxDB at {self.url}')
        self.client = InfluxDBClientAsync(url=self.url, token=self.token, org=self.org)
        self.write_api = self.client.write_api()
        logger.info('Connected to InfluxDB.')

    async def write_data_loop(self, input_storage_queue: asyncio.Queue):
        logger.info('InfluxDB writer loop starting...')
        if not self.write_api:
            raise RuntimeError('InfluxWriter.connect() must be called before start')

        flush_interval = self.flush_interval_ms / 1000.0
        batch = []

        while True:
            try:
                item = await asyncio.wait_for(input_storage_queue.get(), timeout=flush_interval)
            except asyncio.TimeoutError:
                item = None

            if item is None:
                if batch:
                    await self.write_api.write(bucket=self.bucket, record=batch)
                    batch.clear()
                continue

            if item == 'STOP':
                break

            batch.append(item)

            if len(batch) >= 500:
                await self.write_api.write(bucket=self.bucket, record=batch)
                batch.clear()

        if batch:
            await self.write_api.write(bucket=self.bucket, record=batch)

    async def close(self):
        if self.write_api:
            logger.info('Flushing remaining data to InfluxDB...')
            await self.write_api.close()
        if self.client:
            logger.info('Closing InfluxDB client...')
            await self.client.close()
        logger.info('InfluxDB connection closed.')
