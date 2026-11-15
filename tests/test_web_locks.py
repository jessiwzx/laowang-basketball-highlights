"""写锁纪律 / `rev` 单调 / 失败不写盘——本文件钉住 bbh/web 的三条全局契约。

Global Constraint（spec 的硬约束，本轮之前**零测试覆盖**）：
**所有 sidecar 写操作必须在 `state.video_lock(video_path)` 下执行**。
T6 复审用变异证明过：把 `state.video_lock` 换成空 contextmanager，既有的
9 条 web 用例全绿照过——实现经实测正确，缺的是钉住它的测试。同一轮 T8
复审又证明 `rev` 自增与 `?v=` 击穿同样零覆盖。

口径来源：.superpowers/sdd/2026-09-19-frontend-refactor-backend/test-gap-1-brief.md

**有意不在本文件断言范围内的端点**（防后人「补上」）：

1. `GET /api/calib/frame.png`——它写会话（frame_rgb/frame_png/points/rev）
   却不取 `state.video_lock`，是 T8 排期在案的 parked observation（留最终
   复审裁定）。把它写进「只读 = 取 0 次锁」等于替那条观察背书，所以它
   **两边都不进清单**：本文件只在夹具装配里用它抽帧，不对它的取锁次数下断言。
2. `POST /api/detect`、`POST /api/wall/scan`、`POST /api/gen`——三个 job
   端点**有意不取路由级锁**：job 一跑几分钟，路由层持锁会把界面其余所有
   写操作冻死几分钟；它们自己不碰 sidecar（没有读-改-写），真正的写发生
   在 worker 线程里、由冻结函数自己做锁/或在单线程里成串。所以它们既不
   在写端点清单（不是「恰一次锁」），也不在只读清单（不是「只读」）。
   它们的**守卫**不许写盘这一条，由口径③ 覆盖。
"""
import json
import shutil
import urllib.parse
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bbh.models import Calibration, GoalEvent, save_events
from bbh.web import state
from bbh.web.app import create_app

import bbh.app as A

client = TestClient(create_app())


class _LockSpy:
    """进 `with` 才算一次取锁（记录「取了几次 + 用没用起来」）。"""

    def __init__(self, real, calls, path):
        self._real, self.calls, self.path = real, calls, path

    def __enter__(self):
        self.calls.append(self.path)          # 只有真进了 with 才算
        return self._real.__enter__()

    def __exit__(self, *a):
        return self._real.__exit__(*a)


@pytest.fixture()
def lock_calls(monkeypatch):
    """记录本次调用里所有 `state.video_lock(vp)` 的锁键。

    各 router 都是 `from . import state` 后 `state.video_lock(vp)`，所以
    `monkeypatch.setattr(state, …)` 一定命中（**不要**去 patch `state`
    之外的引用——那些引用在 import 时就绑定了）。

    注意：夹具装配期（开会话/抽帧/建标定）自己也会取锁，用例里先
    `lock_calls.clear()` 再发被测请求——这样夹具怎么装都不影响断言。
    """
    real = state.video_lock
    calls = []
    monkeypatch.setattr(state, "video_lock",
                        lambda vp: _LockSpy(real(vp), calls, vp))
    return calls


NAME = "sample"          # work/<视频名>= 真视频的文件名（取自 sample.mp4）


def _work(root: Path, video: str) -> Path:
    return root / "work" / Path(video).stem


