# 单页工作台 UI 重设计 Implementation Plan

> **📌 历史文档（过程存档）**：本计划任务已全部实施完毕，仅作过程记录。
> 现行说明见 `README.md`。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把三 Tab 界面重组为「剪辑台」单页工作台（左窄栏流水线 + 右主栏预览为主）+ 独立「⚙ 标定设置」Tab，检测完成自动载入，复核零滚动。

**Architecture:** 页面 State 保存完整行数据（7 列：勾选/时间/状态/置信度/前秒/后秒/标签），表格组件只是 4 列投影；所有业务函数（打标/生成/预览）继续消费完整行，签名基本不变。检测状态轮询函数扩展为"完成即自动载入"。规格见 `docs/superpowers/specs/2026-08-16-single-page-ui-design.md`。

**Tech Stack:** Python 3.12 / gradio 5.50 / pytest；ffmpeg 切片链路不动。

> **用户明确要求：Task 8（build_app 重排、前端制作）执行时必须调用 frontend-design 技能**（布局/视觉细节以其指导为准，本计划给出的组件结构是功能骨架）。

**约定（全计划适用）：**
- 完整行格式：`[checked:int, t_str, status, conf_str, before, after, tag]`（与现有 `load_events_table` 输出一致）
- 投影格式：`[checked:int, t_str, conf_disp, tag]`，`conf_disp = "高 0.90"` / `"待 0.60"`
- `gr.update()` 返回 dict，测试里用 `isinstance(x, dict)` 判断"无操作"
- 测试命令一律 `.venv/Scripts/python -m pytest ...`（项目根目录下执行）

---

### Task 1: 投影与写回纯函数

**Files:**
- Modify: `bbh/app.py`（在 `_parse_t` 之前插入）
- Test: `tests/test_app.py`（文件末尾追加）

- [ ] **Step 1: 写失败测试**

在 `tests/test_app.py` 末尾追加：

```python
class Test表格投影与写回:
    """单页工作台：表格 4 列是完整行 State 的投影，编辑要能写回。"""

    def test_投影_完整行变四列(self, events_work, sample_video):
        from bbh.app import project_rows
        _, rows = load_events_table(str(sample_video))
        proj = project_rows(rows)
        assert len(proj) == len(rows)
        assert len(proj[0]) == 4
        assert proj[0][0] == rows[0][0] and proj[0][1] == rows[0][1]
        assert proj[0][2].startswith(("高 ", "待 ")) and "." in proj[0][2]
        assert proj[0][3] == rows[0][6]

    def test_写回_表格改勾选与标签回写完整行(self, events_work, sample_video):
        from bbh.app import project_rows, sync_table_edits
        _, rows = load_events_table(str(sample_video))
        proj = project_rows(rows)
        proj[0][0] = 0
        proj[0][3] = "小王"
        synced = sync_table_edits(rows, proj)
        assert synced[0][0] == 0 and synced[0][6] == "小王"
        assert synced[0][1] == rows[0][1]      # 时间列不接受表格侧修改
        assert synced[0][4] == rows[0][4]      # 前秒由面板路径改，不在表格

    def test_写回_空与短表不炸(self):
        from bbh.app import sync_table_edits
        assert sync_table_edits([], []) == []
        assert sync_table_edits(None, None) == []
        rows = [[1, "00:02.5", "high", "0.90", 15, 5, ""]]
        assert sync_table_edits(rows, [[1, "00:02.5", "高 0.90", ""]])[0][0] == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_app.py -k 表格投影与写回 -q`
Expected: FAIL `ImportError: cannot import name 'project_rows'`

- [ ] **Step 3: 最小实现**

在 `bbh/app.py` 的 `_parse_t`（约 348 行）之前插入：

```python
def project_rows(rows):
    """完整行 State → 4 列表格投影（☑ 选中/时间/置信度/标签）。
    置信度与状态合并成一格文本：高置信绿色语义、待确认提醒复核。"""
    return [[int(bool(r[0])), r[1],
             f"{'高' if r[2] == 'high' else '待'} {float(r[3]):.2f}",
             str(r[6] or "")] for r in (rows or [])]


def sync_table_edits(rows, table):
    """4 列表格的编辑 → 写回完整行 State。只回写勾选与标签两列；
    时间/置信度由数据生成、前后秒走面板路径，均不接受表格侧修改。
    表格比 State 长时按 State 截断（正常不会发生，防御性）。"""
    out = [list(r) for r in (rows or [])]
    for i, t in enumerate(table or []):
        if i >= len(out):
            break
        out[i][0] = 1 if t[0] else 0
        out[i][6] = str(t[3] or "")
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_app.py -k 表格投影与写回 -q`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add bbh/app.py tests/test_app.py
git commit -m "feat: 表格投影与写回纯函数（单页工作台地基）"
```

---

### Task 2: 当前面板 HTML（含 ℹ 悬浮完整数据卡）

**Files:**
- Modify: `bbh/app.py`（`_cur_row_md` 函数替换为下面的新函数组）
- Test: `tests/test_app.py`

- [ ] **Step 1: 写失败测试**

```python
class Test当前面板:
    def test_面板_含行信息与悬浮卡(self, events_work, sample_video):
        from bbh.app import _cur_panel_html
        _, rows = load_events_table(str(sample_video))
        html = _cur_panel_html(rows, 0, "判定依据：测试文案")
        assert "第 1 条" in html and rows[0][1] in html
        assert "判定依据：测试文案" in html
        assert "ℹ" in html                       # 悬浮信息入口
        assert "onmouseover" in html             # 内联事件（gr.HTML 会执行）
        assert f"前 {rows[0][4]:.0f}s" in html and f"后 {rows[0][5]:.0f}s" in html
        assert str(rows[0][3]) in html           # 完整置信度在卡里

    def test_面板_无当前行给提示(self):
        from bbh.app import _cur_panel_html
        assert "当前行：无" in _cur_panel_html(None, None)
        assert "当前行：无" in _cur_panel_html([[1] * 7], 99)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_app.py -k Test当前面板 -q`
Expected: FAIL `ImportError: cannot import name '_cur_panel_html'`

- [ ] **Step 3: 实现**

`bbh/app.py` 中把 `_cur_row_md` 整个函数替换为：

```python
_PANEL_INIT = ('<div style="color:#888">当前行：无——点击左侧表格任意一行，'
               '这里显示该条信息并预览</div>')


