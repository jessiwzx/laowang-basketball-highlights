"""单用户会话状态 + 每视频一把写锁。

Gradio 用 gr.State 存「当前视频 / 标定页当前参考帧 / 已点坐标」；换成
HTTP 后这些活在服务端，本机单用户只有一个实例，用模块级单例即可。

写锁的必要性：所有 sidecar 写操作都是「读整档 → 改 → 写回」，而前端在
轮询（读档）——交错会写到半截内容，Windows 上还会撞 PermissionError。
按视频路径取锁（RLock：同一请求里可重入）。
"""
import os
import threading

from ..app import clean_path


class Session:
    def __init__(self) -> None:
        self.video_path: str = ""
        self.frame_rgb = None            # 标定页当前参考帧（numpy RGB）
        self.points: list = []           # 筐口左端、右端、球（1-3 次）
        self.seg_t: float = 0.0          # 「时间点」控件当前值（补段标定的 t0）
        # 图片走内存不落盘（work/ sidecar 红线）：帧/预览各存 PNG 字节，
        # 由 /api/calib/{frame,preview}.png 出，URL 挂 ?v=<rev> 击穿缓存
        self.frame_png: bytes | None = None
        self.preview_png: bytes | None = None
        self.rev: int = 0                # 每次重算 +1（等价旧界面 preview 的 mtime）


SESSION = Session()

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def video_lock(video_path: str) -> threading.RLock:
    """该视频的写锁（路径大小写/分隔符归一后作键）。"""
    key = os.path.realpath(clean_path(video_path)) if video_path else ""
    with _locks_guard:
        return _locks.setdefault(key, threading.RLock())
