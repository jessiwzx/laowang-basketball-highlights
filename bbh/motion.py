"""镜头位移配准：固定机位中途被碰/缓慢漂移的测量与校正。

原理：以标定参考帧（用户点筐的那帧）为基准，检测期间周期性用 ORB 特征
匹配当前帧与参考帧，取好匹配位移的中位数（抗人群/球运动干扰）作为该时刻
的镜头位移 (dx, dy)；检出坐标减去位移即回到标定坐标系，标定几何
（筐口矩形/ROI/条带）对全程有效。参考帧必须就是点标定的那一帧——
标定几何只在那帧的画面坐标里成立（用户 2026-08-20 指出：不能用视频
首帧，首帧常是调试准备阶段、机位未定）。

2026-08-20 取证 work/_v8_analysis/：720p 机位 t≈835 镜头被碰右跳 +32px、
片尾 dy 漂到 −16px——碰后所有真进球被几何门槛判「从筐旁飞过」降级
（off +0.42~+0.67），校正后 off 回落 −0.12~+0.26、17 条真进球 15 条 high。
"""
import hashlib

import cv2
import numpy as np

SAMPLE_EVERY = 150    # 每 150 绝对帧测一次位移（30fps ≈ 5s 一次）
DOWN_W = 640          # ORB 用的降采样宽度（提速；中位数平移精度 ~2px 够用）
ORB_FEATURES = 2000
LOWE_RATIO = 0.7
MATCH_MIN = 20        # 好匹配数下限——低于视为本帧不可靠（模糊/遮挡），不采样
MAD_MAX_PX = 6.0      # 匹配位移离散度上限（原分辨率像素）——离散大说明匹配被
                      # 运动物体带偏，同样不采样，保持上次位移


def load_ref_image(path: str):
    """读参考帧（中文路径 cv2.imread 会静默失败 → np.fromfile+imdecode）。
    不可读返回 None（调用方降级为不做位移校正，不崩）。"""
    try:
        buf = np.fromfile(path, np.uint8)
    except OSError:
        return None
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return img if img is not None and img.size else None


def save_ref_image(frame_bgr: np.ndarray, path: str) -> str:
    """参考帧落盘 PNG（同样因中文路径用 imencode+tofile），返回内容指纹
    （md5 前 8 位，存进 Calibration.ref_hash——进缓存指纹，换参考帧后
    旧检测结果自动失效）。"""
    ok, buf = cv2.imencode(".png", frame_bgr)
    if not ok:
        raise OSError(f"参考帧 PNG 编码失败: {path}")
    buf.tofile(path)
    return hashlib.md5(buf.tobytes()).hexdigest()[:8]


def _down_gray(bgr: np.ndarray, fx: float) -> np.ndarray:
    small = cv2.resize(bgr, None, fx=fx, fy=fx) if fx < 1.0 else bgr
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)


def interp_shift(samples: list, t: float) -> tuple[float, float]:
    """t 时刻的镜头位移：样本间分段线性插值；首样本前按首样本、
    末样本后保持末样本（漂移是慢变量，采不到的区间就近延伸）。
    纯函数（不依赖参考帧）——net_motion 等后置 pass 直接复用采样表。"""
    s = samples
    if not s:
        return 0.0, 0.0
    if t <= s[0][0]:
        return s[0][1], s[0][2]
    for (t0, x0, y0), (t1, x1, y1) in zip(s, s[1:]):
        if t < t1:
            f = (t - t0) / (t1 - t0)
            return x0 + (x1 - x0) * f, y0 + (y1 - y0) * f
    return s[-1][1], s[-1][2]


class MotionTracker:
    """位移采样与查询。samples: [(t, dx, dy)]，位移为原分辨率像素、
    检测视频坐标系（参考帧与检测视频分辨率不同时按检测帧换算）。"""

    def __init__(self, ref_bgr: np.ndarray, samples: list | None = None):
        self._orb = cv2.ORB_create(ORB_FEATURES)
        self._bf = cv2.BFMatcher(cv2.NORM_HAMMING)
        self._ref_fx = DOWN_W / ref_bgr.shape[1]
        ref_gray = _down_gray(ref_bgr, self._ref_fx)
        self._ref_kp, self._ref_des = self._orb.detectAndCompute(ref_gray, None)
        self._frame_fx = None       # 检测帧缩放比，首个 update 帧确定
        self.samples: list[tuple[float, float, float]] = [
            tuple(s) for s in (samples or [])]

    def update(self, frame_bgr: np.ndarray, t: float) -> bool:
        """测一帧相对参考帧的位移并入表，成功 True。匹配不足或离散过大
        不采样（该时刻位移沿用插值/上次值——镜头不会一帧一变）。"""
        if self._ref_des is None:
            return False
        if self._frame_fx is None:
            self._frame_fx = DOWN_W / frame_bgr.shape[1]
        gray = _down_gray(frame_bgr, self._frame_fx)
        kp, des = self._orb.detectAndCompute(gray, None)
        if des is None or len(des) < MATCH_MIN:
            return False
        matches = self._bf.knnMatch(self._ref_des, des, k=2)
        good = [m for m, n in (p for p in matches if len(p) == 2)
                if m.distance < LOWE_RATIO * n.distance]
        if len(good) < MATCH_MIN:
            return False
        d = np.array([(kp[m.trainIdx].pt[0] - self._ref_kp[m.queryIdx].pt[0],
                       kp[m.trainIdx].pt[1] - self._ref_kp[m.queryIdx].pt[1])
                      for m in good])
        med = np.median(d, axis=0)
        mad = np.median(np.abs(d - med))     # x/y 合并评估的离散度
        if mad / self._frame_fx > MAD_MAX_PX:
            return False
        self.samples.append((float(t),
                             round(float(med[0] / self._frame_fx), 2),
                             round(float(med[1] / self._frame_fx), 2)))
        return True

    def shift(self, t: float) -> tuple[float, float]:
        """t 时刻的镜头位移（插值见 interp_shift）。"""
        return interp_shift(self.samples, t)