def _make_result(root: Path, video: str, n: int = 3) -> None:
    """造一份检测结果 + 回扫缩略图（形状与真产物一致，对齐 work/<视频名>/）。

    缩略图必须真存在：两墙只显示「缩略图文件在」的球（wall_payload 按文件
    存在性过滤），缺了它们 `/api/wall/*` 的键求交会得到空表、归人直接 400。
    """
    work = _work(root, video)
    work.mkdir(parents=True, exist_ok=True)
    evs = [GoalEvent(
        t=12.1 + i * 60, confidence=0.90 - i * 0.10,
        status=("high", "review", "low")[i % 3],
        cross_off=0.02, above_n=3, above_span=0.6, below_n=2, below_span=0.4,
        descent_px=18.0, below_big=2, net_post=12.5, net_ctrl=3.1,
        net_local=True) for i in range(n)]
    save_events(str(work / "events.json"), video=str(root / f"{NAME}.mp4"),
                duration=480.0, events=evs, warning=None,
                calib_key=[1, 2, 3], logic_ver=1)
    (work / "shots").mkdir(exist_ok=True)
    traces = {}
    for i, e in enumerate(evs):                 # 键 = 原始检测时刻，同 A._fmt_t
        key = A._fmt_t(e.t)
        (work / "shots" / f"{i}.jpg").write_bytes(b"\xff\xd8\xff\xe0jpg")
        traces[key] = {"status": "ok", "release_t": e.t,
                       "thumbs": [f"shots/{i}.jpg"]}
    (work / "shooters.json").write_text(
        json.dumps({"traces": traces}, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def env(sample_video, tmp_path, monkeypatch):
    """装法照抄同目录邻居（test_web_wall.py 的 env）：chdir 到 tmp → 造
    work/<视频名>/ 的结果与回扫缩略图 → 开会话。

    **不**造 calibration.json：标定由需要它的用例走真端点生成（见
    calib_env）；口径③ 要的正是「有结果、没标定」这个组合。
    """
    monkeypatch.chdir(tmp_path)
    _make_result(tmp_path, sample_video)
    before = dict(vars(state.SESSION))
    state.SESSION.video_path = ""
    r = client.post("/api/session", json={"videoPath": sample_video})
    assert r.status_code == 200, r.text
    yield tmp_path
    vars(state.SESSION).clear()
    vars(state.SESSION).update(before)


def _frame_and_points(t: float = 1.0) -> None:
    """抽帧（frame.png 会清空 points）+ 补三点（筐口左端/右端 + 球）。

    抽帧必须走真视频：`extract_frame_at` 对不存在的路径直接 gr.Error。
    """
    r = client.get("/api/calib/frame.png", params={"t": t})
    assert r.status_code == 200, r.text
    for x, y in ((10, 20), (30, 20), (20, 20)):
        assert client.post("/api/calib/point",
                           json={"x": x, "y": y}).status_code == 200


@pytest.fixture()
def calib_env(env):
    """env + 抽帧 + 三点 + 主标定（走真端点，标定档落 work/<视频名>/）。"""
    _frame_and_points(1.0)
    r = client.post("/api/calib/build", json={})
    assert r.status_code == 200, r.text
    return env


@pytest.fixture()
def vkey(sample_video):
    """期望锁键 = 路由传进 `state.video_lock` 的那个串。

    `_session_video()` 给的是 `A.clean_path(SESSION.video_path)`，而会话里
    存的就是开会话时 clean_path 过的路径——所以期望值也用 clean_path 算，
    不硬编码（tmp 根路径里有 pytest 的随机段）。
    """
    return A.clean_path(sample_video)


# ---------- 口径① 每个写端点各取一次锁、键正确 ----------
# 清单 13 条（实测自各 router，不增删）。多数端点是先 `with` 再调冻结函数，
# 所以拿 400/404 也算数——**主断言只看锁键序列**；状态码同时断言是为了防
# 「请求根本没走到端点」伪装成绿（探针式假绿：URL 打错 → 404 → calls 恰好
# 空？不，空就红了；但 200/400 的区分能防「守卫提前短路」这类漂移）。
# 唯一例外是第 6/7/10 条（校验/守卫在锁**之前**），必须给合法输入。
# 第 5、13 条的锁键是空串：它们写的是仓库根的全局档（tags.json /
# ui_prefs.json），不属于任何一个视频，state.video_lock("") 退化成同一个
# 模块级 RLock。


def test_lock_01_patch_event(env, lock_calls, vkey):
    lock_calls.clear()
    r = client.patch("/api/events/00:12.1", json={"selection": True})
    assert r.status_code == 200, r.text
    assert lock_calls == [vkey]


def test_lock_02_record_miss(env, lock_calls, vkey):
    lock_calls.clear()
    r = client.post("/api/events/miss", json={"text": "756"})
    assert r.status_code == 200, r.text
    assert lock_calls == [vkey]


def test_lock_03_delete_events(env, lock_calls, vkey):
    lock_calls.clear()
    r = client.request("DELETE", "/api/events", json={"keys": ["00:12.1"]})
    assert r.status_code == 200, r.text
    assert lock_calls == [vkey]


def test_lock_04_restore_events(env, lock_calls, vkey):
    # 先删一条：restore 在「没有已删除的进球」时同样 400（守卫在锁内），
    # 但本口径要的是**真实用户流的成功路径**，别让断言落在守卫上
    assert client.request("DELETE", "/api/events",
                          json={"keys": ["00:12.1"]}).status_code == 200
    lock_calls.clear()
    r = client.post("/api/events/restore")
    assert r.status_code == 200, r.text
    assert lock_calls == [vkey]


def test_lock_05_add_tag_uses_the_global_lock_key(env, lock_calls):
    lock_calls.clear()
    r = client.post("/api/tags", json={"name": "锁测试标签"})
    assert r.status_code == 200, r.text
    assert lock_calls == [""]           # tags.json 跨视频共用 → 全局锁键


def test_lock_06_feedback(env, lock_calls, vkey):
    lock_calls.clear()
    # verdict 校验在锁之前：非法值到不了锁，必须给合法值（见 api_marks._VERDICTS）
    r = client.post("/api/feedback/00:12.1", json={"verdict": "goal"})
    assert r.status_code == 200, r.text
    assert lock_calls == [vkey]


def test_lock_07_flags(env, lock_calls, vkey):
    lock_calls.clear()
    # flag 校验在锁之前：合法值取自 api_marks._FLAGS
    r = client.post("/api/flags/00:12.1", json={"flag": "thumb"})
    assert r.status_code == 200, r.text
    assert lock_calls == [vkey]


def test_lock_08_wall_assign(env, lock_calls, vkey):
    lock_calls.clear()
    r = client.post("/api/wall/assign",
                    json={"person": "老杨", "keys": ["00:12.1"]})
    assert r.status_code == 200, r.text
    assert lock_calls == [vkey]


def test_lock_09_wall_unassign(env, lock_calls, vkey):
    client.post("/api/wall/assign", json={"person": "老杨", "keys": ["00:12.1"]})
    lock_calls.clear()
    r = client.post("/api/wall/unassign", json={"keys": ["00:12.1"]})
    assert r.status_code == 200, r.text
    assert lock_calls == [vkey]


def test_lock_10_calib_build(calib_env, lock_calls, vkey):
    lock_calls.clear()
    # 「先抽帧」守卫在锁之前：夹具已抽过帧 + 点过三点，这里才走得到锁
    r = client.post("/api/calib/build", json={})
    assert r.status_code == 200, r.text
    assert lock_calls == [vkey]


def test_lock_11_calib_refine(calib_env, lock_calls, vkey):
    lock_calls.clear()
    # refine 的守卫（无标定档 → 400）在锁**之内**，所以不需要前置就能测到锁；
    # 但夹具仍建了标定档，让这条同时是「真实用户流」而非只有守卫的路径
    r = client.post("/api/calib/refine", json={"sLow": 90, "vLow": 100})
    assert r.status_code == 200, r.text
    assert lock_calls == [vkey]


def test_lock_12_delete_segment(calib_env, lock_calls, vkey):
    lock_calls.clear()
    # 守卫在锁内：i=99 不在列表里 → 400，但**锁已经取了**（正是本口径要的）
    r = client.request("DELETE", "/api/calib/segments/99")
    assert r.status_code == 400
    assert "不在列表里" in r.json()["error"]
    assert lock_calls == [vkey]


def test_lock_13_session_uses_the_global_lock_key(env, lock_calls, sample_video):
    lock_calls.clear()
    # 口径⑤：_remember_path 对仓库根全局 ui_prefs.json 做读整档→改→写回，
    # 与 tags.json 同形状，锁键同为空串
    r = client.post("/api/session", json={"videoPath": sample_video})
    assert r.status_code == 200, r.text
    assert lock_calls == [""]


# 每项第三位 = **期望状态码**（本夹具下实跑观测到的值，不是照抄 200）。
# 只断 `< 500` 是不够的：URL 打错、端点被删、路由注册行被删，全都会变成
# 404，而 `calls == []` 依然成立——这一组就等于没断言（写端点那组已用状态码
# 防住这个假绿，只读组不能漏）。
READONLY = [
    ("GET", "/api/state", 200),
    ("GET", "/api/events", 200),
    ("GET", "/api/wall", 200),
    ("GET", "/api/wall/guard", 200),
    ("GET", "/api/gen/choices", 200),
    ("GET", "/api/calib/state", 200),
    ("GET", "/api/calib/segments", 200),
    ("GET", "/api/calib/existing", 200),
    ("GET", "/api/calib/preview.png", 200),   # 夹具已建标定 → 有预览可看
    ("POST", "/api/calib/point", 200),      # 只动会话内存（点选坐标），不碰盘
    ("DELETE", "/api/calib/point", 200),    # 同上
    ("GET", "/api/jobs/nope", 404),         # 404 也不许取锁
]

# 期望 404 的项**必须**配 error 文案断言：光断 404 的话，把 URL 打错成另一个
# 不存在的路径仍然是 404，照样假绿——等于白改。（缺项的 KeyError 就是强制
# 配对的机制，新增 404 项时不补文案会红。）
_NOT_FOUND_TEXT = {
    "/api/jobs/nope": "没有这个任务",
}


def test_readonly_endpoints_take_no_lock(calib_env, lock_calls):
    lock_calls.clear()
    for method, url, want in READONLY:
        kw = {"json": {"x": 10, "y": 20}} if url.endswith("/point") \
            and method == "POST" else {}
        r = client.request(method, url, **kw)
        assert r.status_code == want, \
            f"{method} {url} → {r.status_code}（期望 {want}）{r.text}"
        if want == 404:
            assert _NOT_FOUND_TEXT[url] in r.json()["error"]
    assert lock_calls == []


# ---------- 口径② `rev` 单调不减，换视频不归零 ----------

def _rev(url: str) -> int:
    """'/api/calib/preview.png?v=7' → 7（契约层：响应里的 URL）。"""
    return int(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["v"][0])


def test_rev_only_grows_and_survives_a_video_switch(env, sample_video, tmp_path):
    """`rev` 是预览 PNG 的缓存击穿计数器（?v=<rev>），只许 +1、不许归零——
    归零会让浏览器拿旧图（`rev` 不随回包下发，前端记不住当前值）。

    偏离 brief 原文的一处（brief 的顺序照字面走会拿不到 rev4）：
    `GET /api/calib/frame.png` 会**清空 points**（换参考帧 = 点选重来），
    所以「抽帧 → build」之间必须补回三点，否则 build 在锁内就抛
    「请至少点击 3 次」→ 400，`_rev_url` 根本走不到、rev4 测不到。
    本用例因此把「抽帧 + 补三点」封成一步（_frame_and_points）。
    """
    other = tmp_path / "sample2.mp4"            # 换视频不能归零：另一支真视频
    shutil.copyfile(sample_video, other)

    _frame_and_points(1.0)
    b1 = client.post("/api/calib/build", json={})
    assert b1.status_code == 200, b1.text
    rev1 = _rev(b1.json()["previewUrl"])        # 契约层

    rf = client.post("/api/calib/refine", json={"sLow": 90, "vLow": 100})
    assert rf.status_code == 200, rf.text
    rev2 = _rev(rf.json()["previewUrl"])        # 契约层

    assert rev1 < rev2

    # frame.png 的 URL 不带 ?v，契约层观测不到它的 rev（见文件头：这条端点
    # 是 parked observation）——这一步只能读会话字段
    assert client.get("/api/calib/frame.png",
                      params={"t": 2.0}).status_code == 200
    rev3 = state.SESSION.rev
    assert rev2 < rev3

    # 换视频：rev 不许归零
    r = client.post("/api/session", json={"videoPath": str(other)})
    assert r.status_code == 200, r.text
    after_switch = state.SESSION.rev           # 状态层
    assert after_switch >= rev3                # 换视频那一步之后没变小
    assert client.get("/api/calib/state").json()["points"] == []   # 会话确实换了

    # 新视频上重跑一遍：抽帧 → 三点 → build
    _frame_and_points(1.0)
    b2 = client.post("/api/calib/build", json={})
    assert b2.status_code == 200, b2.text
    rev4 = _rev(b2.json()["previewUrl"])        # 契约层
    assert rev3 < rev4
    # 契约层的连续性是本口径的要点：从 rev3 到这里只允许 +2（抽帧一次、
    # build 一次）——若换视频把 rev 归零，或某个端点重复自增，这个等式就红。
    # 等式比不等式硬（复审实测 M5：把 frame_png 的 rev += 1 改成 += 2 时，
    # 不等式全绿、等式红），代价是它耦合了「这段路径恰好两次自增」这个实现
    # 细节：将来若**合法地**多出一次 rev 自增（如新增一个改预览的端点），
    # 这条会假红——届时按当时的真实契约改数字（+2 → +3），**不要删掉断言**
    # （删了就退回只增不减，击穿缓存那类回归再也测不到）。
    assert rev4 == rev3 + 2


# ---------- 口径③ 失败路径不写盘 ----------

def _snapshot(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = p.read_bytes()
    return out


def _bytes_or_none(p: Path):
    return p.read_bytes() if p.exists() else None


def test_failed_requests_write_nothing(env, tmp_path, sample_video):
    """一串必失败的请求跑完，work/ 全树与仓库根 tags.json **逐字节相同**。

    「失败不写盘」是 sidecar 契约的另一半：错误分支若先写后抛，界面回滚了
    状态、盘上却留下半截（或幽灵键），下次开页读到的东西界面解释不了。
    """
    # 基线先让 tags.json 真实存在，否则「不变」是平凡真（None == None）
    assert client.post("/api/tags",
                       json={"name": "锁测试标签"}).status_code == 200
    # build 的先置守卫在锁之前：不抽帧就 400，测不到真正的失败分支
    _frame_and_points(1.0)
    # 第二支视频：有标定、无检测结果——给 wall/scan 的「尚无检测结果」分支
    scan_only = tmp_path / "scanonly.mp4"
    sw = tmp_path / "work" / "scanonly"
    sw.mkdir(parents=True, exist_ok=True)
    Calibration(hoop_rect=(300, 140, 80, 40), hsv_low=(0, 80, 120),
                hsv_high=(25, 255, 255), frame_size=(1920, 1080)
                ).save(str(sw / "calibration.json"))

    def snap():
        return (_snapshot(tmp_path / "work"),
                _bytes_or_none(tmp_path / "tags.json"))

    before = snap()

    fails = [
        # build 的「点数不足」路：segT 给了但没主标定 → 锁内 400
        ("POST", "/api/calib/build", {"json": {"segT": 300.0}}),
        ("DELETE", "/api/calib/segments/99", {}),        # 段不在列表里
        ("POST", "/api/tags", {"json": {"name": "  "}}),  # 空名
        ("POST", "/api/feedback/00:12.1", {"json": {"verdict": "maybe"}}),
        ("POST", "/api/flags/00:12.1", {"json": {"flag": "wat"}}),
        ("POST", "/api/wall/assign",
         {"json": {"person": "老杨", "keys": ["99:99.9"]}}),   # 不在墙上
        ("PATCH", "/api/events/9:99.9", {"json": {"selection": True}}),
        ("POST", "/api/gen", {"json": {"vertical": True, "items": []}}),
    ]
    for method, url, kw in fails:
        r = client.request(method, url, **kw)
        # 必须 4xx 而不是 5xx：5xx 说明异常从别处冒出来（那时盘可能已写脏）
        assert 400 <= r.status_code < 500, \
            f"{method} {url} → {r.status_code} {r.text}"
        assert "error" in r.json()          # 失败恒为 {"error": 中文}
    # 空 keys 的 DELETE /api/events 有意不在清单里：它的契约是「批量宁丢失配
    # 键不中断整批」，空 keys 抛错在锁内，写盘与否要单独看，别混进整体性断言。

    # wall/scan：守卫在起 job 之前同步跑（拿到 400 而不是 jobId）。
    # 「无检测结果」分支要求会话指向那支有标定没结果的视频——这里**直接赋值
    # 会话字段**切视频（纯内存、零写盘）；若走 POST /api/session，它会写
    # ui_prefs.json，在「不写盘」窗口里制造一次合法写盘，把口径搅浑。
    state.SESSION.video_path = str(scan_only)
    try:
        r = client.post("/api/wall/scan")
        assert r.status_code == 400, r.text
        assert "尚无检测结果" in r.json()["error"]
    finally:
        state.SESSION.video_path = A.clean_path(sample_video)

    after = snap()
    assert after == before