def _info_card(row) -> str:
    """ℹ 悬浮卡：当前行完整数据（含表格 4 列里看不到的字段）。
    纯内联事件实现 hover——gr.HTML 经 innerHTML 注入，<script> 不执行、
    内联 onmouseover/onmouseout 执行（与播放器 ontimeupdate 同一套约束）。"""
    checked, t_str, status, conf, before, after, tag = (list(row) + [""] * 7)[:7]
    items = [
        f"时间　{t_str}",
        f"状态　{'高置信' if status == 'high' else '待确认'}（置信度 {conf}）",
        f"切片　前 {before} 秒 + 后 {after} 秒",
        f"标签　{tag or '—'}",
        f"选中　{'是' if checked else '否'}",
    ]
    card = "<br>".join(items)
    return (
        '<span style="position:relative; cursor:help; font-size:15px" '
        'title="悬浮查看该条完整数据"'
        "onmouseover=\"this.lastElementChild.style.display='block'\" "
        "onmouseout=\"this.lastElementChild.style.display='none'\">ℹ️"
        '<div style="display:none; position:absolute; right:0; top:130%; '
        'z-index:50; background:#fff; border:1px solid #d1d5db; '
        'border-radius:8px; padding:10px 14px; '
        'box-shadow:0 4px 16px rgba(0,0,0,.18); font-size:12px; '
        f'line-height:1.9; white-space:nowrap">{card}</div></span>')


def _cur_panel_html(rows, i, reason: str = "") -> str:
    """右栏「当前行」面板：行信息 + 判定依据 + ℹ 悬浮完整数据卡。
    取代旧 _cur_row_md 与 preview_msg 两个组件的职责。"""
    if rows is None or not isinstance(i, int) or not (0 <= i < len(rows)):
        return _PANEL_INIT
    row = rows[i]
    _, t_str, status, conf, before, after, tag = (list(row) + [""] * 7)[:7]
    head = (f'<div style="font-size:14px"><b>当前：第 {i + 1} 条 · {t_str} · '
            f'{"高置信" if status == "high" else "⚠️ 待确认"}（{conf}） · '
            f'标签 {tag or "—"} · 前 {before} 秒 + 后 {after} 秒 '
            f'{_info_card(row)}</b></div>')
    body = (f'<div style="font-size:12.5px; color:#555; margin-top:4px">'
            f'{reason}</div>') if reason else ""
    return head + body
```

注意：`_cur_row_md` 此刻还有调用方（`load_table_for_ui`/`preview_row_click`/`apply_tag`/`build_app`）。**本任务先不删旧函数体以外的东西**——把 `_cur_row_md` 保留为薄包装放在 `_cur_panel_html` 之后（Task 8 清理）：

```python
def _cur_row_md(table, i) -> str:
    """兼容旧调用（Task 8 移除）：文本版当前行提示。"""
    if table is None or not isinstance(i, int) or not (0 <= i < len(table)):
        return "当前行：无——点击表格任意一行后，再点标签按钮"
    row = table[i]
    tag = str(row[6]) if len(row) > 6 else ""
    return f"当前行：第 {i + 1} 条 · {row[1]} · 标签：**{tag or '—'}**"
```

> 执行偏差（质量审查）：`_info_card`/`_cur_panel_html` 对 tag（及 reason）做 `html.escape` 内容位转义，防用户标签含 `<` 破坏面板结构；测试追加卡片独有字段与空 reason 断言。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_app.py -k Test当前面板 -q`
Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add bbh/app.py tests/test_app.py
git commit -m "feat: 当前面板 HTML 与 ℹ 悬浮完整数据卡"
```

---

### Task 3: 载入函数五元组 + 已有结果自动探测

**Files:**
- Modify: `bbh/app.py`（`load_table_for_ui` 改造；新增 `probe_existing`；新增 `_load_events_or_none`）
- Test: `tests/test_app.py`

- [ ] **Step 1: 写失败测试**

```python
class Test载入与探测:
    def test_载入_五元组含投影与统计(self, events_work, sample_video):
        from bbh.app import load_table_for_ui
        dur_md, proj, rows, cur, panel = load_table_for_ui(str(sample_video))
        assert "视频总长" in dur_md and "条" in dur_md
        assert len(proj) == len(rows) and len(proj[0]) == 4
        assert cur is None
        assert "当前行：无" in panel

    def test_载入_路径带引号也能载(self, events_work, sample_video):
        from bbh.app import load_table_for_ui
        out = load_table_for_ui(f'"{sample_video}"')
        assert len(out[2]) > 0

    def test_探测_已有结果直接载入(self, events_work, sample_video):
        from bbh.app import probe_existing
        dur_md, proj, rows, cur, panel, status = probe_existing(str(sample_video))
        assert "视频总长" in dur_md and len(rows) > 0
        assert "已载入已有检测结果" in status

    def test_探测_无结果给新视频提示并清空(self, tmp_path, monkeypatch):
        from bbh.app import probe_existing
        monkeypatch.chdir(tmp_path)
        dur_md, proj, rows, cur, panel, status = probe_existing(
            str(tmp_path / "不存在.mp4"))
        assert proj == [] and rows == []
        assert "点「开始检测」" in status

    def test_探测_空路径不炸(self, tmp_path, monkeypatch):
        from bbh.app import probe_existing
        monkeypatch.chdir(tmp_path)
        out = probe_existing("")
        assert out[5] != ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_app.py -k Test载入与探测 -q`
Expected: FAIL（`load_table_for_ui` 返回 4 元组解包失败 / `probe_existing` 不存在）

- [ ] **Step 3: 实现**

`bbh/app.py`：`preview_row_click` 内的 events 读取逻辑提成模块级（放在 `_work_dir_for` 之后）：

```python
def _load_events_or_none(video_path):
    """读 events.json；失败（未检测/损坏）返回 None，不抛。"""
    try:
        return load_events(os.path.join(
            _work_dir_for(clean_path(video_path)), "events.json"))
    except (OSError, ValueError, KeyError):
        return None
```

`load_table_for_ui` 整体替换为：

```python
def load_table_for_ui(video_path: str, offset: float = 0.0):
    """载入检测结果 → (时长统计, 4 列投影, 完整行 State, 当前行复位, 面板)。
    对应 [duration_md, events_table, rows_state, cur_row_state, cur_panel]。"""
    dur, rows = load_events_table(clean_path(video_path), offset)
    n_high = sum(1 for r in rows if r[2] == "high")
    dur_md = (f"视频总长 {int(dur // 60)} 分 {dur % 60:.0f} 秒 · "
              f"共 {len(rows)} 条（高置信 {n_high} / 待确认 {len(rows) - n_high}）")
    return (dur_md, project_rows(rows), rows, None, _cur_panel_html(rows, None))
