# 第三方组件与许可声明

本文件说明本仓库中**哪些内容适用哪个许可**。项目整体采用 [MIT](LICENSE)，
但它**不覆盖**下列第三方组件与模型权重。

## 一、本仓库自有内容

| 内容 | 许可 | 说明 |
|---|---|---|
| 源代码（`bbh/`、`frontend/`、`tests/`、`tools/`、`scripts/`） | **MIT** | 见 [LICENSE](LICENSE) |
| 文档（`README.md`、`docs/`、本文件） | **MIT** | 同上 |
| `bbh/weights/best.pt`（自训检球/检筐权重） | **MIT** | 由本项目作者用自己的比赛视频标注数据训练，随仓库按 MIT 提供。**训练数据不公开**，所以别人无法复现训练过程，只能直接使用权重。 |

## 二、运行时依赖（Python）

版本号与 `requirements.txt` 一致，许可取自各包发布的元数据。

| 包 | 版本 | 许可 |
|---|---|---|
| fastapi | 0.141.1 | MIT |
| starlette | 0.52.1 | BSD-3-Clause |
| uvicorn | 0.52.3 | BSD-3-Clause |
| pydantic | 2.12.3 | MIT |
| numpy | 2.5.2 | BSD-3-Clause（含 0BSD / MIT / Zlib 等子组件） |
| opencv-python | 5.0.0.93 | Apache-2.0 |
| pillow | 11.3.0 | MIT-CMU |
| imageio-ffmpeg | 0.6.0 | BSD-2-Clause |
| torch | 2.5.1+cu121 | BSD-3-Clause |
| torchvision | 0.20.1+cu121 | BSD-3-Clause |
| **ultralytics** | **8.4.163** | **AGPL-3.0** ← 见第四节 |
| httpx（测试） | 0.28.1 | BSD-3-Clause |
| pytest（测试） | 9.1.1 | MIT |

### FFmpeg 可执行文件

`imageio-ffmpeg` 会下载并随包提供**预编译的 FFmpeg 可执行文件**。该二进制**不受
imageio-ffmpeg 自身的 BSD-2-Clause 覆盖**，其许可（LGPL / GPL，取决于构建配置）
以 [FFmpeg 官方说明](https://ffmpeg.org/legal.html) 为准。**若你要把本项目打包成
闭源商业产品分发，必须单独核对 FFmpeg 的许可条款。**

## 三、前端依赖

前端构建产物（`bbh/static/`）已随仓库提供，**使用者不需要安装 Node 环境**。
仅在修改前端源码并重新构建时才会用到下列依赖：

| 包 | 用途 | 许可 |
|---|---|---|
| vue 3 | UI 框架 | MIT |
| element-plus | 组件库 | MIT |
| @element-plus/icons-vue | 图标 | MIT |
| vite / @vitejs/plugin-vue | 构建工具 | MIT |

## 四、⚠️ 关于 Ultralytics（AGPL-3.0）

这是本项目**唯一需要注意的许可冲突点**，请务必读这一节。

- 本项目通过 `bbh/detector_yolo.py` 直接 `import ultralytics` 完成检球与检人；
- Ultralytics YOLOv8 的许可为 **AGPL-3.0**（或需购买其商业许可）；
- AGPL-3.0 具有**传染性**：以 AGPL 组件为基础的网络服务/分发物，通常需要整体
  以 AGPL-3.0 提供源码。

因此：**本仓库根目录的 MIT 许可，仅就作者原创代码部分作出声明**。严格的合规做法
是把整个项目按 AGPL-3.0 发布。作者选择保留 MIT 的原因是：本项目定位为本地
（local-first）工具，绝大多数使用场景是自己用或开源分发，不构成 AGPL 意义上的
网络服务分发。

**如果你打算据此做闭源商业产品或对外提供付费服务，请自行完成合规判断，或向
Ultralytics 购买商业许可。** 仓库作者不为使用者的合规判断承担责任。

## 五、模型权重与训练数据

| 权重 | 来源 | 许可 |
|---|---|---|
| `bbh/weights/best.pt` | 本项目作者自训（类名 ball / hoop / person，判定只用 ball 与 hoop） | MIT（随仓库提供） |
| `bbh/weights/yolov8n.pt` | Ultralytics 官方 COCO 预训练权重，仅用于检人环节 | 受 Ultralytics AGPL-3.0 约束 |

**训练数据不公开**：`best.pt` 的训练集来自作者自己的比赛视频标注，包含真实人物
画面，因此不随仓库发布。这意味着：

1. 你可以直接使用权重，但**无法复现训练过程**；
2. 权重在**作者自己的场地/机位/球色**上训练，换场地后效果可能明显下降；
3. `best.pt` 的 checkpoint 元数据中可能残留作者训练时的本地目录字符串（不含任何
   个人身份信息），不影响加载与推理。

## 六、示例素材

本仓库**不包含**任何比赛视频、球员照片或视频帧截图。README 与代码中的时间戳、
影片名等均为评测用的标识符，不指向可获取的视频文件。

---

如需就许可问题联系，请在本仓库开 issue。
