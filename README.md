# RealTalk_AI

基于阿里云百炼（DashScope）的实时多语言语音翻译工具，让说不同语言的人能够直接对话。

- **听译**：对方说英语 / 日语 / 韩语等，实时显示识别原文与中文译文
- **说译**：你输入中文，翻译成目标语言并用该语言的音色朗读出来

> 当前为 MVP 版本（v0.1.0）。核心链路已可用，功能会逐步扩展。

## 目录

- [效果概览](#效果概览)
- [快速开始](#快速开始)
- [技术选型](#技术选型)
- [语种支持矩阵](#语种支持矩阵)
- [命令行工具](#命令行工具)
- [项目结构](#项目结构)
- [配置项](#配置项)
- [安全须知](#安全须知)
- [常见问题](#常见问题)
- [开发](#开发)

## 效果概览

界面分两个页签，对应两个使用方向。

**听译（外语 → 中文）**：点击「开始聆听」后对着麦克风说话，每句话会生成一张卡片。卡片带蓝色边框表示还在识别中、文字会不断刷新；边框变灰表示这句已定稿。卡片上会标注该句译文来自 Gummy 内置翻译还是文本模型补译。

**说译（中文 → 外语）**：输入中文，选择目标语言与音色，点击「翻译并朗读」（或按 `Ctrl+Enter`）。译文会先显示出来，语音随后边合成边播放。

## 快速开始

### 1. 前置条件

- Python 3.10 或更高版本
- 一个可用的麦克风与扬声器
- 阿里云账号，并已开通**百炼（大模型服务平台）**

### 2. 获取 API Key

1. 访问[百炼控制台](https://bailian.console.aliyun.com/)并开通服务
2. 右上角进入「API-KEY」页面，创建并复制 API Key

详见官方文档：[获取 API Key](https://help.aliyun.com/zh/model-studio/get-api-key)

新用户有免费额度（有效期 90 天，仅华北2 北京地域的模型享有）。本项目用到的模型都在北京地域。

### 3. 安装

强烈建议使用虚拟环境，避免 `dashscope` 的依赖（protobuf、rich 等）与你已有的项目冲突：

```bash
git clone https://github.com/<your-name>/RealTalk_AI.git
cd RealTalk_AI

python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

python -m pip install -r requirements.txt
```

Linux 上还需要安装 PortAudio（`sounddevice` 的底层依赖）：

```bash
sudo apt-get install libportaudio2
```

### 4. 配置 API Key

```bash
# Windows PowerShell
Copy-Item .env.example .env
# macOS / Linux
cp .env.example .env
```

编辑 `.env`，把 `DASHSCOPE_API_KEY` 换成你自己的 Key。`.env` 已被 `.gitignore` 忽略，不会被提交。

### 5. 验证环境

```bash
python -m realtalk.cli check
```

这条命令会逐项探测配置、音频设备，以及实时识别、文本翻译、语音合成三个模型的可用性，最后给出两个使用方向各自能否工作的结论。

探测是**逐项独立**的：某个模型不可用不会中断检查，因为百炼的免费额度按模型独立计算，一个模型没额度不代表其他模型也没有。整套检查会产生极少量计费（不到一分钱）。

### 6. 启动

```bash
python main.py
```

## 技术选型

阿里云在语音领域有**两套并行的产品线**，本项目选择了百炼。这个决定影响整个架构，理由如下。

| | 百炼 / DashScope（本项目采用） | 智能语音交互 NLS |
|---|---|---|
| 鉴权 | 单个 API Key，长期有效 | AppKey + Token，Token 36~48 小时过期需自行刷新 |
| 安装 | `pip install dashscope` | PyPI 无官方包，需下载源码本地安装 |
| 多语言识别 | 参数级切换，支持 `source_language="auto"` | **每个语种需在控制台建独立项目、用独立 AppKey** |
| 识别 + 翻译 | Gummy 模型在一条 WebSocket 内同时输出两者 | 无翻译能力，需外接翻译 API |
| 回调数据 | 结构化对象 | JSON 字符串，需自行解析 |

决定性因素是多语言识别方式。NLS 的官方文档明确写着「语音识别服务不支持通过 API 参数动态切换模型，每个项目绑定一个固定的语音识别模型」。而本项目的核心场景恰恰是**事先不知道对方说什么语言**，用 NLS 就必须先猜语种再挑 AppKey，实时场景下不成立。

各环节的具体选择：

| 环节 | 选择 | 说明 |
|---|---|---|
| 实时语音识别 + 翻译 | [`gummy-realtime-v1`](https://help.aliyun.com/zh/model-studio/real-time-python-sdk) | 唯一在单条连接内同时给出识别与翻译的模型，省掉一次网络往返 |
| 文本翻译 | [`qwen-mt-flash`](https://help.aliyun.com/zh/model-studio/qwen-mt-api)，不可用时自动降级 | 官方已标注 `qwen-mt-turbo` 不再更新；flash 支持流式输出，覆盖 92 种语言 |
| 语音合成 | [`cosyvoice-v2`](https://help.aliyun.com/zh/model-studio/cosyvoice-python-sdk) | 输出 PCM 直接喂声卡，无需解码，首字延迟更低 |
| 音频采集 / 播放 | `sounddevice` | 相比 `pyaudio`，在 Windows 与 Python 3.13 上有预编译 wheel，不需要本机编译环境 |

### 为什么需要「兜底翻译」

Gummy 的翻译方向是**有向矩阵**，不是任意语种互译。能直接翻译成中文的源语种只有英、日、韩、法、德、西、俄、意、粤；葡萄牙语、印尼语、阿拉伯语、泰语等按官方文档只能翻到英文。

所以「翻译成中文」这件事不能假定 Gummy 一定能做。`ListenSession` 按源语种自动选择三种策略：

| 场景 | 策略 | 延迟 |
|---|---|---|
| 源语种已知且矩阵支持 | 只用 Gummy 内置翻译 | 最低，无额外往返 |
| 源语种为 `auto` | 开启 Gummy 翻译，对「定稿了却没拿到译文」的句子用 Qwen-MT 补译 | 大部分句子走快路径 |
| 源语种已知但矩阵不支持 | 关闭 Gummy 翻译（避免无效计费），全部交给 Qwen-MT | 译文比原文稍慢出现 |

补译只对定稿句子进行。中间结果每秒刷新多次，逐条送去翻译既昂贵又会让界面文字反复跳动。

界面上每张卡片都会标注该句译文的实际来源，方便你判断当前走的是哪条链路。

### 翻译模型的自动降级

百炼的免费额度是**按模型独立计算**的，不能跨模型共用。这意味着 `qwen-mt-flash` 额度用尽时，`qwen-mt-plus` 往往仍然可用。由于每个用户账号的额度状态各不相同，把模型写死会让别人克隆项目后直接失败。

因此 `TextTranslator` 实现了一条降级链：首选模型因**额度或权限**问题不可用时，自动换到下一个候选（`qwen-mt-plus` → `qwen-mt-turbo`），并记住结果，后续请求直接用可用的那个，不再重复试探。

两个刻意的设计约束：

- **只对可降级错误降级**。额度用尽、模型无权限属于「换个模型可能就好」；网络超时、服务端 500 属于瞬时故障，换模型解决不了，只会掩盖真正的问题，所以这类错误直接上报。
- **降级结果必须缓存**。否则每次请求都要先撞一次不可用的模型，凭空多一个网络往返的延迟。

发生降级时日志会明确提示。想彻底省掉首次试探的那一个请求，在 `.env` 里显式指定可用模型即可：

```bash
REALTALK_MT_MODEL=qwen-mt-plus
```

## 语种支持矩阵

### 听译方向（外语 → 中文）

Gummy 可识别 14 个语种：中文、英语、日语、韩语、粤语、德语、法语、俄语、西班牙语、意大利语、葡萄牙语、印尼语、阿拉伯语、泰语。

| 源语种 | 译文来源 |
|---|---|
| 英、日、韩、法、德、西、俄、意、粤 | Gummy 内置翻译（快路径） |
| 葡、印尼、阿拉伯、泰 | Gummy 转写 + Qwen-MT 补译 |

### 说译方向（中文 → 外语）

受限于 `cosyvoice-v2` 的系统音色。官方系统音色是**单语言绑定**的（日语音色只能念日语），因此目标语言必须有对应音色才能启用。

| 目标语言 | 可用音色 |
|---|---|
| 英语 | Eva、Brian、Luna、Luca、Emily、Eric（均为英式） |
| 日语 | Yuuna、Yuuma、Tomoka、Tomoya |
| 韩语 | Jihun、Kyong |
| 中文 | 龙小淳、Bella |

法语、德语、西班牙语等目前没有对应的 `cosyvoice-v2` 系统音色。要支持它们需要走 `cosyvoice-v3` 系列的声音复刻功能，属于后续计划。

用 `python -m realtalk.cli voices` 可以随时查看完整音色列表。

## 命令行工具

界面之外提供了命令行，排查问题时比 GUI 直观得多：

```bash
python -m realtalk.cli check                      # 检查配置与 API 连通性
python -m realtalk.cli devices                    # 列出录音设备
python -m realtalk.cli voices                     # 列出可用 TTS 音色

python -m realtalk.cli listen                     # 听译，自动检测语种
python -m realtalk.cli listen --source ja         # 指定日语，走 Gummy 快路径
python -m realtalk.cli listen --device 5          # 指定麦克风

python -m realtalk.cli speak "洗手间在哪里？" --target ja
python -m realtalk.cli speak "很高兴见到你" --target en --voice loongbrian_v2

python -m realtalk.cli listen -v                  # 加 -v 输出调试日志
```

`listen` 输出中行首的 `G` 表示译文来自 Gummy 内置翻译，`M` 表示来自文本模型补译。

## 项目结构

```
RealTalk_AI/
├── main.py                  # 图形界面启动入口
├── realtalk/
│   ├── config.py            # 配置加载，API Key 只从环境读取
│   ├── languages.py         # 语种定义、Gummy 翻译方向矩阵、音色映射
│   ├── cli.py               # 命令行入口
│   ├── audio/
│   │   ├── recorder.py      # 麦克风采集（16kHz 单声道 PCM）
│   │   └── player.py        # PCM 流式播放
│   ├── core/                # 业务逻辑，完全不依赖界面
│   │   ├── events.py        # 统一事件模型
│   │   ├── listen.py        # 方向一编排（含降级策略）
│   │   ├── speak.py         # 方向二编排
│   │   ├── translator.py    # Qwen-MT 文本翻译
│   │   └── tts.py           # CosyVoice 语音合成
│   └── ui/                  # PySide6 界面
│       ├── main_window.py
│       ├── listen_page.py
│       ├── speak_page.py
│       └── theme.py
└── tests/                   # 全部不依赖真实 API Key
```

`core/` 与 `ui/` 完全解耦，核心层通过回调向外发事件，不引用任何 PySide6 符号。这样命令行和图形界面能共用同一套逻辑，将来要换成 Web 界面也只需替换 `ui/`。

### 线程模型

这是本项目最容易出错的地方，实现时特别处理过：

- **麦克风采集回调运行在 PortAudio 的实时线程上**，在那里做 WebSocket 发送会造成丢帧和爆音。所以采集回调只把裸字节丢进队列，实际发送由独立的转发线程完成。
- **dashscope 的结果回调运行在 SDK 的接收线程上**，Qt 控件只能在 UI 线程操作。界面层用 Qt `Signal` 做跨线程投递（从任意线程 `emit` 是安全的），所有槽函数因此都跑在 UI 线程。
- **`session.start()` 会阻塞到 WebSocket 建连完成**，不能在 UI 线程调用，否则界面会卡住一两秒。启停都放在后台线程。

## 配置项

所有配置都通过环境变量或 `.env` 提供，优先级为：进程环境变量 > `.env` > 代码内默认值。

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `DASHSCOPE_API_KEY` | 是 | — | 百炼 API Key |
| `REALTALK_ASR_MODEL` | 否 | `gummy-realtime-v1` | 实时识别与翻译模型 |
| `REALTALK_MT_MODEL` | 否 | `qwen-mt-flash` | 文本翻译模型 |
| `REALTALK_TTS_MODEL` | 否 | `cosyvoice-v2` | 语音合成模型 |
| `REALTALK_MAX_END_SILENCE` | 否 | `800` | VAD 断句静音阈值（毫秒，200~6000）。调小则断句更快、交互感更强，但容易把长句切碎 |
| `REALTALK_WEBSOCKET_URL` | 否 | — | 百炼业务空间专属域名，一般无需设置 |

## 安全须知

本项目按开源发布的要求处理凭证：

- API Key **只从环境变量或 `.env` 读取**，代码中没有任何硬编码凭证
- `.env`、`config.yaml` 等可能含密钥的文件已列入 `.gitignore`
- `.env.example` 只含占位值，且配置层会主动拒绝未替换的占位 Key
- `Settings.__repr__` 经过重写，Key 不会出现在日志和异常堆栈里；界面上只展示脱敏后的形式
- 测试用例中有专门断言校验 Key 不会泄漏到 `repr` 和界面文本

提交前建议再确认一次：

```bash
git status --short          # 确认 .env 不在待提交列表里
git diff --cached           # 确认没有 sk- 开头的字符串
```

## 常见问题

**报 403 `Free quota exhausted`**

该模型的免费额度用尽了。免费额度按模型独立计算，所以其他模型很可能仍然可用 —— `check` 会逐项探测并告诉你每个模型的真实状态，不会因为一个模型不可用就中断。

翻译模型会自动降级到候选模型，通常无需干预。如果所有候选都用尽，有两个选择：在百炼控制台为账户充值；或者如果账户开启了「仅使用免费额度」模式，需要在控制台关闭它才会转为按量计费。

**`check` 报其他翻译错误**

依次确认：API Key 是否复制完整、百炼服务是否已开通、是否误用了阿里云 AccessKey（那是 NLS 产品线的凭证，长度约 230 字符，本项目不适用）。

**启动 GUI 时报找不到音频设备**

先跑 `python -m realtalk.cli devices` 确认系统能枚举到麦克风。Windows 上还需在「设置 → 隐私和安全性 → 麦克风」中允许桌面应用访问麦克风。

**识别出的文字断句太长/太碎**

调整 `REALTALK_MAX_END_SILENCE`。默认 800ms 是官方默认值，调到 400~500 会让断句更积极、对话感更强，调到 1500 以上更适合完整表述的场景。

**译文比原文明显滞后**

如果卡片上标注的是「文本模型补译」，说明该语种走的是兜底链路，多了一次翻译请求。改用 Gummy 直接支持的源语种，或在下拉框里明确指定源语种即可走快路径。

**安装后其他项目报依赖冲突**

`dashscope` 会拉取较新的 protobuf 和 rich，可能与 streamlit 等库冲突。请在虚拟环境中安装本项目。

## 开发

```bash
python -m pip install -r requirements.txt
python -m pip install pytest ruff

# 测试全部不依赖真实 API Key，可离线运行
QT_QPA_PLATFORM=offscreen pytest -q      # Windows: $env:QT_QPA_PLATFORM="offscreen"
ruff check realtalk tests
```

`tests/test_languages.py` 锁定了 Gummy 的翻译方向矩阵。该矩阵是从官方文档手抄进代码的，一旦抄错一个语种，降级判断就会失效——本该补译的语种被认为可以直译，结果译文永远为空。改动 `languages.py` 前请先核对[官方文档](https://help.aliyun.com/zh/model-studio/real-time-python-sdk)。

### 后续计划

- [ ] 说译方向支持更多语言（需接入 `cosyvoice-v3` 声音复刻，以获得跨语言一致的音色）
- [ ] 听译结果导出（文本 / 字幕文件）
- [ ] 双向对话模式：两个人各说母语，互相听到自己的语言
- [ ] 热词定制，提升专有名词识别准确率
- [ ] 端到端延迟统计面板

## 许可

[MIT](LICENSE)

## 致谢

本项目的 API 调用方式均依据阿里云官方文档实现：

- [Gummy 实时语音识别与翻译](https://help.aliyun.com/zh/model-studio/real-time-python-sdk)
- [Qwen-MT 机器翻译](https://help.aliyun.com/zh/model-studio/qwen-mt-api)
- [CosyVoice 语音合成](https://help.aliyun.com/zh/model-studio/cosyvoice-python-sdk)
- [CosyVoice 音色列表](https://help.aliyun.com/zh/model-studio/cosyvoice-voice-list)
