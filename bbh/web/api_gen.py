"""长任务：检测 / 回扫溯源 / 生成集锦。

三类任务都走 jobs.REGISTRY：同类单飞（第二次 409）、进度在 job 里、结果
在 /api/jobs/{id}/result。前端 0.8~1s 轮询一次。

守卫**同步跑**：前置条件不满足（未标定 / 无检测结果 / 没勾选）时，
POST 直接 400 把文案给出去，而不是先返回一个 jobId 再让它变红——
前端一步到位弹提示，少一次往返也少一种状态。

检测的进度不在生成器里而在模块级 app._state（旧界面靠 Timer 轮询它），
所以 job 的 worker 是个轮询循环；回扫/生成是生成器，直接转发 yield。

job 本身一跑几分钟，路由层**不取 state.video_lock**：这几条路径自己不碰
sidecar（没有读-改-写），真正的写发生在 worker 线程里由冻结函数做；这里
持锁会跨越整个 job，把界面其余所有写操作冻住几分钟。
"""
import math
import os
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .. import app as A
from .api_session import _media_url, _session_video, state_payload
from .jobs import REGISTRY, JobBusy

router = APIRouter(prefix="/api")

_MSG_IDX = {"wall_scan": 0, "gen": 4}      # 各生成器里「进度文案」在第几位
# ⚠ 静默耦合（改 `bbh/app.py` 前必读）：_MSG_IDX 的下标与 _gen_result 的逐行
# 解析都钉在冻结文件的**具体位置**上——
#   * `bbh/app.py:1326`
#         `lines += [f"· {item} → {os.path.basename(p)}" for item, p in outs]`
#     这是结果清单唯一的产出点；_gen_result 按 `· ` 开头 + ` → ` 分隔反解出
#     (item, 文件名)。**改这里的 summary 格式（哪怕只把 ` → ` 换成 `：`）必须
#     同步 `_MSG_IDX` 与 `_gen_result` 的解析**，否则每行都进不了 _path_in /
#     os.path.exists 分支 ⇒ videos: []、firstUrl: null，而 summaryMd 照旧含那些
#     行 —— 界面表现是「生成成功、清单为空」。tests/test_web_jobs_api.py::
#     test_gen_selected_real_summary_feeds_result_videos 用真 gen_selected 钉住。
#   * `bbh/app.py:1330-1332`（gen_selected 的末次 yield）：[0] 是成品路径或
#     gr.update、[4] 是结果清单 —— 就是 _MSG_IDX["gen"] == 4 与 _gen_result
#     取 last[0]/last[4] 的出处；`bbh/app.py:1363,1375,1382`
#     （run_shooter_tracing 的 yield）同理对应 _MSG_IDX["wall_scan"] == 0。


class GenIn(BaseModel):
    """生成请求。items 必须是数组——裸 dict 下 {"items": "视频合集"} 会被
    `[str(s) for s in ...]` 按**字符**迭代成 6 个垃圾项还不报错，真起一个
    gen job 去跑；模型校验把它挡在起 job 之前。"""
    vertical: bool = True
    # 产出模式：
    #   "reel"   = 每个勾选项一条（旧行为，默认）
    #   "single" = 每个进球一条独立成品（见 app.generate_singles）
    #   "hook4"  = 每 3 球一条「25s 精选（4s 钩子）」（见 app.generate_hook_reels）
    # **用 Literal 而不是 str**：非法值由 pydantic 拦在起 job 之前（422），
    # 不会出现"任务起来了、跑到一半才发现模式名拼错"。
    output: Literal["reel", "single", "hook4"] = "reel"
    items: list[str] = []
    # —— 会话级行状态（旧界面靠 `rows_state` 带进来）——
    # 不加这几个字段，用户设的前后秒与校准偏移会在生成路径上被静默丢弃：
    # 服务端这里自己 load_events_table(vp) 重算行，前后秒恒为常量默认
    # 5.0/2.0（app.py:42-43、:804）、时刻恒为未校准的检测时刻，而
    # generate_highlight 真的拿 row[4]/row[5]/row[1] 去切（app.py:1992）
    # ⇒ 预览按你设的 8/3 切、成品却按 5/2 切，且没有任何提示。
    before: float | None = None
    after: float | None = None
    durs: dict[str, list[float]] | None = None   # 原始时刻串 → [before, after]（单行改动）
    offset: float | None = None


def _start(kind: str, worker) -> dict:
    try:
        job = REGISTRY.start(kind, worker)
    except JobBusy as e:
        raise HTTPException(409, str(e))
    return {"jobId": job.id}


def _drive(gen, report, kind: str):
    """把生成器的 yield 转发成 job 进度；返回末次 yield（结果在这）。"""
    idx = _MSG_IDX[kind]
    last = None
    for out in gen:
        last = out
        text = out[idx] if isinstance(out, (tuple, list)) else out
        if isinstance(text, str):
            report(msg=text)
    return last


