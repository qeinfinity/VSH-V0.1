# Utility Functions
import asyncio
import logging
import ntplib # Ensure this is in requirements.txt

logger = logging.getLogger(__name__)

async def check_ntp_offset(config, retries=3, timeout=1):
    ntp_client = ntplib.NTPClient()
    max_offset_ms = config.getfloat('system', 'ntp_max_offset_ms', fallback=100.0)
    ntp_server = 'pool.ntp.org' # Or choose a specific one

    for attempt in range(retries):
        try:
            logger.info(f'Checking NTP time offset against {ntp_server} (attempt {attempt + 1}/{retries})...')
            response = await asyncio.to_thread(ntp_client.request, ntp_server, version=3, timeout=timeout)
            offset_ms = abs(response.offset * 1000) # offset is in seconds
            logger.info(f'NTP offset: {offset_ms:.2f} ms.')
            if offset_ms <= max_offset_ms:
                return True
            else:
                logger.warning(f'NTP offset {offset_ms:.2f} ms exceeds threshold of {max_offset_ms:.2f} ms.')
        except ntplib.NTPException as e:
            logger.error(f'NTP request failed (attempt {attempt + 1}/{retries}): {e}')
        except Exception as e:
            logger.error(f'Unexpected error during NTP check (attempt {attempt + 1}/{retries}): {e}')
        if attempt < retries - 1:
            await asyncio.sleep(1) # Wait before retrying
    return False