```

新增（`load_table_for_ui` 之后）：

```python
def probe_existing(video_path: str):
    """路径输入变化 → 自动探测已有检测结果：有则直接载入，无则清空待检测。
    对应 [duration_md, events_table, rows_state, cur_row_state, cur_panel,
    status_md]。"""
    vp = clean_path(video_path)
    if not vp or not os.path.exists(os.path.join(_work_dir_for(vp),
                                                 "events.json")):
        return ("", [], [], None, _PANEL_INIT,
                "新视频：点「开始检测」开始（检测中途退出可续跑）")
    try:
        dur_md, proj, rows, _, panel = load_table_for_ui(vp)
    except (OSError, ValueError, KeyError):
        return ("", [], [], None, _PANEL_INIT,
                "检测结果文件损坏——请重新检测覆盖")
    return (dur_md, proj, rows, None, panel,
            "已载入已有检测结果——重新检测会覆盖")
```

同时更新两个旧测试（`test_载入按钮四元组不炸`、`test_进球时刻校准偏移` 里
`load_table_for_ui` 的解包）：四元组 → 五元组、行断言改用返回的 `rows`
（第 3 个元素）。例如：

```python
def test_载入按钮四元组不炸(events_work, sample_video):
    """「载入检测结果」全链路：曾因 _cur_row_md(rows, None) 的 0<=None 崩溃。"""
    from bbh.app import load_table_for_ui
    dur_md, proj, rows, cur, panel = load_table_for_ui(str(sample_video))
    assert "视频总长" in dur_md and len(rows) > 0 and len(proj[0]) == 4
    dur_md2, _, rows2, _, _ = load_table_for_ui(f'"{sample_video}"')
    assert len(rows2) == len(rows)
```

`test_进球时刻校准偏移` 内两处解包 `_, rows_a, _, _ = ...` 改为
`_, _, rows_a, _, _ = ...`（偏移断言本身不动）。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_app.py -k "载入与探测 or 载入按钮 or 时刻校准" -q`
Expected: 全部 passed（含更新后的旧测试）

- [ ] **Step 5: 提交**

```bash
git add bbh/app.py tests/test_app.py
git commit -m "feat: 载入返回投影五元组；路径输入自动探测已有结果"
```

---

### Task 4: 点行预览新签名 + 回到逐条

**Files:**
- Modify: `bbh/app.py`（`preview_row_click` 改造；新增 `_back_to_preview`）
- Test: `tests/test_app.py`

- [ ] **Step 1: 写失败测试（并更新 4 个旧测试的解包）**

```python
class Test点行预览新签名:
    def test_返回六元组_面板含依据与算式(self, events_work, sample_video):
        from bbh.app import preview_row_click
        _, rows = load_events_table(str(sample_video))
        out = preview_row_click(str(sample_video), rows, _row_evt(0))
        html, hl, cur, panel, cb, ca = out
        assert "<video" in html and "/gradio_api/file=" in html
        assert isinstance(hl, dict) and hl.get("visible") is False  # 收起成品
        assert cur == 0
        assert "第 1 条" in panel and "判定依据" in panel and "置信度" in panel
        assert cb == rows[0][4] and ca == rows[0][5]   # 面板数字框同步该行

    def test_回到逐条预览按钮(self):
        from bbh.app import _back_to_preview
        hl, html, back = _back_to_preview()
        assert isinstance(hl, dict) and hl.get("visible") is False
        assert isinstance(back, dict) and back.get("visible") is False
        assert "点表格任意一行" in html
```

旧测试更新（只改解包与 msg→panel 断言，行为断言不动）：
- `test_点击表格行即预览`：`html, msg, cur, cur_md = ...` →
  `html, _, cur, panel, _, _ = ...`；原 `assert ... in msg` 的判定依据/算式
  断言全部改到 `panel`；`"第 1 条" in cur_md` → `"第 1 条" in panel`；
  `muted`/`autoplay`/`?v=` 断言留在 `html` 上不变
- `test_点击表头或空表报错`：解包行去掉（只 with pytest.raises）
- `test_相邻片段不合并_各自预览`：`html, msg, _, _ = ...` → 取 `html`
- `test_预览时长帧准确`：若用 `preview_row_click`，同样只取 `html`

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_app.py -k "新签名 or 即预览 or 不合并" -q`
Expected: 新签名测试 FAIL（解包数量不符）

- [ ] **Step 3: 实现**

`preview_row_click` 整体替换为：

```python
def preview_row_click(video_path: str, rows, evt: gr.SelectData):
    """表格行点击 → 立即预览该行并设为「当前行」（打标签/改前后秒的作用对象）。
    返回 (播放器 HTML, 成品收起, 当前行号, 当前面板, 面板前秒, 面板后秒)，
    对应 [preview_player, highlight_player, cur_row_state, cur_panel,
    cur_before, cur_after]。"""
    i = evt.index[0] if isinstance(evt.index, (list, tuple)) else None
    if not isinstance(i, int) or i < 0 or not (0 <= i < len(rows or [])):
        raise gr.Error("请点击表格中的数据行（点击表头无效）")
    row = rows[i]
    t = _parse_t(row[1])
    before, after = _as_float(row[4], 15), _as_float(row[5], 5)
    data = _load_events_or_none(video_path)
    duration = data["duration"] if data else float("inf")
    out = _cut_preview(video_path, t, before, after)
    # 分色条按"实际落进切片"的前后长度（视频首尾处窗口会被裁短）
    if duration == float("inf"):
        split_b, split_a = before, after
    else:
        split_b = min(before, t)
        split_a = min(after, max(duration - t, 0.0))
    reason = (_reason_text(data["events"][i])   # 行号与事件一一对应
              if data and i < len(data["events"]) else "")
    return (_player_html(out, split_b, split_a),
            gr.update(visible=False), i, _cur_panel_html(rows, i, reason),
            before, after)
```

新增（其后）：

```python
def _back_to_preview():
    """「↩ 回到逐条预览」：收起集锦成品，恢复预览占位。"""
    return (gr.update(visible=False),
            '<div style="color:#888; padding:12px 0">'
            "点表格任意一行，这里播放该片段</div>",
            gr.update(visible=False))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_app.py -k "新签名 or 即预览 or 不合并 or 表头" -q`
Expected: 全部 passed

- [ ] **Step 5: 提交**

```bash
git add bbh/app.py tests/test_app.py
git commit -m "feat: 点行预览返回六元组（收起成品+面板+前后秒同步）"
```

---

### Task 5: 面板重切 + 全局应用 + 表格编辑落盘

**Files:**
- Modify: `bbh/app.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: 写失败测试**

