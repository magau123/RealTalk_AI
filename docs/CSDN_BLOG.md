# RealTalk_AI：基于 Qwen3.5 LiveTranslate 的实时双向语音翻译工具

> 开源地址：<https://github.com/magau123/RealTalk_AI>  
> 技术栈：Python、PySide6、Qwen3.5 LiveTranslate、Qwen-MT、CosyVoice、WASAPI  
> 开源协议：MIT License

## 一、项目介绍

在跨语言会议、海外直播、视频课程或面对面沟通中，普通翻译软件经常需要反复复制文本、切换窗口，或者只能完成单向翻译。RealTalk_AI 将语音识别、机器翻译、语音合成和桌面字幕整合到一个应用中，让沟通过程尽量接近自然对话。

它解决两个最直接的问题：

1. 对方说英语、日语等外语时，程序实时显示原文和中文译文。
2. 用户需要回复时，只要说中文，程序就会翻译成选定的外语并朗读给对方。

项目完全开源，代码地址：

**https://github.com/magau123/RealTalk_AI**

当前版本定位为可运行的 Alpha / MVP，Windows 下支持麦克风和 WASAPI 系统声音回环；macOS 与 Linux 可使用麦克风输入。

## 二、适合哪些场景

### 1. 观看海外直播或视频

选择电脑的 WASAPI 回环设备，程序可以直接采集播放器正在输出的声音，将英语或日语实时翻译成中文。打开字幕模式后，窗口会缩小并保持置顶，只显示当前一句，不需要频繁切换播放器。

### 2. 跨语言面对面交流

对方讲话时开启检测，你可以直接阅读中文；需要回答时点击“我要说中文”，说完后系统会把译文朗读出来。

### 3. 国际会议和远程沟通

RealTalk_AI 可以作为辅助字幕工具。它不会加入会议平台，也不读取聊天账号，只处理用户选择的本机音频设备。

### 4. 外语学习

每条记录同时保留识别原文和中文译文，可用于核对发音、理解句意和积累表达。

## 三、主要功能

- 一键开始或关闭实时检测
- 实时模型自动识别外语，不需要为英语和日语准备两个按钮
- 同时显示外语原文和中文译文
- 中文语音回复、外语语音合成和重新朗读
- Windows 系统声音回环采集
- 麦克风与系统声音分别选择
- 置顶字幕小窗，只显示当前语音段落
- 50%–100% 窗口透明度调节
- 翻译模型额度或权限异常时自动降级
- API Key 环境变量管理与脱敏显示
- CLI 配置、设备和模型检查工具

## 四、项目流程图

