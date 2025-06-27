import redis.asyncio as redis
from redis.exceptions import RedisError, ConnectionError,TimeoutError
from loguru import logger

#配置 redis 连接池

redis_pool = redis.ConnectionPool(
    host="localhost",
    port=6379,
    db=0,
    #password="your_password", # 如果有密码，请取消注释并填写密码
    decode_responses=True,
    max_connections=100,
    timeout=30,
    encoding="utf-8",
    encoding_errors="strict",
    socket_keepalive=True,
    socket_timeout=30,
    retry_on_timeout=True,
    retry_on_timeout_count=3,
    retry_on_timeout_delay=1,
)

async def init_redis():
    try:
        conn = redis.Redis(connection_pool=redis_pool)
        sig = await conn.ping() # 检查连接是否成功
        if sig:
            logger.info("Redis connection successful")
        return conn
    except (ConnectionError, TimeoutError, RedisError) as e:
        logger.error(f"Redis connection error: {e}")
        return None
    except Exception as e:
        logger.error(f"Redis connection error: {e}")
        return None