```python
class Test编辑路径:
    def test_重切_更新该行前后秒并重出播放器(self, events_work, sample_video):
        from bbh.app import recut_current
        _, rows = load_events_table(str(sample_video))
        html, rows2, panel = recut_current(str(sample_video), rows, 0, 10, 3)
        assert "<video" in html and "进球前 10s" in html
        assert rows2[0][4] == 10 and rows2[0][5] == 3
        assert "第 1 条" in panel and "前 10" in panel
        assert rows[0][4] == 15                      # 原 State 不被就地污染

    def test_重切_未选行报错(self, events_work, sample_video):
        from bbh.app import recut_current
        _, rows = load_events_table(str(sample_video))
        with pytest.raises(gr.Error):
            recut_current(str(sample_video), rows, None, 10, 3)

    def test_全局应用_写回全部行并出投影(self):
        from bbh.app import apply_all_rows
        rows = [[1, "00:10.0", "high", "0.90", 15, 5, ""],
                [0, "00:20.0", "review", "0.60", 15, 5, "x"]]
        rows2, proj = apply_all_rows(rows, 8, 2)
        assert all(r[4] == 8 and r[5] == 2 for r in rows2)
        assert len(proj) == 2 and len(proj[0]) == 4

    def test_全局应用_清空数字框按默认兜底(self):
        from bbh.app import apply_all_rows
        rows2, _ = apply_all_rows([[1, "00:10.0", "high", "0.90", 1, 1, ""]],
                                  None, None)
        assert rows2[0][4] == 15 and rows2[0][5] == 5

    def test_表格编辑_落盘标签分配(self, events_work, sample_video, tmp_path):
        from bbh.app import on_table_edit, _load_assignments
        _, rows = load_events_table(str(sample_video))
        proj = [[1, rows[0][1], "高 0.90", "小王"]]
        synced = on_table_edit(str(sample_video), rows, proj)
        assert synced[0][6] == "小王"
        assert _load_assignments(str(sample_video)).get(rows[0][1]) == "小王"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_app.py -k Test编辑路径 -q`
Expected: FAIL `ImportError`

- [ ] **Step 3: 实现**

在 `preview_row_click`/`_back_to_preview` 之后新增：

```python
def recut_current(video_path: str, rows, cur_row, before, after):
    """面板「↻ 按此重切预览」：把面板前后秒写回当前行并重切预览。
    返回 (播放器 HTML, 完整行 State, 当前面板)，对应
    [preview_player, rows_state, cur_panel]。"""
    if cur_row is None or rows is None or not (0 <= int(cur_row) < len(rows)):
        raise gr.Error("请先点击表格选择一行，再重切预览")
    i = int(cur_row)
    before, after = _as_float(before, 15), _as_float(after, 5)
    rows = [list(r) for r in rows]                  # 不就地污染旧 State
    rows[i][4], rows[i][5] = before, after
    t = _parse_t(rows[i][1])
    data = _load_events_or_none(video_path)
    duration = data["duration"] if data else float("inf")
    out = _cut_preview(video_path, t, before, after)
    if duration == float("inf"):
        split_b, split_a = before, after
    else:
        split_b = min(before, t)
        split_a = min(after, max(duration - t, 0.0))
    reason = (_reason_text(data["events"][i])
              if data and i < len(data["events"]) else "")
    return (_player_html(out, split_b, split_a), rows,
            _cur_panel_html(rows, i, reason))


def apply_all_rows(video_path: str, rows, b, a, cur_row=None):
    """全局前后秒 → 写回全部行并重出投影；当前行面板与前后秒框同步刷新
    （防"应用后重切回写旧值"静默撤销），面板带判定依据（读文件，无切片成本）。
    对应 [rows_state, events_table, cur_panel, cur_before, cur_after]。"""
    b = _as_float(b, 15)             # 数字框被清空/坏值时不写 None
    a = _as_float(a, 5)
    out = [list(r) for r in (rows or [])]
    for r in out:
        r[4], r[5] = b, a
    if isinstance(cur_row, int) and 0 <= cur_row < len(out):
        return (out, project_rows(out),
                _cur_panel_html(out, cur_row,
                                _reason_for(video_path, out, cur_row)), b, a)
    return (out, project_rows(out), gr.update(), gr.update(), gr.update())


def on_table_edit(video_path: str, rows, table):
    """表格任何手工编辑（勾选/标签格）→ 写回 State 并落盘分配表。
    对应 [rows_state]。"""
    synced = sync_table_edits(rows, table)
    _persist_assignments(video_path, synced)
    return synced
```

> 执行偏差（质量审查）：recut_current 与 preview_row_click 共享
> `_preview_and_reason` helper；`_cur_panel_html` 对 before/after 做
> `_as_float` 兜底防 None 格 TypeError；`_back_to_preview` docstring
> 补组件映射。
>
> 执行偏差（实现时发现）：Step 1 测试中 `"进球前 10s" in html` 与既定的
> 分色条语义矛盾——播放器分色条按"实际落进切片"的前后长度渲染
> （`split_b = min(before, t)`，`test_点击表格行即预览` 已锁定该行为）；
> 样例视频 6s、进球 t≈2.5，前侧 10s 会被裁短显示为 `进球前 2s`。断言已
> 改为按同一公式断言裁短后的值（`进球前 {min(10, t):.0f}s` + `进球后 3s`），
> 实现按计划不变。
>
> 执行偏差（质量审查 Important）：`apply_all_rows` 签名扩展为
> `(rows, b, a, cur_row=None)`、返回五元组——当前行面板与前后秒框随全局
> 应用同步刷新，防"应用后重切回写旧值"静默撤销刚应用的全局值；b/a 的
> None 兜底统一走 `_as_float`。Task 8 接线相应加 `cur_row_state` 输入与
> `cur_panel/cur_before/cur_after` 输出。`_info_card` 前后秒同样
> `_as_float` 归一（与头部行信息一致，防"前 None 秒"）。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_app.py -k Test编辑路径 -q`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add bbh/app.py tests/test_app.py
git commit -m "feat: 面板重切/全局应用/表格编辑写回三条路径"
```

---

### Task 6: 打标面板化 + 生成切换预览位

**Files:**
- Modify: `bbh/app.py`（`apply_tag` 返回值第二项换 `_cur_panel_html`；新增 `_generate_for_ui`）
- Test: `tests/test_app.py`

- [ ] **Step 1: 写失败测试；更新两个旧打标测试的断言**