![RealTalk_AI 实时语音翻译流程](https://raw.githubusercontent.com/magau123/RealTalk_AI/main/docs/assets/realtalk-workflow.svg)

> 如果 CSDN 编辑器无法直接加载远程 SVG，可将仓库中的 `docs/assets/realtalk-workflow.svg` 上传到文章图片空间后替换链接。

完整的 Archify 交互图位于：

`docs/assets/realtalk-workflow.html`

交互版本支持缩放、搜索、聚焦节点和关系追踪，下载后使用浏览器打开即可。

## 五、整体架构

RealTalk_AI 有两条主要链路。

### 听译链路：外语转换为中文

1. 从麦克风或 Windows WASAPI 回环读取音频。
2. 将设备音频重采样为 16 kHz、单声道 PCM。
3. 通过 WebSocket 流式发送到 Qwen3.5 LiveTranslate。
4. 服务端持续返回语音转写事件和中文翻译事件。
5. 本地将 `item_id` 与 `response_id` 按出现顺序配对为同一句。
6. PySide6 界面原地刷新当前记录；字幕模式只展示最新一句。

### 回复链路：中文转换为外语语音

1. 用户点击“我要说中文”，应用暂停原来的听译通道。
2. 中文语音通过实时模型识别并翻译为回复语言。
3. 实时翻译缺失时，由 Qwen-MT 补译。
4. CosyVoice 将译文合成为 PCM 音频。
5. 本机扬声器流式播放给对方。
6. 说完后可以恢复听译。

## 六、为什么选择这些模型

### Qwen3.5 LiveTranslate

主听译链路使用 `qwen3.5-livetranslate-flash-realtime`。它在一条 WebSocket 连接中完成实时语音识别和翻译，并使用 `qwen3-asr-flash-realtime` 生成转写文本。相比“先完整识别、再调用文本翻译”的串行方案，它减少了一次等待。

### Qwen-MT

`qwen-mt-flash` 主要用于中文回复的补译。由于百炼免费额度按模型独立计算，项目在额度或权限类错误时会依次尝试其他候选模型，并缓存可用结果，避免每句话都重复撞一次失败请求。

### CosyVoice

语音合成使用 `cosyvoice-v2`。它能流式输出 PCM，不需要先保存和解码完整音频文件，适合对话场景。音色按目标语言选择，避免用错误语种的音色朗读文本。

## 七、几个关键工程问题

### 1. 连续语音为什么曾经只能识别一个词

LiveTranslate 将原文和译文分成两条事件流。一次语音活动中可能产生多个响应，不能把一次 `speech_started` 当成一句话。

项目现在使用服务端的 `item_id` 和 `response_id` 分别编号，再按出现顺序配对。这样连续语音会生成多条独立记录，不会让后一句覆盖前一句。

### 2. 为什么 VAD 不能设置得太激进

静音断句阈值不只影响“多久出一句”，也影响翻译质量。阈值过小时，一句话会被切成多个没有上下文的短片段。项目默认使用 800ms，这是延迟与完整语义之间的折中。

### 3. 如何采集电脑正在播放的声音

Windows 普通麦克风 API 只能读取输入设备。项目通过 `PyAudioWPatch` 枚举 WASAPI Loopback 设备，从默认输出设备的回环端采集声音；如果设备采样率不是 16 kHz，再在本地重采样。

### 4. 如何避免翻译后的声音再次被识别

扬声器播放外语译文时，监听通道可能把这段声音重新收进去，形成“自己翻译自己”的循环。RealTalk_AI 在播放期间用静音帧替换上行音频，但不会彻底停止发送，因为长时间没有音频数据可能触发服务端断线。

### 5. 为什么采用半双工

实时模型的翻译目标语言在建立连接时确定。一条连接不能同时承担“外语 → 中文”和“中文 → 外语”。项目通过两个操作状态复用一套会话控制器：平时听译，需要回复时显式切换，既减少计费，也避免两个通道同时抢麦克风。

### 6. GUI 为什么不会被网络连接卡住

WebSocket 建连、关闭和语音合成都可能阻塞。项目把这些操作放到后台线程；SDK 回调通过 Qt Signal 投递到 UI 线程，避免从网络线程直接修改控件。

## 八、代码结构

```text
RealTalk_AI/
├── main.py
├── realtalk/
│   ├── config.py              # 环境变量、模型与 WebSocket 配置
│   ├── languages.py           # 语种、翻译方向与音色
│   ├── cli.py                 # 命令行检查工具
│   ├── audio/
│   │   ├── recorder.py        # 麦克风、WASAPI、重采样
│   │   └── player.py          # PCM 流式播放
│   ├── core/
│   │   ├── conversation.py    # 半双工对话编排
│   │   ├── qwen_listen.py     # LiveTranslate 事件适配
│   │   ├── listen.py          # 听译会话工厂
│   │   ├── translator.py      # Qwen-MT 与降级
│   │   ├── tts.py             # CosyVoice
│   │   └── events.py          # 与 UI 解耦的事件模型
│   └── ui/
│       ├── conversation_page.py
│       ├── main_window.py
│       └── theme.py
├── docs/
│   └── assets/                # 流程图与交互图
└── tests/                     # 测试不需要真实 API Key
```

`core` 不引用 PySide6。CLI 和桌面界面共用相同的业务逻辑，因此后续更换 UI 或封装 Web 服务时不需要重写模型接入。

## 九、安装和运行

### 1. 克隆仓库

```bash
git clone https://github.com/magau123/RealTalk_AI.git
cd RealTalk_AI
```

### 2. 创建虚拟环境

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.venv\Scripts\Activate.ps1
```

macOS / Linux：

```bash
source .venv/bin/activate
```

### 3. 安装依赖

```bash
python -m pip install -r requirements.txt
```

Linux 还需要 PortAudio：

```bash
sudo apt-get install libportaudio2
```

### 4. 配置 API Key

Windows：

```powershell
Copy-Item .env.example .env
```

macOS / Linux：

```bash
cp .env.example .env
```

编辑 `.env`：

```env
DASHSCOPE_API_KEY=你的百炼_API_Key
```

获取地址：<https://bailian.console.aliyun.com/>

### 5. 检查环境

```bash
python -m realtalk.cli check
```

该命令会分别检查：

- API Key 与基础配置
- 音频输入设备
- Qwen 实时翻译
- Qwen-MT 文本翻译
- CosyVoice 语音合成

完整检查会产生少量 API 调用。

### 6. 启动

```bash
python main.py
```

## 十、命令行调试

```bash
python -m realtalk.cli devices
python -m realtalk.cli voices
python -m realtalk.cli listen --source en
python -m realtalk.cli listen --source ja
python -m realtalk.cli speak "洗手间在哪里？" --target en
python -m realtalk.cli check -v
```

当 GUI 没有输出时，先用 CLI 判断问题来自音频设备、实时模型、文本翻译还是语音合成。

## 十一、安全设计

项目不会把真实 API Key 写入代码：

- Key 只从进程环境变量或 `.env` 读取。
- `.env` 已加入 `.gitignore`。
- `.env.example` 只有占位内容。
- 配置对象的 `repr` 会隐藏 Key。
- GUI 只显示脱敏后的 Key。
- 自动测试检查完整 Key 不会进入界面文本。

不要将自己的 `.env`、截图中的完整 Key 或终端日志上传到公开仓库。

## 十二、常见问题

### 报错 `403 Free quota exhausted`

当前模型的免费额度已耗尽。百炼额度按模型分别计算，可以先运行 `python -m realtalk.cli check` 查看其他模型是否可用。若所有候选模型都不可用，需要充值或调整控制台的免费额度限制。

### 找不到系统声音

运行：

```bash
python -m realtalk.cli devices
```

Windows 下选择名称带“回环”的设备。仍然没有时，确认声卡驱动、默认播放设备以及麦克风隐私权限正常。

### 翻译内容被切得太碎

在 `.env` 中提高：

```env
REALTALK_MAX_END_SILENCE=1200
```

默认值为 800ms。数值越小断句越快，但更容易丢失上下文。

### 对方的声音被重复识别

优先使用耳机，降低扬声器音量，并确认“对方声音来源”和“我的麦克风”没有选成同一个不合适的设备。

## 十三、开发与测试

```bash
python -m pip install pytest ruff

# Windows PowerShell
$env:QT_QPA_PLATFORM="offscreen"
pytest -q
ruff check realtalk tests
```

测试使用假的 API Key，不请求真实云服务。模型接入的在线效果仍需使用自己的百炼账号进行联调。

## 十四、当前限制与后续计划

当前版本仍有以下边界：

- 主要在 Windows 上完成系统声音回环验证。
- 依赖云端模型，需要网络和百炼额度。
- 当前没有离线识别模式。
- 西班牙语、法语、德语等回复音色需要接入 CosyVoice v3 声音复刻。
- 暂不支持导出对话记录和自定义热词。

后续计划包括对话导出、专业术语热词、端到端延迟统计，以及更多回复音色。

## 十五、开源地址

GitHub：**https://github.com/magau123/RealTalk_AI**

项目采用 MIT License，可以自由学习、修改和二次开发。欢迎提交 Issue、Pull Request，或在实际会议、直播和跨语言交流场景中反馈问题。

如果这个项目对你有帮助，欢迎在 GitHub 点一个 Star。
