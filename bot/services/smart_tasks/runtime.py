"""在机器人循环运行 AI；结果与投递安排一次性原子封存。"""
import asyncio

from loguru import logger

from bot.services.admin_push import PushError
from bot.services.admin_push.content import compile_plan
from bot.services.mcp_client import MCPClientError
from bot.services.text_generation import TextGenerationError
from .execution import generate, SKIP_MARKER

JOB_ID = 'smart_task_dispatch'


class SmartTaskRuntime:
    def __init__(self, service, ai, scheduler=None, generator=generate):
        self.service, self.store = service, service.store
        self.ai, self.scheduler, self.generator = ai, scheduler, generator
        self.loop = None
        self._accepting = False
        self._workers = set()
        self._tick_task = None
        self._tick_lock = asyncio.Lock()
        self._last_cleanup = 0
        service.runtime = self

    @property
    def ready(self):
        return bool(self._accepting and self.loop and self.loop.is_running())

    async def start(self):
        if self.ready:
            return
        self.loop = asyncio.get_running_loop()
        self.store.recover()
        self._accepting = True
        if self.scheduler:
            self.scheduler.add_job(self.tick, 'interval', seconds=1, id=JOB_ID, replace_existing=True, max_instances=1, coalesce=True)
        await self.tick()

    def wake(self):
        if self.ready:
            self.loop.call_soon_threadsafe(self._schedule_tick)

    def _schedule_tick(self):
        if self.ready and (self._tick_task is None or self._tick_task.done()):
            self._tick_task = self.loop.create_task(self.tick())
            self._tick_task.add_done_callback(self._observe)

    @staticmethod
    def _observe(task):
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(f'智能任务运行异常，已保留持久日志: {type(exc).__name__}')

    def _done(self, task):
        self._workers.discard(task)
        self._observe(task)
        self._schedule_tick()

    async def tick(self):
        if not self.ready or self._tick_lock.locked():
            return
        async with self._tick_lock:
            self.store.sync_deliveries()
            self.store.schedule_due()
            for _ in range(max(0, 2 - len(self._workers))):
                run = self.store.claim()
                if run is None:
                    break
                task = self.loop.create_task(self._execute(run))
                self._workers.add(task)
                task.add_done_callback(self._done)
            if self.service.clock() - self._last_cleanup >= 60:
                self.store.cleanup()
                self._last_cleanup = self.service.clock()
            self.service.push._wake()

    async def _generate_result(self, run):
        text, images = await self.generator(run, self.ai, self.store, self.service.clock)
        if text.strip() == SKIP_MARKER:
            return text, None, None, True
        if run['snapshot'].get('send_images', True) is False:
            images = []
        asset_ids = []
        for image in images[:4]:
            asset = await self.service.push.assets.add_generated(image)
            asset_ids.append(asset['id'])
        targets = run['snapshot']['targets'] if run['mode'] != 'test' else ([run['test_target']] if run['test_target'] else [])
        composition = {'text': text, 'asset_ids': asset_ids, 'targets': targets, 'settings': {}}
        return text, composition, compile_plan(text, asset_ids), False

    async def _execute(self, run):
        try:
            result = await asyncio.wait_for(self._generate_result(run), timeout=run['snapshot']['limits']['timeout_seconds'])
        except asyncio.CancelledError:
            self.store.fail(run['id'], '服务停止导致运行中断，不自动重跑。', interrupted=True)
            raise
        except asyncio.TimeoutError:
            self.store.fail(run['id'], '运行超时，未自动重试。')
        except (PushError, TextGenerationError) as exc:
            self.store.fail(run['id'], str(exc))
        except MCPClientError:
            self.store.fail(run['id'], 'MCP 服务器不可用或后台权限已撤销，未自动重试。')
        except Exception as exc:
            logger.warning(f'智能任务失败: {type(exc).__name__}')
            self.store.fail(run['id'], '运行失败，请检查模型、工具与配图配置。未自动重试。')
        else:
            # Do not catch commit errors and then claim failure: the transaction is
            # the handoff boundary; recovery must decide from the durable state.
            self.store.complete(run['id'], *result)
        finally:
            self.service.push._wake()

    async def stop(self):
        self._accepting = False
        if self.scheduler:
            try:
                self.scheduler.remove_job(JOB_ID)
            except Exception:
                pass
        if self._tick_task:
            await asyncio.gather(self._tick_task, return_exceptions=True)
        workers = list(self._workers)
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        self.loop = None
