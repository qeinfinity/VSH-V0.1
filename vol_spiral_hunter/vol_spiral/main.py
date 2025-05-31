# Vol-Spiral Hunter - Main Application Entry Point
import asyncio
import logging
from logging import Handler
from typing import List

from colorama import Fore, Style, init as colorama_init

from .config_loader import load_config, load_dot_env
from .ingest import binance_ingester, deribit_ingester
from .process import metrics_calculator
from .storage import influx_writer
from .alert import rules_stubs
from .utils import ntp_checker


class ColorFormatter(logging.Formatter):
    COLOR_MAP = {
        logging.DEBUG: Fore.CYAN,
        logging.INFO: Fore.GREEN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.MAGENTA,
    }

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        color = self.COLOR_MAP.get(record.levelno, "")
        return f"{color}{message}{Style.RESET_ALL}"

logger = logging.getLogger(__name__)

async def main_async():
    # Load environment variables
    load_dot_env()
    # Load configuration
    config = load_config('config.ini')

    # Setup logging
    log_level = getattr(logging, config.get('system', 'log_level', fallback='INFO').upper(), logging.INFO)
    use_color = config.getboolean('system', 'color_logging', fallback=False)

    console_handler = logging.StreamHandler()
    file_handler = logging.FileHandler('vol_spiral_hunter.log')

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    if use_color:
        colorama_init()
        console_handler.setFormatter(ColorFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    else:
        console_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)

    logging.basicConfig(level=log_level, handlers=[console_handler, file_handler])

    logger.info('Starting Vol-Spiral Hunter V0.1...')

    # NTP Check
    if not await ntp_checker.check_ntp_offset(config):
        logger.critical('NTP check failed. Exiting.')
        return

    # --- Initialize Queues ---
    # Example: binance_book_queue = asyncio.Queue(maxsize=config.getint('system', 'ws_queue_size'))

    # --- Initialize Modules ---
    # influx_db = influx_writer.InfluxWriter(config)
    # await influx_db.connect()

    # --- Start Tasks ---
    # tasks = [
    #    asyncio.create_task(binance_ingester.run(config, binance_book_queue, ...)),
    #    asyncio.create_task(metrics_calculator.run(config, binance_book_queue, influx_db, ...)),
    # ]
    # await asyncio.gather(*tasks)
    logger.info('Vol-Spiral Hunter V0.1 stopped.')

def run_main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print('Application interrupted. Exiting...')

if __name__ == '__main__':
    run_main()
