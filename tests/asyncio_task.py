import asyncio
import time

async def call_api(name:str,delay:float):
    print(f"Calling API {name} with delay {delay}")
    await asyncio.sleep(delay)
    print(f"API {name} called")

async def main():
    time_1 = time.perf_counter()
    print("start A coroutine")
    # 加入到事件循环中
    task_1 = asyncio.create_task(call_api("A",3))

    print("start B coroutine")
    task_2 = asyncio.create_task(call_api("B",5))

    # await task_1
    print("end A coroutine")
    # await task_2
    print("end B coroutine")

    time_2 = time.perf_counter()
    print(f"Time taken: {time_2-time_1}")

asyncio.run(main())