def _poll_detect(report) -> dict:
    """检测已在后台线程里跑（route 里同步起好了），这里只报进度等它完。"""
    while A._state.get("running"):
        msg, pct = A._refresh_status()
        report(msg=msg, pct=pct)
        time.sleep(0.5)
    _msg, pct = A._refresh_status()
    report(pct=pct)
    if A._state.get("error"):
        raise RuntimeError(A._state["error"])
    if A._state.get("cancelled"):
        return {"cancelled": True,
                "msg": "⏹ 已取消——检查点已保存，再点「开始检测」可续跑"}
    return {"msg": A._state.get("done_msg") or "✅ 完成"}


@router.post("/detect")
def detect():
    vp = _session_video()
    if REGISTRY.running_kind("detect"):
        raise HTTPException(409, "已有检测在跑")
    # start_detection 起线程后立刻返回；前置守卫（未标定 / 视频不存在 /
    # 已有检测）在它里面同步抛 gr.Error → 400
    A.start_detection(vp)
    return _start("detect", lambda report: _poll_detect(report))


@router.post("/wall/scan")
def wall_scan():
    vp = _session_video()
    if not A._calib_ready(vp):
        raise HTTPException(400, "本视频还没标定——先去「⚙ 标定设置」完成一次")
    data = A._load_events_or_none(vp)
    if not data or not data.get("events"):
        raise HTTPException(400, "尚无检测结果——先点「▶ 开始检测」")
    return _start("wall_scan", lambda report: {
        "msg": str((_drive(A.run_shooter_tracing(vp), report, "wall_scan")
                    or ["✅ 完成"])[0])})


def _finite(v):
    """有限实数才算「这次传了值」：NaN / ±inf / 字符串 / None 一律当没传。

    与前端 `finiteDur` 同义（`Number.isFinite` 不做隐式转换）——非有限值
    一律**忽略这次传入、保留行里原值**，绝不静默跳回默认 5/2。"""
    return v if isinstance(v, (int, float)) and math.isfinite(v) else None


def _apply_session_rows(rows, body: GenIn):
    """把**会话级的行状态**叠到盘上重算出来的行上（旧界面靠 `rows_state`）。

    服务端在 `gen()` 里自己 `load_events_table(vp)` 重算行，于是用户在界面上
    设的「前后秒」与「时刻校准偏移」会在生成路径上被静默丢弃——前后秒恒为
    常量默认 5.0/2.0、时刻恒为未校准的检测时刻，而 `gen_selected` 真的拿
    `row[1]`（切点）与 `row[4]/row[5]` 去切（`bbh/app.py:1992`）⇒ 同一个会话里
    预览按用户设的值切、成品却不是，且没有任何提示。

    优先级与前端 `durOf` 逐条对齐：单行 `durs[行键]` > 全局 `before/after` >
    行里原有的值（服务端给的）。

    ⚠ 行键是 **row[1] 在偏移平移之前**的值——`load_events_table(offset=0)` 时
    它就是 `_fmt_t(e.t)`，也正是分配表/勾选表/删除表/裁决表共用的那个串。所以
    必须先把所有行的键读完，最后一步才整体平移 `row[1]`。

    ⚠ 偏移**只作为生成时的局部参数叠加，绝不改行键本身**：`_verdict_keys`
    （`api_session.py:137-159`）用的是**记录那一刻的行时间串**当键，整表平移
    （`load_events_table(vp, offset)`）会让两套键分叉 ⇒ 刷新后哪几行亮灯会错。

    只动 row[1]（切点）与 row[4]/row[5]（前后秒）；row[0] 勾选、row[2] 档位、
    row[3] 置信、row[6] 标签一个字节不动。
    """
    out = [list(r) for r in (rows or [])]      # 复制：不改入参里的行
    durs = body.durs if isinstance(body.durs, dict) else {}
    for r in out:
        pair = durs.get(r[1])                  # ← 行键（此刻还没被偏移平移）
        b = a = None
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            b, a = _finite(pair[0]), _finite(pair[1])
        if b is None:
            b = _finite(body.before)
        if a is None:
            a = _finite(body.after)
        if b is not None:
            r[4] = float(b)
        if a is not None:
            r[5] = float(a)
    off = _finite(body.offset)
    if off:                                    # 0 与「没传」都不动（0 平移是恒等）
        for r in out:
            # 夹负那一句与 load_events_table 里那行逐字同义（app.py:801）
            r[1] = A._fmt_t(max(0.0, A._parse_t(r[1]) + float(off)))
    return out


