# Base Ingester (Optional)
class BaseIngester:
    def __init__(self, config, symbol, queue): pass
    async def connect(self): raise NotImplementedError
    async def run(self): raise NotImplementedError
    async def close(self): raise NotImplementedError