```python
class Test打标与生成切换:
    def test_打标_面板更新且行数据带标签(self, events_work, sample_video, tmp_path,
                                      monkeypatch):
        from bbh.app import apply_tag
        _, rows = load_events_table(str(sample_video))
        rows, panel, dd, tags = apply_tag(str(sample_video), rows, 0, "小王")
        assert rows[0][6] == "小王"
        assert "第 1 条" in panel and "小王" in panel and "ℹ" in panel

    def test_生成_返回成品路径与占位切换(self, events_work, sample_video):
        from bbh.app import _generate_for_ui
        _, rows = load_events_table(str(sample_video))
        out, html, back = _generate_for_ui(str(sample_video), rows, "")
        assert os.path.basename(out) == "highlight.mp4"
        assert "已生成" in html
        assert isinstance(back, dict) and back.get("visible") is True
```

旧测试更新：`test_打标签更新表格并入库`、`test_标签按视频持久化_重载恢复`、
`test_打标签的前置校验` 中 `table, cur_md, dd, tags = apply_tag(...)` 的
`cur_md` 断言（`"第 1 条" in cur_md`）改为 `panel`（变量改名即可，断言不变）。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_app.py -k "打标与生成切换 or 打标签" -q`
Expected: 新测试 FAIL（panel 无 ℹ / `_generate_for_ui` 不存在）

- [ ] **Step 3: 实现**

`apply_tag` 中最后一行改为（其余不动）：

```python
    return (rows, _cur_panel_html(rows, i, _reason_for(video_path, rows, i)),
            gr.update(choices=tags), tags)
```

（函数体开头加 `rows = [list(r) for r in (rows or [])]` 防止就地污染调用方
State；`table[i][6] = tag` 相应改为 `rows[i][6] = tag`，其余引用同步改名。）

新增：

```python
def _generate_for_ui(video_path: str, rows, tag_filter: str = ""):
    """生成集锦 → (成品路径, 预览占位 HTML, 回到逐条按钮显示)。
    成品在预览位播放（对应 [highlight_player, preview_player, btn_back]）。"""
    out = generate_highlight(video_path, rows, tag_filter)
    placeholder = ('<div style="color:#888; padding:12px 0">'
                   "✅ 集锦已生成，正在播放。点表格任意一行或"
                   "「↩ 回到逐条预览」继续复核</div>")
    return out, placeholder, gr.update(visible=True)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_app.py -k "打标与生成切换 or 打标签 or 持久化" -q`
Expected: 全部 passed

- [ ] **Step 5: 提交**

```bash
git add bbh/app.py tests/test_app.py
git commit -m "feat: 打标走面板；生成集锦切换预览位播放"
```

---

### Task 7: 检测完成自动载入

**Files:**
- Modify: `bbh/app.py`（`_state` 加 `autoloaded` 键；`start_detection` 文案与复位；新增 `refresh_and_autoload`）
- Test: `tests/test_app.py`

- [ ] **Step 1: 写失败测试**

```python
class Test自动载入:
    def _mark_done(self, app_state, video):
        app._state.update(running=False, done=True, error=None,
                          autoloaded=False,
                          events_path=os.path.join(
                              app._work_dir_for(video), "events.json"))

    def test_检测中_面板提示且表格锁定(self, app_state):
        from bbh.app import refresh_and_autoload
        app._state.update(running=True, done=False, done_frames=5,
                          total_frames=10, stage=None)
        out = refresh_and_autoload("whatever.mp4")
        assert len(out) == 7
        assert "检测中" in out[0] and out[1] == 50
        assert isinstance(out[3], dict) and out[3].get("interactive") is False
        assert "完成后自动载入" in out[6]

    def test_完成_自动载入一次(self, app_state, events_work, sample_video):
        from bbh.app import refresh_and_autoload
        self._mark_done(app_state, str(sample_video))
        out = refresh_and_autoload(str(sample_video))
        assert "视频总长" in out[2] and len(out[4]) > 0    # 载入成功
        assert app._state["autoloaded"] is True

    def test_已载入后_不再重复载入(self, app_state, events_work, sample_video):
        from bbh.app import refresh_and_autoload
        self._mark_done(app_state, str(sample_video))
        refresh_and_autoload(str(sample_video))
        out2 = refresh_and_autoload(str(sample_video))
        assert isinstance(out2[3], dict) and "value" not in out2[3]  # 无操作

    def test_空闲_全无操作(self, app_state):
        from bbh.app import refresh_and_autoload
        app._state.update(running=False, done=False, error=None)
        out = refresh_and_autoload("x.mp4")
        assert all(isinstance(x, dict) for x in out[2:])
```

（`app_state` 是既有 fixture：重置 `app._state` 并隔离 CALIB_FILE；
`import bbh.app as app` 文件头已有。）

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_app.py -k Test自动载入 -q`
Expected: FAIL `ImportError: cannot import name 'refresh_and_autoload'`

- [ ] **Step 3: 实现**

`_state` 初始化字典加入 `"autoloaded": False`（`_state = {..., "stage": None,
"autoloaded": False}`）。

`start_detection` 的 `_state.update(...)` 增加 `autoloaded=False`；无标定文案改为：

```python
        raise gr.Error("还没标定——先切到「⚙ 标定设置」完成一次（固定机位只需一次）")
```

`_refresh_status` 之后新增：

```python
def refresh_and_autoload(video_path: str):
    """Timer 轮询：进度刷新 + 检测完成自动载入表格（只载一次，不覆盖复核编辑）。
    返回 7 元组，对应 [status_md, progress_bar, duration_md, events_table,
    rows_state, cur_row_state, cur_panel]。检测中锁表格，其余分支显式恢复
    interactive=True（纯值/空 update 都不会触碰该属性，不恢复就永久锁死）。
    空闲 tick 的 status_md 给空 update——不覆盖 probe_existing 等先前写入的
    提示（「空闲」初值已由组件构造给定）；出错分支仍写实值，错误才能上屏。"""
    md, pct = _refresh_status()
    if _state["running"]:
        return (md, pct, gr.update(), gr.update(interactive=False), gr.update(),
                gr.update(), "⏳ 检测中——完成后自动载入结果，可离开本页")
    if _state["error"]:
        return (md, pct, gr.update(), gr.update(interactive=True), gr.update(),
                gr.update(), gr.update())
    if _state["done"] and not _state.get("autoloaded"):
        _state["autoloaded"] = True
        try:
            dur_md, proj, rows, _, panel = load_table_for_ui(video_path)
            return (md, pct, dur_md, gr.update(value=proj, interactive=True),
                    rows, None, panel)
        except (gr.Error, OSError, ValueError, KeyError) as e:
            return (md, pct, gr.update(), gr.update(interactive=True),
                    gr.update(), gr.update(), f"检测完成但载入失败：{e}")
    return (gr.update(), pct, gr.update(), gr.update(interactive=True),
            gr.update(), gr.update(), gr.update())
```