@router.post("/gen")
def gen(body: GenIn):
    vp = _session_video()
    items = [str(s) for s in (body.items or []) if str(s).strip()]
    if not items:
        raise HTTPException(400, "至少勾选一项（视频合集或任一标签）")
    vertical = bool(body.vertical)
    output = body.output
    # 「25s 精选」2026-11-01 起横竖都支持（竖版走 vrender 跟球运镜、不慢放），
    # 所以这里不再强制 vertical=False——画幅按用户选的走。
    _dur, rows = A.load_events_table(vp)
    rows = _apply_session_rows(rows, body)
    work = A._work_dir_for(vp)

    def run(report):
        # 逐条百分比只走 pct 通道，**绝不从 msg 里正则抠 k/N**——那等于在
        # 文案格式上再压一层不走样的隐性约束（本项目已因这类耦合踩过坑）。
        # 集锦模式不调 on_progress（一次一个成品，pct 没意义），行为不变。
        def on_progress(k, n):
            report(pct=int(round(100.0 * k / n)) if n else 0)

        last = _drive(A.gen_selected(vp, rows, items, vertical=vertical,
                                     output=output, on_progress=on_progress),
                      report, "gen")
        return _gen_result(last, work, vertical, output)

    return _start("gen", run)


def _path_in(work: str, p: str) -> str:
    """成品路径归一：管线返回的是 os.path.join(work_dir, name)，而 work_dir
    本身就是相对的 `work/<视频名>`——所以 [0] 里已经是带目录的路径；但结果
    清单里写的是裸文件名（basename）。按有没有目录前缀决定要不要拼。"""
    return p if os.path.dirname(p) else os.path.join(work, p)


def _gen_result(last, work: str, vertical: bool, output: str = "reel") -> dict:
    """末次 yield = (成品播放, 预览占位, …, 结果清单, …)。

    全跳过时 [0] 是 gr.update（不是路径），所以成品清单从 [4] 的结果清单
    里按 `· 项名 → 文件名` 反解；跳过项留在 summaryMd 里让用户看见
    （不假装成功）。gen_selected 只把已生成的写进那几行。

    一行里可能有**多个** ` → `：标签名自身含箭头时（POST /api/tags 零校验、
    editor.safe_filename 只换 `\\/:*?"<>|` 不换箭头），`f"· {item} → {name}"`
    就会拼出 `· A → B → highlight-AB.mp4`。所以不赌分隔符唯一——从右往左
    逐个试分割点，第一个右半边在 work 下**真实存在**的才是 (item, name)；
    整个右半边都不是文件说明这行是「跳过 / 提示」行，**整行丢弃**，绝不
    拿它去凑一条 url 为 null 的幻觉成品。
    """
    summary = ""
    first = None
    if isinstance(last, (tuple, list)):
        first = last[0] if isinstance(last[0], str) else None
        summary = last[4] if len(last) > 4 and isinstance(last[4], str) else ""
    videos = []
    for line in summary.splitlines():
        if not line.startswith("· "):
            continue
        parts = line[2:].split(" → ")
        for i in range(len(parts) - 1, 0, -1):
            # 存在性判据与 URL 同源（都过 _path_in）——名字带目录前缀时
            # os.path.join(work, name) 会拼出 work/<视频>/work/<视频>/…。
            name = " → ".join(parts[i:]).strip()
            if os.path.exists(_path_in(work, name)):
                videos.append({"item": " → ".join(parts[:i]).strip(),
                               "name": name,
                               "url": _media_url(_path_in(work, name))})
                break
    first_url = _media_url(_path_in(work, first)) if first else None
    # 右半边是护栏不是兜底：防「确实出了片（结果清单里有文件），但播放位 [0]
    # 不是路径，**或首片文件已不存在**（_media_url 对不存在的路径回 None）」
    # 这种语义上合法却拿不到可播 URL 的形状——那时按清单第一条给前端一个
    # 能播的 URL，而不是 null。
    return {"summaryMd": summary, "vertical": vertical, "output": output,
            "videos": videos,
            "firstUrl": first_url or (videos[0]["url"] if videos else None)}


