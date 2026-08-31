# RealTalk_AI

基于阿里云百炼（DashScope）的实时对话翻译工具。和听不懂中文的人面对面交流时，双方各说各的母语。

- 对方说英语（或日语、韩语），你**看到中文**
- 你说中文，对方**听到他的母语**

不需要打字，两个方向都是开口说话。

> 当前为 MVP 版本（v0.2.0）。英语链路已完整验证可用。

## 目录

- [它是怎么用的](#它是怎么用的)
- [快速开始](#快速开始)
- [技术选型](#技术选型)
- [两种对话模式](#两种对话模式)
- [语种支持](#语种支持)
- [文档没写、靠实测得出的结论](#文档没写靠实测得出的结论)
- [命令行工具](#命令行工具)
- [项目结构](#项目结构)
- [配置项](#配置项)
- [安全须知](#安全须知)
- [常见问题](#常见问题)
- [开发](#开发)

## 它是怎么用的

界面是一整块对话记录，对方的话靠左、你的话靠右，像聊天窗口。每条消息上面是**原文**（小字，用来确认识别得对不对），下面是**译文**（大字，你真正要读的内容）。

默认是**手动模式**，底部两个大按钮控制当前轮到谁说：

- 点「**对方说（英语）**」→ 开始听对方讲英语，识别结果和中文译文实时出现在左侧
- 点「**我说（中文）**」→ 开始听你讲中文，每说完一句就自动翻译成英语并**朗读给对方听**

点击其中一个会自动结束另一方的轮次。你说的每句话念完后，气泡上会出现「重新朗读」，对方没听清时可以重放。

勾选顶部的「**自动识别说话人**」后切换到**自动模式**：两个按钮合并成一个「开始对话」，之后双方直接说话即可，系统自己判断每句是谁说的，不用再碰界面。

## 快速开始

### 1. 前置条件

- Python 3.10 或更高版本
- 可用的麦克风与扬声器
- 阿里云账号，并已开通**百炼（大模型服务平台）**

### 2. 获取 API Key

1. 访问[百炼控制台](https://bailian.console.aliyun.com/)并开通服务
2. 右上角进入「API-KEY」页面，创建并复制 API Key

详见官方文档：[获取 API Key](https://help.aliyun.com/zh/model-studio/get-api-key)

新用户有免费额度（有效期 90 天，仅华北2 北京地域的模型享有）。本项目用到的模型都在北京地域。

### 3. 安装

强烈建议使用虚拟环境，避免 `dashscope` 的依赖（protobuf、rich 等）与你已有的项目冲突：

```bash
git clone https://github.com/magau123/RealTalk_AI.git
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

这条命令会逐项探测配置、音频设备，以及实时识别、文本翻译、语音合成三个模型的可用性，最后给出结论。

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

决定性因素是识别模型的绑定方式。NLS 的官方文档明确写着「语音识别服务不支持通过 API 参数动态切换模型，每个项目绑定一个固定的语音识别模型」。对话场景需要在中文和外语之间来回切换识别语种，用 NLS 就得为每个语种维护一套 AppKey 并在代码里切换，非常笨重。

各环节的具体选择：

| 环节 | 选择 | 说明 |
|---|---|---|
| 识别 + 翻译 | [`gummy-realtime-v1`](https://help.aliyun.com/zh/model-studio/real-time-python-sdk) | 唯一在单条连接内同时给出识别与翻译的模型。对话的两个方向都用它，只是源语言和目标语言对调 |
| 语音合成 | [`cosyvoice-v2`](https://help.aliyun.com/zh/model-studio/cosyvoice-python-sdk) | 输出 PCM 直接喂声卡，无需解码，首字延迟更低 |
| 文本翻译 | [`qwen-mt-flash`](https://help.aliyun.com/zh/model-studio/qwen-mt-api)，不可用时自动降级 | 对话主链路用不到它，仅用于命令行诊断，以及 Gummy 无法直译时的兜底 |
| 音频采集 / 播放 | `sounddevice` | 相比 `pyaudio`，在 Windows 与 Python 3.13 上有预编译 wheel，不需要本机编译环境 |

对话的两个方向都只用 Gummy 一条 WebSocket 完成识别和翻译，不需要额外的翻译请求。这是延迟最低的路径。

### 翻译模型的自动降级

百炼的免费额度是**按模型独立计算**的，不能跨模型共用。这意味着 `qwen-mt-flash` 额度用尽时，`qwen-mt-plus` 往往仍然可用。由于每个用户账号的额度状态各不相同，把模型写死会让别人克隆项目后直接失败。

因此 `TextTranslator` 实现了一条降级链：首选模型因**额度或权限**问题不可用时，自动换到下一个候选（`qwen-mt-plus` → `qwen-mt-turbo`），并记住结果，后续请求直接用可用的那个，不再重复试探。

两个刻意的设计约束：

- **只对可降级错误降级**。额度用尽、模型无权限属于「换个模型可能就好」；网络超时、服务端 500 属于瞬时故障，换模型解决不了，只会掩盖真正的问题，所以这类错误直接上报。
- **降级结果必须缓存**。否则每次请求都要先撞一次不可用的模型，凭空多一个网络往返的延迟。

想彻底省掉首次试探的那一个请求，在 `.env` 里显式指定可用模型即可：

```bash
REALTALK_MT_MODEL=qwen-mt-plus
```

## 两种对话模式

两种模式的共同前提：Gummy 的翻译目标语言**在建立连接时就固定了**，一条连接只能有一个翻译方向。因此不存在「两条链路同时开着」的选项 —— 那样你说的中文会被「外语→中文」那条也听进去按外语强行拟合成乱码，而且 Gummy 的识别与翻译分别计费，开两条就是四份费用。

### 手动模式

两个按钮显式切换发言人，各自开一条方向固定的连接：对方说话时是 `外语→中文`，你说话时是 `中文→外语`。

优点是**延迟最低且不会认错语种**。两个方向的翻译都由 Gummy 在同一条连接内完成，没有任何额外的网络往返，源语种也是写死的，不存在误判。

### 自动模式

单条连接，`source_language="auto"`，翻译目标**固定为中文**。每句话定稿后，根据识别文本判断是谁说的：

- 判定为对方 → Gummy 已经给出中文译文，直接显示，延迟与手动模式完全相同
- 判定为你 → 额外做一次中译外，再合成语音

反过来把目标固定为外语是行不通的：那样对方的话会被翻成他自己的语言，等于没翻。所以只能固定中文，让"你说的话"走补翻译。这样安排的好处是，**额外那一跳只压在你这一侧，而你这一侧本来就要等语音合成**，多出的延迟被掩盖掉了。

判断说话人用的是**书写系统**而不是语种识别接口：假名、谚文、汉字分属不同的 Unicode 区块，判据是确定性的，既不花钱也没有网络延迟，准确率还更高。判断顺序不能颠倒 —— 日文和中文共用汉字区块，必须先查假名，否则「これは本です」会因为含「本」被误判成中文。

已知局限：整句只有汉字、不含任何假名的日文会被误判为中文，例如单独说「東京駅」。完整句子里几乎总有助词假名，所以风险主要集中在单词级的短应答上。遇到了切回手动模式即可。

### 回声抑制

两种模式都要处理同一个问题：扬声器播放译文时，麦克风会把这段声音重新收进来，识别器会把"自己刚说出去的话"当成有人在讲，再翻译一遍显示出来。

处理办法是播放期间用静音帧**替换**麦克风数据（`ListenSession.mute()`）。注意是替换而不是停发 —— 服务端有 23 秒收不到数据就断开的限制，真停发的话，念一段长句子就可能把连接饿死。这一点有专门的测试覆盖，因为它在真机上不容易稳定复现，一旦失效却很难定位。

## 语种支持

对话是双向的，一个语种要能用必须**同时满足三个条件**：

1. Gummy 支持「该语种 → 中文」直译（听懂对方）
2. Gummy 支持「中文 → 该语种」直译（把你的话翻给对方）
3. 有可用的 CosyVoice 音色（把译文读出来）

三者取交集后，当前可用的是：

| 语种 | 对方听到的音色 |
|---|---|
| 英语 | Eva、Brian、Luna、Luca、Emily、Eric（均为英式） |
| 日语 | Yuuna、Yuuma、Tomoka、Tomoya |
| 韩语 | Jihun、Kyong |

**西班牙语暂不支持**，原因是第三条：Gummy 的双向翻译都没问题，但 `cosyvoice-v2` 没有西班牙语系统音色，翻出来的西语读不出来。要支持它需要走 `cosyvoice-v3` 系列的声音复刻功能，属于后续计划。法语、德语、俄语、意大利语同理。

这个交集在 `languages.conversation_languages()` 里是**算出来的**而不是硬编码的，将来往音色表或翻译矩阵里加语种时，不会因为漏掉某个条件而在运行时才暴露问题。

## 文档没写、靠实测得出的结论

以下几点在阿里云官方文档里查不到，但每一条都直接决定了实现方式。都是实测验证过的，记在这里免得后来者重新踩一遍。

**`source_language="auto"` 是按句检测的，不是整条连接锁定一次。**

文档对 auto 的全部描述只有一句「如果无法提前确定语种，可不设置」，没提检测粒度。这个未知量决定了自动模式能否成立：若是连接级锁定，开头听到英语就会锁死，后面说的中文会被按英语强行拟合成乱码。实测在一条连接里依次送入「英文、中文、英文、中文」四段语音，四句都被正确识别并各自翻译，确认是**句子级检测**。

**源语种等于目标语种时，服务端原样返回原文，不是返回空。**

自动模式的翻译目标固定为中文，你说中文时 Gummy 实际在做中译中。实测它会把原文照抄回来当作"译文"。这个结果必须显式丢弃 —— 否则外语音色会拿着中文文本去念，对方完全听不懂。代码里 `_handle_auto_sentence` 专门做了这件事，并有测试锁定。

**识别结果里没有「检测到的语种」字段。**

`TranscriptionResult` 只有 `sentence_id`、`text`、`is_sentence_end` 等，没有任何语种信息。`Translation.lang` 是**翻译目标语种**，不是检测出的源语种，不能拿来判断说话人。这就是本项目用书写系统判断而不是读取字段的原因。

**`stop()` 可能永久阻塞。**

文档只说它「阻塞至 `on_complete` 或 `on_error` 被调用」，**没有超时参数**，SDK 源码里是个无超时的 `join()`。服务端在异常情况下可能永远不下发结束指令，此时调用线程就永久挂死。关闭窗口走的正是这条路径，卡住的话整个界面冻结，只能强杀进程 —— 开发过程中真的遇到了。现在 `_safe_stop_recognizer` 把它放在守护线程里等一个上限（5 秒），超时就放弃这条连接。

**23 秒收不到音频，服务端会断开连接。**

SDK 里同样硬编码了 `SILENCE_TIMEOUT_S = 23`。这意味着任何「暂停发送音频」的想法都有时限，回声抑制才改成发静音帧而不是停发。

**识别器对象不可复用。**

`stop()` 之后该对象即作废，再次使用会抛 `InvalidParameter`。每轮对话都必须新建 `TranslationRecognizerRealtime` 实例。

## 命令行工具

界面之外提供了命令行，排查问题时比 GUI 直观得多。它直接操作单向链路，可以把问题定位到具体环节：

```bash
python -m realtalk.cli check                      # 逐项探测配置与三个模型
python -m realtalk.cli devices                    # 列出录音设备
python -m realtalk.cli voices                     # 列出可用 TTS 音色

python -m realtalk.cli listen --source en         # 只测「听懂对方」这一半
python -m realtalk.cli listen --source ja
python -m realtalk.cli listen --device 5          # 指定麦克风

# 只测「翻译并朗读」这一半（从文本出发，跳过语音识别）
python -m realtalk.cli speak "洗手间在哪里？" --target en

python -m realtalk.cli check -v                   # 加 -v 输出调试日志
```

## 项目结构

```
RealTalk_AI/
├── main.py                     # 图形界面启动入口
├── realtalk/
│   ├── config.py               # 配置加载，API Key 只从环境读取
│   ├── languages.py            # 语种定义、Gummy 翻译方向矩阵、音色映射
│   ├── cli.py                  # 命令行入口
│   ├── audio/
│   │   ├── recorder.py         # 麦克风采集（16kHz 单声道 PCM）
│   │   └── player.py           # PCM 流式播放（线程安全）
│   ├── core/                   # 业务逻辑，完全不依赖界面
│   │   ├── conversation.py     # 对话会话，产品主入口
│   │   ├── listen.py           # 单向链路：语音 → 识别 → 翻译 → 文本
│   │   ├── speak.py            # 单向链路：文本 → 翻译 → TTS → 播放
│   │   ├── translator.py       # Qwen-MT 文本翻译（含降级链）
│   │   ├── tts.py              # CosyVoice 语音合成
│   │   └── events.py           # 统一事件模型
│   └── ui/                     # PySide6 界面
│       ├── conversation_page.py
│       ├── main_window.py
│       └── theme.py
└── tests/                      # 全部不依赖真实 API Key
```

`ConversationSession` 把两条单向链路组织成一场半双工对话：轮到对方说时用 `ListenSession(外语→中文)`，轮到你说时用 `ListenSession(中文→外语)` 并在每句定稿后交给 TTS 朗读。

`core/` 与 `ui/` 完全解耦，核心层通过回调向外发事件，不引用任何 PySide6 符号。命令行和图形界面因此能共用同一套逻辑，将来要换成 Web 界面也只需替换 `ui/`。

### 线程模型

这是本项目最容易出错的地方，几处都踩过坑并做了针对性处理：

- **麦克风采集回调运行在 PortAudio 的实时线程上**，在那里做 WebSocket 发送会造成丢帧和爆音。所以采集回调只把裸字节丢进队列，实际发送由独立的转发线程完成。
- **dashscope 的结果回调运行在 SDK 的接收线程上**，Qt 控件只能在 UI 线程操作。界面层用 Qt `Signal` 做跨线程投递（从任意线程 `emit` 是安全的），所有槽函数因此都跑在 UI 线程。
- **`session.start_turn()` 会阻塞到 WebSocket 建连完成**，不能在 UI 线程调用，否则界面会卡住一两秒。启停都放在后台线程。
- **`PcmStreamPlayer` 会被三个线程同时碰**：合成回调线程调 `start`/`feed`，业务线程调 `finish`，关窗口的线程调 `stop`。早期版本没有生命周期锁，`finish` 正在 join 时 `stop` 去 close 同一个流，PortAudio 因访问已释放的流让整个进程崩溃（Windows 上是访问违例）。现在用一把 `RLock` 串行化生命周期操作，且**只有确认播放线程已退出才关闭音频流**，join 超时时宁可泄漏也不 close。
- **`recognizer.stop()` 必须加超时保护**，它在 SDK 里是无超时的 `join()`，详见上一节。
- **朗读线程有全局异常兜底**。它一旦因异常退出，后续所有译文都不会再被朗读，而且失败是静默的 —— 这类 bug 极难察觉，所以宁可记日志继续处理下一条。

## 配置项

所有配置都通过环境变量或 `.env` 提供，优先级为：进程环境变量 > `.env` > 代码内默认值。

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `DASHSCOPE_API_KEY` | 是 | — | 百炼 API Key |
| `REALTALK_ASR_MODEL` | 否 | `gummy-realtime-v1` | 实时识别与翻译模型 |
| `REALTALK_MT_MODEL` | 否 | `qwen-mt-flash` | 文本翻译模型 |
| `REALTALK_TTS_MODEL` | 否 | `cosyvoice-v2` | 语音合成模型 |
| `REALTALK_MAX_END_SILENCE` | 否 | `800` | VAD 断句静音阈值（毫秒，200~6000）。调小则断句更快、对话感更强，但容易把长句切碎 |
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
git diff --cached           # 确认没有 sk- 开头的真实字符串
```

## 常见问题

**报 403 `Free quota exhausted`**

该模型的免费额度用尽了。免费额度按模型独立计算，所以其他模型很可能仍然可用 —— `check` 会逐项探测并告诉你每个模型的真实状态。

翻译模型会自动降级到候选模型。如果所有候选都用尽，有两个选择：在百炼控制台为账户充值；或者如果账户开启了「仅使用免费额度」模式，需要在控制台关闭它才会转为按量计费。

**找不到音频设备**

先跑 `python -m realtalk.cli devices` 确认系统能枚举到麦克风。Windows 上还需在「设置 → 隐私和安全性 → 麦克风」中允许桌面应用访问麦克风。

**断句太长或太碎**

调整 `REALTALK_MAX_END_SILENCE`。默认 800ms 是官方默认值，调到 400~500 会让断句更积极、对话感更强，调到 1500 以上更适合完整表述的场景。

需要注意 Gummy 的 VAD 判据是「音频里出现了超过阈值的静音」，而不是「没有音频了」。对着麦克风说话时静音也在持续采集，所以断句正常；但如果将来要加「翻译音频文件」功能，文件播完后必须再补几帧静音，最后一句才会定稿。

**对方听到系统把自己刚说的话又翻译了一遍**

这是回声。正常情况下播放译文时麦克风输入会被自动屏蔽，如果仍然出现，可能是外放音量过大或麦克风灵敏度过高，建议改用耳机。

**自动模式把日语当成了中文**

判断说话人靠的是书写系统，整句只有汉字、不含假名的日文（例如单独说「東京駅」）会被误判。完整句子里几乎总有助词假名，所以主要影响单词级的短应答。取消勾选「自动识别说话人」切回手动模式即可。

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

`tests/test_languages.py` 锁定了 Gummy 的翻译方向矩阵。该矩阵是从官方文档手抄进代码的，一旦抄错一个语种，降级判断就会失效 —— 本该补译的语种被认为可以直译，结果译文永远为空。这种 bug 不会报错，只会静默失效。改动 `languages.py` 前请先核对[官方文档](https://help.aliyun.com/zh/model-studio/real-time-python-sdk)。

### 后续计划

- [ ] 支持西班牙语、法语、德语等（需接入 `cosyvoice-v3` 声音复刻以获得音色）
- [x] 自动识别当前说话人，免去手动切换
- [ ] 对话记录导出
- [ ] 热词定制，提升人名、地名、专业术语的识别准确率
- [ ] 端到端延迟统计面板

## 许可

[MIT](LICENSE)

## 致谢

本项目的 API 调用方式均依据阿里云官方文档实现：

- [Gummy 实时语音识别与翻译](https://help.aliyun.com/zh/model-studio/real-time-python-sdk)
- [Qwen-MT 机器翻译](https://help.aliyun.com/zh/model-studio/qwen-mt-api)
- [CosyVoice 语音合成](https://help.aliyun.com/zh/model-studio/cosyvoice-python-sdk)
- [CosyVoice 音色列表](https://help.aliyun.com/zh/model-studio/cosyvoice-voice-list)