> **实现偏差（审查修正）**：原计划的 done/空闲/失败分支返回纯值或空
> `gr.update()`——gradio 5.50 里纯值只设 value、空 update 零属性更新，
> running 分支发过的 `interactive=False` 会在 block 配置上永久累积，
> 检测一完成复核表格就被锁死（勾选/标签编辑全失效）。修正：非 running
> 分支全部显式 `interactive=True`，成功分支用 `gr.update(value=proj,
> interactive=True)` 一次带全。except 同时加宽为 `(gr.Error, OSError,
> ValueError, KeyError)`：events.json 损坏时 `load_events` 冒出的是后三类
> （`probe_existing` 同路径同捕法），只捕 gr.Error 会炸掉 Timer 回调。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_app.py -k "自动载入 or 刷新状态 or 重入" -q`
Expected: 全部 passed（既有状态测试不回归）

- [ ] **Step 5: 提交**

```bash
git add bbh/app.py tests/test_app.py
git commit -m "feat: 检测完成自动载入（只载一次，防覆盖复核编辑）"
```

---

### Task 8: build_app 重排为单页工作台

**Files:**
- Modify: `bbh/app.py:557-747`（`build_app` 整体替换）
- Test: `tests/test_app.py`（`test_build_app可构建` 不变，应直接通过）

- [ ] **Step 1: 整体替换 build_app**

```python
def build_app():
    with gr.Blocks(title="篮球进球自动集锦") as demo:
        # ---------- 主页：剪辑台（左窄栏流水线 + 右主栏预览） ----------
        with gr.Tab("剪辑台"):
            with gr.Row():
                with gr.Column(scale=2):
                    gr.Markdown("**① 视频与检测**　首次使用先去「⚙ 标定设置」"
                                "做一次标定（固定机位只需一次）")
                    video_path_box = gr.Textbox(
                        label="视频路径", placeholder=r"D:\videos\2026-08-16.mp4")
                    btn_start = gr.Button("▶ 开始检测", variant="primary")
                    status_md = gr.Markdown("空闲。检测中途退出后，"
                                            "重新点开始即可从检查点续跑。")
                    progress_bar = gr.Slider(0, 100, value=0, label="进度%",
                                             interactive=False)
                    duration_md = gr.Markdown()
                    gr.Markdown("**② 进球列表**　点行 → 右侧预览；"
                                "可直接改「选中 / 标签」格")
                    events_table = gr.Dataframe(
                        headers=["选中", "时间", "置信度", "标签"],
                        datatype=["bool", "str", "str", "str"],
                        type="array", interactive=True, max_height=420,
                        label="进球列表（4 列精简视图；ℹ 完整数据看右侧面板）")
                    rows_state = gr.State([])   # 完整行：勾选/时间/状态/置信度/前/后/标签
                    with gr.Row():
                        global_before = gr.Number(value=15, label="全局 前(秒)")
                        global_after = gr.Number(value=5, label="全局 后(秒)")
                        btn_apply_all = gr.Button("应用到全部")
                    with gr.Accordion("更多调整", open=False):
                        time_offset = gr.Number(
                            value=0, label="进球时刻校准（秒）：检测比实际进球"
                            "早就填正数（整体后移），晚了填负数；改完点重新载入")
                        btn_reload = gr.Button("重新载入")
                    btn_generate = gr.Button("🎬 生成集锦 MP4", variant="primary")

                with gr.Column(scale=5):
                    preview_player = gr.HTML(
                        '<div style="color:#888; padding:12px 0">'
                        "点击表格任意一行，这里播放该片段；进度条上"
                        '<span style="color:#2563eb">蓝=进球前</span>、'
                        '<span style="color:#dc2626">红线=进球时刻</span>、'
                        '<span style="color:#16a34a">绿=进球后</span>'
                        "，点击进度条可跳转</div>")
                    highlight_player = gr.Video(label="集锦成品", visible=False)
                    btn_back = gr.Button("↩ 回到逐条预览", visible=False)
                    with gr.Row():
                        cur_before = gr.Number(value=15, label="当前条 前(秒)")
                        cur_after = gr.Number(value=5, label="当前条 后(秒)")
                        btn_recut = gr.Button("↻ 按此重切预览")
                    cur_row_state = gr.State(None)   # 打标签/重切的作用行
                    cur_panel = gr.HTML(_PANEL_INIT)
                    tags_state = gr.State(_load_tags())
                    tag_filter = gr.Dropdown(
                        choices=_load_tags(), value=None,
                        label="生成时只保留该标签的进球（留空 = 全部勾选的）")
                    gr.Markdown("**打标签**：先点表格选中一行，再点标签按钮；"
                                "新名字回车自动入库并打给当前行。")

                    @gr.render(inputs=tags_state)
                    def _tag_buttons(tags):
                        """标签库快速选择：新增标签后按钮自动出现。"""
                        with gr.Row():
                            if not (tags or []):
                                gr.Markdown("（标签库是空的——在下方输入第一个名字）")
                            for tag in (tags or []):
                                def _hit(vp, rows, cur, _tag=tag):
                                    rows, panel, dd, t = apply_tag(
                                        vp, rows, cur, _tag)
                                    return project_rows(rows), rows, panel, dd, t
                                gr.Button(f"🏷 {tag}", size="sm").click(
                                    _hit,
                                    inputs=[video_path_box, rows_state,
                                            cur_row_state],
                                    outputs=[events_table, rows_state,
                                             cur_panel, tag_filter, tags_state])

                    with gr.Row():
                        new_tag_box = gr.Textbox(
                            label="新标签名字（回车或点按钮，打给当前行）", scale=3)
                        btn_new_tag = gr.Button("🏷 打给当前行", scale=1)

                    def _apply_new_tag(vp, rows, cur, name):
                        rows, panel, dd, tags = apply_tag(vp, rows, cur, name)
                        return (project_rows(rows), rows, panel, dd, tags, "")

                    status_timer = gr.Timer(2.0)
                    status_timer.tick(
                        refresh_and_autoload, inputs=[video_path_box],
                        outputs=[status_md, progress_bar, duration_md,
                                 events_table, rows_state, cur_row_state,
                                 cur_panel])
                    video_path_box.change(
                        probe_existing, inputs=[video_path_box],
                        outputs=[duration_md, events_table, rows_state,
                                 cur_row_state, cur_panel, status_md])
                    btn_start.click(start_detection, inputs=[video_path_box],
                                    outputs=[status_md, progress_bar])
                    events_table.select(
                        preview_row_click,
                        inputs=[video_path_box, rows_state],
                        outputs=[preview_player, highlight_player,
                                 cur_row_state, cur_panel, cur_before,
                                 cur_after])
                    events_table.input(
                        on_table_edit,
                        inputs=[video_path_box, rows_state, events_table],
                        outputs=[rows_state])
                    btn_recut.click(
                        recut_current,
                        inputs=[video_path_box, rows_state, cur_row_state,
                                cur_before, cur_after],
                        outputs=[preview_player, rows_state, cur_panel])
                    btn_apply_all.click(
                        apply_all_rows, inputs=[rows_state, global_before,
                                                global_after, cur_row_state],
                        outputs=[rows_state, events_table, cur_panel,
                                 cur_before, cur_after])
                    btn_reload.click(
                        load_table_for_ui, inputs=[video_path_box, time_offset],
                        outputs=[duration_md, events_table, rows_state,
                                 cur_row_state, cur_panel])
                    btn_new_tag.click(_apply_new_tag,
                                      inputs=[video_path_box, rows_state,
                                              cur_row_state, new_tag_box],
                                      outputs=[events_table, rows_state,
                                               cur_panel, tag_filter,
                                               tags_state, new_tag_box])
                    new_tag_box.submit(_apply_new_tag,
                                       inputs=[video_path_box, rows_state,
                                               cur_row_state, new_tag_box],
                                       outputs=[events_table, rows_state,
                                                cur_panel, tag_filter,
                                                tags_state, new_tag_box])
                    btn_generate.click(
                        _generate_for_ui,
                        inputs=[video_path_box, rows_state, tag_filter],
                        outputs=[highlight_player, preview_player, btn_back])
                    btn_back.click(_back_to_preview, inputs=None,
                                   outputs=[highlight_player, preview_player,
                                            btn_back])

        # ---------- ⚙ 标定设置（原① 标定页整体搬迁，内部不变） ----------
        with gr.Tab("⚙ 标定设置（首次使用做一次）"):
            gr.Markdown("上传一段日常视频，或直接填本机路径（**长视频推荐，免上传**）"
                        " → 自动抽取中段一帧 → 在帧上依次点击："
                        "**筐口左端、筐口右端、球（1-3 次）**")
            calib_video = gr.Video(label="参考视频上传（短视频适用，大文件见下方路径）")
            calib_path = gr.Textbox(
                label="或填参考视频完整路径（回车或点按钮抽帧）",
                placeholder=r"D:\videos\2026-08-16.mp4")
            btn_from_path = gr.Button("从该路径抽帧")
            with gr.Row():
                calib_time = gr.Number(label="时间点（秒）——跳到球在筐口附近的画面",
                                       precision=0)
                btn_jump = gr.Button("跳到该时间抽帧")
            frame_img = gr.Image(
                label="参考帧（依次点击：筐口左端、筐口右端、球 1-3 次）",
                sources=())   # 不限高度，按原视频尺寸显示（.select 坐标始终是原图像素）
            click_msg = gr.Markdown(click_hint([]))
            pts_state = gr.State([])
            with gr.Row():
                btn_clear = gr.Button("清空点击")
                btn_calib = gr.Button("生成标定", variant="primary")
            preview_img = gr.Image(
                label="预览验证（绿=球，蓝框=筐口，红线=筐口平面，黄框=检测区域）")
            calib_msg = gr.Markdown()
            gr.Markdown(
                "预览是**单帧**，看不出时间维度：篮筐下**静止不动的绿点（篮网等）"
                "在正式检测时会被自动滤除**——同一位置持续出现数秒即判为静态物。"
                "只有跟着球移动的绿点才参与进球判定。\n\n"
                "**HSV 微调**：先把「饱和度下限」调小到**球变绿**，"
                "再逐步调大到肤色消失（肤色与球同为橙色相，但饱和度低不少）。"
                "每次重算后会显示 ROI 内命中像素数——**0 命中说明球也被滤掉了**。")
            with gr.Row():
                s_low_slider = gr.Slider(0, 255, value=80, step=1,
                                         label="饱和度 S 下限（调大滤肤色）")
                v_low_slider = gr.Slider(0, 255, value=100, step=1,
                                         label="明度 V 下限")
                btn_refine = gr.Button("重算预览")

            calib_video.change(extract_frame, inputs=calib_video,
                               outputs=[frame_img, pts_state, click_msg,
                                        preview_img, calib_msg])
            calib_path.submit(extract_frame, inputs=calib_path,
                              outputs=[frame_img, pts_state, click_msg,
                                       preview_img, calib_msg])
            btn_from_path.click(extract_frame, inputs=calib_path,
                                outputs=[frame_img, pts_state, click_msg,
                                         preview_img, calib_msg])

            def _jump(uploaded, path_str, t):
                return extract_frame_at(uploaded or path_str, t)

            btn_jump.click(_jump, inputs=[calib_video, calib_path, calib_time],
                           outputs=[frame_img, pts_state, click_msg,
                                    preview_img, calib_msg])
            frame_img.select(add_point, inputs=pts_state,
                             outputs=[pts_state, click_msg])
            btn_clear.click(lambda: ([], click_hint([]), None, None),
                            inputs=None,
                            outputs=[pts_state, click_msg, preview_img, calib_msg])
            btn_calib.click(build_calibration, inputs=[pts_state, frame_img],
                            outputs=[preview_img, calib_msg,
                                     s_low_slider, v_low_slider])
            btn_refine.click(refine_hsv,
                             inputs=[frame_img, s_low_slider, v_low_slider],
                             outputs=[preview_img, calib_msg])

    return demo
