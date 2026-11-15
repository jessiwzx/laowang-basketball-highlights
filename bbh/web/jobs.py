"""长任务注册表：检测 / 回扫溯源 / 生成集锦统一成 job。

前端 POST 启动拿 job_id，之后 0.8~1s GET /api/jobs/{id} 轮询（不引 SSE：
断线刷新后还能接着看，实现也最简）。job 只在内存里——本机单用户工具，
重启丢了重跑一次即可。

同类任务同时只允许一个（旧界面同样是「已有检测在跑」就拒绝），第二次
启动抛 JobBusy，路由层翻成 409。
"""
import threading
import time
import uuid
from dataclasses import dataclass, field


class JobBusy(Exception):
    """同类任务已在跑——路由层翻成 409。"""


@dataclass
class Job:
    id: str
    kind: str
    state: str = "running"           # running | done | error
    pct: int = 0
    msg: str = ""
    found: int | None = None         # 已发现球数（检测/回扫进度用）
    error: str | None = None
    result: object = None
    started: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "state": self.state,
                "pct": self.pct, "msg": self.msg, "found": self.found,
                "error": self.error, "done": self.state != "running"}


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._running: dict[str, str] = {}      # kind → job_id（单飞）
        self._lock = threading.Lock()

    def start(self, kind: str, worker) -> Job:
        """worker(report) 在后台线程里跑；返回值进 job.result，异常进
        job.error。report(msg=, pct=, found=) 只覆盖给了值的字段。"""
        with self._lock:
            if kind in self._running:
                raise JobBusy(f"{kind} 已在跑——等它结束或刷新页面看进度")
            job = Job(id=uuid.uuid4().hex[:12], kind=kind)
            self._jobs[job.id] = job
            self._running[kind] = job.id

        def report(msg=None, pct=None, found=None):
            if msg is not None:
                job.msg = msg
            if pct is not None:
                job.pct = int(pct)
            if found is not None:
                job.found = int(found)

        def run():
            state, error = None, None
            try:
                job.result = worker(report)
                state = "done"
            except Exception as e:  # noqa: BLE001 —— 任何异常都要带回界面
                state = "error"
                error = getattr(e, "message", None) or str(e)
            finally:
                # 释放单飞槽位与落终态必须同处一个临界区，否则两者之间
                # 的窗口里「state 已 done、槽位还占着」——测试与前端都以
                # state != running 当作跑完，紧接着启动同类任务会误报
                # JobBusy。先放槽位再落终态，保证「非 running ⇒ 槽位已空」。
                with self._lock:
                    self._running.pop(kind, None)
                    if state is not None:
                        job.state = state
                        if state == "done":
                            job.pct = 100
                        else:
                            job.error = error

        threading.Thread(target=run, daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def running_kind(self, kind: str) -> bool:
        with self._lock:
            return kind in self._running


REGISTRY = JobRegistry()
