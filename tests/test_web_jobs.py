"""job 注册表：后台跑、进度上报、同类单飞、异常带回文案。"""
import threading
import time

from bbh import _gr_compat as gr
import pytest

from bbh.web.jobs import JobBusy, JobRegistry


def _wait(reg, job, timeout=5.0):
    t0 = time.time()
    while reg.get(job.id).state == "running" and time.time() - t0 < timeout:
        time.sleep(0.02)


def test_worker_runs_and_reports_progress():
    reg = JobRegistry()

    def worker(report):
        report(msg="第一步", pct=10)
        report(msg="第二步", pct=60, found=3)
        return {"ok": True}

    job = reg.start("gen", worker)
    _wait(reg, job)
    j = reg.get(job.id)
    assert j.state == "done" and j.pct == 100
    assert j.result == {"ok": True}
    assert j.found == 3          # 末次上报的球数留着，翻看历史用
    assert j.as_dict()["done"] is True


def test_worker_exception_becomes_error_job():
    reg = JobRegistry()

    def worker(report):
        raise RuntimeError("炸了")

    job = reg.start("detect", worker)
    _wait(reg, job)
    assert reg.get(job.id).state == "error"
    assert "炸了" in reg.get(job.id).error


def test_gr_error_message_is_user_facing():
    reg = JobRegistry()

    def worker(report):
        raise gr.Error("本视频还没标定——先去「⚙ 标定设置」完成一次")

    job = reg.start("wall_scan", worker)
    _wait(reg, job)
    assert reg.get(job.id).error == "本视频还没标定——先去「⚙ 标定设置」完成一次"


def test_same_kind_is_single_flight_until_it_finishes():
    reg = JobRegistry()
    gate = threading.Event()

    def slow(report):
        gate.wait(2.0)
        return None

    job = reg.start("detect", slow)
    with pytest.raises(JobBusy):
        reg.start("detect", lambda r: None)
    # 别的种类不受影响，可以并行
    other = reg.start("gen", lambda r: None)
    _wait(reg, other)
    assert reg.get(other.id).state == "done"
    gate.set()
    _wait(reg, job)
    # 跑完释放，同类可以再启动
    job2 = reg.start("detect", lambda r: None)
    _wait(reg, job2)
    assert reg.get(job2.id).state == "done"
