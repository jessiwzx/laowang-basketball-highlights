# bbh/manual_groups.py
"""手动归人（spec 第二轮修订一等公民）：manual_groups.json 读写+归人/改判/撤销。
身份只挂视频名+球 key——与 features/groups 版本完全解耦，特征重算不作废
（手动标签是事实不是缓存）。"""
import json
import os

GROUPS_FILE = "manual_groups.json"


def load(work_dir) -> dict:
    """{person: [球key,...]}；损坏 → 先备份 .corrupt.bak 再返回 {}（spec：
    损坏→备份后重建，原文可人工找回）；缺失 → {} 不产 .bak（纯缺失≠损坏，
    FileNotFoundError 也走 OSError——挪之前先确认文件存在）。UI 永不崩。"""
    p = os.path.join(work_dir, GROUPS_FILE)
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        if os.path.exists(p):      # 文件在却读/解析不了=损坏 → 备份后重建
            try:
                os.replace(p, p + ".corrupt.bak")
            except OSError:        # 挪不动（占用/只读）也照样返回 {}，不炸 UI
                pass
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(p): [str(k) for k in ks]
            for p, ks in data.items() if isinstance(ks, list)}


def save(work_dir, groups: dict) -> None:
    os.makedirs(work_dir, exist_ok=True)
    p = os.path.join(work_dir, GROUPS_FILE)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, p)


def assign(work_dir, person: str, keys: list) -> dict:
    """归人/改判：keys 并入 person 并从其他人移出（球同时只能在一人）。
    空人名/空 keys → 原样返回不落盘。返回更新后 groups。"""
    if not person or not keys:
        return load(work_dir)
    groups = load(work_dir)
    keys = list(dict.fromkeys(keys))                 # 去重保序
    ks = set(keys)
    kept = [k for k in groups.get(person, []) if k not in ks]
    for p in list(groups):
        groups[p] = [k for k in groups[p] if k not in ks]
        if not groups[p]:
            del groups[p]
    # 本次勾选的球按勾选序在前，该人原有其余球保序在后（plan 单测口径：
    # 改判后 ["01:40.0","02:00.0"]，不是保留旧位的 ["02:00.0","01:40.0"]）
    groups[person] = keys + kept
    save(work_dir, groups)
    return groups


def unassign(work_dir, keys: list) -> dict:
    """移出：keys 从任何人的组里删除（只动 manual_groups，不碰已打标签）。"""
    if not keys:
        return load(work_dir)
    groups = load(work_dir)
    ks = set(keys)
    for p in list(groups):
        groups[p] = [k for k in groups[p] if k not in ks]
        if not groups[p]:
            del groups[p]
    save(work_dir, groups)
    return groups