```

（上面 `⚙ 标定设置` Tab 内的代码是当前 app.py 559-623 行的逐字拷贝，
仅整体缩进一层不变、Tab 标题从「① 标定（首次使用做一次）」改为
「⚙ 标定设置（首次使用做一次）」——其余一字不改。）

替换后清理：
1. 删除旧的 `② 分析`、`③ 集锦` 两个 Tab 块（功能已被剪辑台取代）
2. 删除 `_cur_row_md`（此时已无调用方）与 `preview_clip`（无调用方，
   其逻辑已并入 `preview_row_click`；若测试仍引用则保留到 Task 9 一并处理）

**前端制作要求（用户指定）**：本任务执行时先调用 frontend-design 技能——
上面给出的组件层级/接线是功能契约（输入输出与事件绑定不得改动），但左栏
宽度比、留白、字号、颜色、折叠分组等视觉与交互细节以 frontend-design 的
指导为准，可在其框架内润色。

- [ ] **Step 2: 跑构建冒烟与全量**

Run: `.venv/Scripts/python -m pytest tests/test_app.py -k build_app -q`
Expected: passed

Run: `.venv/Scripts/python -m pytest tests/ -q`
Expected: 全部 passed（如有失败，通常是旧 Tab 相关残留引用，按报错清理）

- [ ] **Step 3: 手工冒烟（快速）**

Run: `.venv/Scripts/python bbh/app.py`（或 run_app.bat），浏览器打开：
- 首页是双栏剪辑台；填入 `C:\Users\wzx\Desktop\视频\stodownload-30min.mp4`
  → 变更后状态行提示"已载入已有检测结果"，表格出现 4 列数据
- 点任意行 → 右侧播放器出片（静音自动播）、面板显示判定依据与算式、
  ℹ 悬浮出完整数据卡
- 面板改前 10 后 3 → 点「↻ 按此重切预览」→ 播放器与分色条按 10/3 重出
- 点标签按钮 → 表格标签列与面板同步
- 「🎬 生成集锦」→ 成品在预览位播放，「↩ 回到逐条预览」恢复
- 「⚙ 标定设置」Tab 功能照旧（抽帧、点选、HSV 微调）

- [ ] **Step 4: 提交**

```bash
git add bbh/app.py tests/test_app.py
git commit -m "feat: build_app 重排为单页剪辑台 + ⚙ 标定设置 Tab"
```

> **执行偏差（Task 8 审查累积）**：① `_generate_for_ui` 改名 `generate_for_ui`
> （公开 API，测试两处同步）；② 新增 `_hint_html(text)` 统一三处预览位占位并
> 回灌 `_back_to_preview`/`generate_for_ui`；③ `load_events_table` 报错文案与
> 模块 docstring 的旧 Tab 引用一并清理（Files 范围外但属清理必需）。
> frontend-design 吸收：橙色主题 `gr.themes.Soft(primary_hue="orange")`、
> 系统字体栈（不引 Google Fonts）、①②步骤徽章、栏比维持 2:5。Self-Review
> 记录节里的 `_generate_for_ui` 字样同步改为 `generate_for_ui`。

---

### Task 9: 收尾——README 与全量回归

**Files:**
- Modify: `README.md`（使用步骤 1-4 改为单页流程）
- Test: 全量

- [ ] **Step 1: README「使用」节替换**

```markdown
## 使用

