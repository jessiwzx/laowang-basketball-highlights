import os

import cv2
import pytest

from bbh.editor import (build_highlight, concat_clips, cut_clip, grab_frame_ffmpeg,
                        plan_clips, probe_duration, transcode_for_detection)


class TestPlanClips纯函数:
    def test_常规切片(self):
        clips = plan_clips([(100.0, 15.0, 5.0)], duration=3600.0)
        assert clips == [(85.0, 105.0)]

    def test_越界裁剪到视频边界(self):
        clips = plan_clips([(3.0, 15.0, 5.0), (3598.0, 15.0, 5.0)], duration=3600.0)
        assert clips[0][0] == 0.0
        assert clips[-1][1] == 3600.0

    def test_重叠片段不合并_各自独立(self):
        """实测反馈：用户不要合并——每个进球独立成段，画面重叠属预期。"""
        clips = plan_clips([(100.0, 15.0, 5.0), (110.0, 15.0, 5.0)], duration=3600.0)
        assert clips == [(85.0, 105.0), (95.0, 115.0)]   # 85-105 与 95-115 各自保留

    def test_不相邻也不合并(self):
        clips = plan_clips([(100.0, 15.0, 5.0), (200.0, 15.0, 5.0)], duration=3600.0)
        assert clips == [(85.0, 105.0), (185.0, 205.0)]


class TestFfmpeg:
    def test_cut_and_concat(self, sample_video, tmp_path):
        cut_clip(sample_video, 2.0, 1.0, str(tmp_path / "c0.mp4"))
        cut_clip(sample_video, 5.0, 1.0, str(tmp_path / "c1.mp4"))
        concat_clips([str(tmp_path / "c0.mp4"), str(tmp_path / "c1.mp4")],
                     str(tmp_path / "out.mp4"))
        import cv2
        cap = cv2.VideoCapture(str(tmp_path / "out.mp4"))
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        assert 45 <= frames <= 75             # 约 2 秒 @30fps，转码有容差

    def test_切片起点零抬到1毫秒(self, monkeypatch, tmp_path):
        """同 app._cut_preview 护栏：HEVC+AAC 手机源 -ss 0 触发 mp4 muxer
        负 DTS 死路（09-11_3 首个进球切片即炸），集锦切片同款命令同样
        恒 >0；真复现需带音轨真机源，此处钉命令行。"""
        import bbh.editor as editor
        seen = []
        monkeypatch.setattr(editor, "_run",
                            lambda cmd, **k: seen.append(cmd))
        editor.cut_clip("v.mp4", 0.0, 7.0, str(tmp_path / "c.mp4"))
        editor.cut_clip("v.mp4", 85.0, 20.0, str(tmp_path / "c2.mp4"))
        sss = [c[c.index("-ss") + 1] for c in seen]
        assert sss == ["0.001", "85.000"]

    def test_单引号路径也能拼接(self, sample_video, tmp_path):
        import os
        d = tmp_path / "it's a dir"
        d.mkdir()
        cut_clip(sample_video, 2.0, 1.0, str(d / "c0.mp4"))
        cut_clip(sample_video, 5.0, 1.0, str(d / "c1.mp4"))
        out = str(d / "out.mp4")
        concat_clips([str(d / "c0.mp4"), str(d / "c1.mp4")], out)
        assert os.path.getsize(out) > 0

    def test_失败时无list文件残留(self, tmp_path):
        out = str(tmp_path / "out.mp4")
        with pytest.raises(RuntimeError):
            concat_clips([str(tmp_path / "不存在.mp4")], out)
        import os
        assert not os.path.exists(out + ".txt")

    def test_空片段列表报错(self, tmp_path):
        with pytest.raises(ValueError):
            build_highlight("whatever.mp4", [], str(tmp_path))


class TestFfmpeg兜底:
    """OpenCV 之外的工具：时长探测 / 管道抽帧 / 转码。"""

    def test_probe_duration(self, sample_video):
        d = probe_duration(str(sample_video))       # 180 帧 @30fps = 6s
        assert d is not None and 5.0 <= d <= 7.0

    def test_grab_frame_ffmpeg取指定时间帧(self, sample_video):
        frame = grab_frame_ffmpeg(str(sample_video), 2.5)
        assert frame is not None and frame.shape[:2] == (360, 640)
        # 灰筐线 y=154 仍在（x=310 处；x=340 在 2.5s 恰被球盖住）
        assert abs(float(frame[154, 310].mean()) - 180) <= 25

    def test_grab_frame_ffmpeg损坏文件返回None(self, tmp_path):
        bad = tmp_path / "bad.mp4"
        bad.write_bytes(b"garbage" * 100)
        assert grab_frame_ffmpeg(str(bad), 1.0) is None

    def test_transcode输出OpenCV可读且无part残留(self, sample_video, tmp_path):
        dst = tmp_path / "det-source.mp4"
        transcode_for_detection(str(sample_video), str(dst))
        cap = cv2.VideoCapture(str(dst))
        try:
            assert cap.isOpened()
            ok, frame = cap.read()
            assert ok and frame.shape[:2] == (360, 640)
        finally:
            cap.release()
        assert not os.path.exists(str(dst) + ".part")

    def test_transcode损坏文件报ffmpeg错误(self, tmp_path):
        bad = tmp_path / "bad.mp4"
        bad.write_bytes(b"x" * 256)
        with pytest.raises(RuntimeError, match="ffmpeg"):
            transcode_for_detection(str(bad), str(tmp_path / "out.mp4"))