@router.get("/gen/choices")
def gen_choices(vertical: bool | None = Query(None),
                output: str = Query("reel")):
    """生成弹窗的清单：视频合集 + 标签库每个标签，默认勾选合集 + 本场有球的
    标签（2026-10-18 起；之前只勾合集，每次都要手勾人）。

    `vertical=None`（2026-10-21）：新界面把画幅收进弹窗且**不设默认值**，
    打开时还没选 ⇒ 统计行不能写「📱 竖屏运镜」，否则等于变相默认。传 None
    走中性「📊 进球统计 ｜ 」。旧的两按钮界面仍显式传 True/False。

    `output="single"`：合集项文案换成「逐球各出一条」，并把**当前模式的合集
    展示串**作为 `allLabel` 回给前端。原因：合集项的展示文案**就是勾选值
    本身**（前端 `pick` 里存的是字符串），两套输出模式文案不同 —— 前端切换
    输出方式时若硬编码旧串，勾选会丢。必须用响应里的 allLabel 做重映射。

    两个模式都有「★ 精选」项（2026-10-24 集锦也支持）：单条=每颗星各出一条，
    集锦=打星的球合成一条。集锦模式下**没有任何星就不列这一项**（避免点了一
    定报错的死选项）；单条模式恒列（它是单条的主路径，空的时候正好给提示）。
    两模式的展示串不同 ⇒ `starLabel` 必须回当前模式的那一串（同上，前端要
    用它做勾选重映射）。

    open_gen_dialog 返回的 choices 是 gr.update(...)，在 gradio 5 里就是个
    dict（{"choices": [...], "value": [...]}）——按 dict 取，别按属性取。
    """
    vp = _session_video()
    _dur, rows = A.load_events_table(vp)
    upd, _vis, stats, _v = A.open_gen_dialog(vp, rows, vertical)
    upd = upd if isinstance(upd, dict) else {}
    # `out` 保留 hook4 原值只为**原样回给前端**（前端靠它判断"输出方式变了、
    # 勾选要重映射"）；下面所有分支判断仍按「是不是 single」走——因为 hook4 与
    # reel 共用同一套合集/精选文案（两者都是"合成一条"的语义，不是"逐球各一条"），
    # `A.all_label` / `A.star_label` 只认 single 这一个特例，传 hook4 会自然落到
    # reel 那两串上，不需要再加第三个串（加串才是灾难：`_ALL_ITEMS` 少登记一个
    # 就会被当标签名筛 ⇒ 0 行 ⇒ 静默不出片）。
    out = str(output or "")
    out = out if out in ("single", "hook4") else "reel"
    label = A.all_label(out)                    # 当前模式下的「合集项」文案
    star_lb = A.star_label(out)                 # 当前模式下的「★ 精选」文案
    choices = list(upd.get("choices") or [])
    star_n = A.star_count(rows)
    if out == "single":
        # 单条模式（2026-10-23）：清单头部多一项「★ 精选」，且**默认只勾它**
        # ——单条的主路径就是"把打星的几条各出一条"，合集/标签留着给"全出"
        # 与"只给某人出"。精选为空时 default 给空数组（不预选任何项）：
        # 绝不预选合集，否则点一下就是 20 条全出。
        choices = [label if c == A.GEN_ALL_LABEL else c for c in choices]
        choices = [star_lb] + choices
        default = [star_lb] if star_n else []
    else:
        # 集锦模式（2026-10-24）：有星时把精选项插到头部，**但默认勾选一个都
        # 不改**（仍是合集 + 本场有球的标签）——集锦的主路径还是"全场一条"，
        # 预选精选会让每次出集锦都多取消一次，也会让老用户觉得行为变了。
        choices = [star_lb] + choices if star_n else choices
        default = [label if c == A.GEN_ALL_LABEL else c
                   for c in (upd.get("value") or [])]
    # 精选项数只进 stats 行（选项文案里不许带数字，见 app.py 常量注释）。
    # 单条模式**恒带**（哪怕 0 球：它就是"这次能出几条"的读数）；集锦模式只在
    # 有星时带（那时清单里才有精选项，0 球时提它是噪音）。
    if stats and (out == "single" or star_n):
        stats = f"{stats} ｜★ 已精选 {star_n} 球"
    return {"choices": choices, "default": default, "allLabel": label,
            # starLabel：当前模式精选项的展示串。两模式都有这一项（集锦在无星
            # 时只是不列出来，串仍回），前端切模式做「合集 ↔ 精选」勾选重映射
            # 时两边都要拿到；让前端去猜 choices[0] 是精选太脆（列表顺序是服务端
            # 实现细节）。
            "starLabel": star_lb,
            "starred": star_n, "stats": stats, "vertical": vertical,
            "output": out}


@router.get("/jobs/{job_id}")
def job_state(job_id: str):
    job = REGISTRY.get(job_id)
    if job is None:
        raise HTTPException(404, f"没有这个任务：{job_id}（服务可能重启过）")
    return job.as_dict()


@router.get("/jobs/{job_id}/result")
def job_result(job_id: str):
    job = REGISTRY.get(job_id)
    if job is None:
        raise HTTPException(404, f"没有这个任务：{job_id}（服务可能重启过）")
    if job.state == "running":
        raise HTTPException(409, "任务还在跑——先等到 done 再来取结果")
    if job.state == "error":
        raise HTTPException(400, job.error or "任务失败")
    payload = dict(job.result or {})
    payload["id"] = job.id
    payload["kind"] = job.kind
    if job.kind in ("detect", "wall_scan"):
        payload.update(state_payload())     # 检测/回扫完 → 行数据与墙一起刷新
    return payload