1. 双击 `run_app.bat`（首次先完成下方「安装」）
2. **⚙ 标定设置**（首次一次）：上传一段日常短视频或填本机路径 → 抽帧 →
   在帧上依次点击筐口左端、右端、球（1-3 次）→ 生成标定；必要时微调 HSV
3. **剪辑台**（每周主力页）：填视频完整路径 → 已有结果自动载入，否则点
   「▶ 开始检测」（后台续跑，完成后自动载入进球列表）
4. 复核打标：点列表任意行 → 右侧立即预览（分色进度条：蓝=进球前 /
   红线=进球时刻 / 绿=进球后）；面板改单条前后秒后「↻ 重切预览」，
   或用全局前后秒批量；点标签按钮标记谁进的球（新名字回车入库）
   → 🎬 生成集锦 MP4（成品直接在预览位播放；按标签生成输出
   `highlight-<标签>.mp4`）。片段不合并——每个进球独立成段
   - 产物在 `work/<视频名>/highlight.mp4`，单片段在 `work/<视频名>/clips/`
```

- [ ] **Step 2: 全量测试**

Run: `.venv/Scripts/python -m pytest tests/ -q`
Expected: 全部 passed（预计 130+）

- [ ] **Step 3: 真实视频验收（用户参与）**

请用户用 30 分钟真实视频从填路径到出集锦一条龙走一遍，确认：
- 检测进度全程可见、完成自动载入
- 复核全程零滚动（点行/打标/改前后秒/生成都在一屏内）
- ℹ 悬浮卡、判定依据、疑似弹出提示可见
- 旧 tags.json / tag_assignments.json 标签正常恢复

- [ ] **Step 4: 提交**

```bash
git add README.md
git commit -m "docs: README 更新为单页剪辑台使用流程"
```

---

## Self-Review 记录

- **Spec 覆盖**：单页双栏（Task 8）/ 4 列投影+State（Task 1、3）/ ℹ 卡
  （Task 2）/ 三条编辑路径（Task 5）/ 面板前后秒+重切（Task 5）/ 标签
  （Task 6、8）/ 生成切换预览位+回到逐条（Task 4、6、8）/ 自动载入+已有
  探测+置灰锁定（Task 3、7）/ 无标定指引（Task 7）/ 时刻校准折叠（Task 8）/
  标定 Tab 搬迁（Task 8）/ README（Task 9）——全覆盖
- **占位符**：Task 8 标定页为"原样搬迁"指令（引用现有代码行号），非占位
- **类型一致性**：`project_rows/sync_table_edits/recut_current/apply_all_rows/
  on_table_edit/probe_existing/refresh_and_autoload/generate_for_ui/
  _back_to_preview/_cur_panel_html/_info_card/_load_events_or_none` 在各
  Task 间签名与返回元组顺序已逐一核对，与 build_app 接线的 outputs 顺序一致

> **终审修正（整体 code review）**：两条跨任务缝隙——
> ① Task 7 的 `refresh_and_autoload` 空闲分支每 2s 重写 status_md，会把
> `probe_existing` 刚写入的探测提示（「已载入已有检测结果」/「检测结果
> 文件损坏」）顶掉：空闲分支 status_md 改返回 `gr.update()`（空闲初值由
> 组件构造给定，running/done 分支照写实值），并补显式 error 分支写实值——
> 否则错误文案会随空闲 update 一起被吞、永不上屏。
> ② Task 5/6 的 `apply_tag`/`apply_all_rows` 重建面板时不传 reason，打标或
> 全局应用后判定依据从面板消失（想再看必须重点行、重跑数秒 ffmpeg 切片）：
> 从 `_preview_and_reason` 提取纯读文件的 `_reason_for(video_path, rows, i)`
> （文件缺失/行号越界返回空串，面板降级不报错）供三处复用；
> `apply_all_rows` 签名扩为 `(video_path, rows, b, a, cur_row=None)`，
> Task 8 接线 inputs 首位相应加 `video_path_box`。上文 Task 5/6/7 代码块
> 已同步为修正后形态。
