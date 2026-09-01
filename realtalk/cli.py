"""命令行入口。

主要用途是在不牵扯界面的情况下验证 API 链路，排查问题时非常有用：

    python -m realtalk.cli check                 # 检查配置与依赖
    python -m realtalk.cli devices               # 列出录音设备
    python -m realtalk.cli listen                # 方向一：听外语，出中文
    python -m realtalk.cli listen --source ja
    python -m realtalk.cli speak "你好，很高兴见到你" --target ja
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import sys
import threading

from realtalk.config import ConfigError, Settings, load_settings
from realtalk.core.events import SentenceUpdate, TranslationSource
from realtalk.languages import (
    AUTO,
    GUMMY_ASR_LANGUAGES,
    TTS_VOICES,
    language_name,
    tts_supported_languages,
)


def _force_utf8_output() -> None:
    """让中文在 Windows 控制台和重定向管道里都能正常显示。

    Python 3.13 在 Windows 上把 stdout 重定向到管道时会用系统区域编码
    （简体中文环境下是 cp936），中文一旦被下游按 UTF-8 解读就是乱码。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8", errors="replace")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def _load_or_exit() -> Settings:
    try:
        return load_settings()
    except ConfigError as exc:
        print(f"配置错误：\n{exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def cmd_devices(_: argparse.Namespace) -> int:
    from realtalk.audio.recorder import list_input_devices

    devices = list_input_devices()
    if not devices:
        print("没有找到可用的录音设备。")
        return 1
    print("可用录音设备：")
    for device in devices:
        print(f"  {device}  （{device.max_input_channels} 声道）")
    print("\n用 --device <序号> 指定设备，不指定则使用系统默认。")
    return 0


_QUOTA_HINT = (
    "该模型的免费额度已用尽。百炼的免费额度是按模型独立计算的，不能跨模型共用，\n"
    "        因此其他模型可能仍然可用。恢复方式有两种：\n"
    "          1. 在百炼控制台为账户充值；\n"
    "          2. 若账户开启了「仅使用免费额度」模式，\n"
    "             需在控制台关闭该模式后才会转为按量计费。"
)


def _explain_failure(message: str) -> str:
    """把服务端错误翻译成用户能直接行动的说明。"""
    lowered = message.lower()
    if "free quota exhausted" in lowered or "arrearage" in lowered:
        return _QUOTA_HINT
    if "invalid api" in lowered or "401" in lowered or "unauthorized" in lowered:
        return "API Key 无效或已被删除，请到百炼控制台重新生成。"
    if "model not exist" in lowered or "invalidparameter" in lowered:
        return "模型名可能有误，或当前账号/地域没有该模型的调用权限。"
    if "access denied" in lowered or "not activated" in lowered:
        return "百炼服务可能尚未开通，请到控制台开通后重试。"
    return "请检查网络连通性，以及百炼控制台中该模型的开通状态。"


def _probe_translation(settings: Settings) -> tuple[bool, str]:
    from realtalk.core.translator import TextTranslator, TranslationError

    translator = TextTranslator(settings)
    try:
        result = translator.translate(
            "今天天气不错。", target_language="en", source_language="zh"
        )
    except TranslationError as exc:
        return False, str(exc)

    note = ""
    if translator.active_model != settings.mt_model:
        note = (
            f"\n              首选模型 {settings.mt_model} 不可用，"
            f"已自动降级到 {translator.active_model}。"
            f"\n              建议在 .env 中设置 "
            f"REALTALK_MT_MODEL={translator.active_model} 以省掉每次启动的试探请求。"
        )
    return True, f"[{translator.active_model}] 今天天气不错。 -> {result}{note}"


def _probe_tts(settings: Settings) -> tuple[bool, str]:
    from realtalk.core.tts import SpeechSynthesizerClient, SynthesisError
    from realtalk.languages import default_voice

    voice = default_voice("en").voice_id
    try:
        audio = SpeechSynthesizerClient(settings).synthesize_to_bytes(
            "Hello.", voice=voice
        )
    except SynthesisError as exc:
        return False, str(exc)
    return True, f"音色 {voice} 返回 {len(audio)} 字节 PCM 音频"


def _probe_asr(settings: Settings) -> tuple[bool, str]:
    """连接实时听译模型，验证鉴权和模型权限。"""
    if settings.asr_model.startswith("qwen3.5-livetranslate"):
        from dashscope.audio.qwen_omni import (
            MultiModality,
            OmniRealtimeCallback,
            OmniRealtimeConversation,
        )
        from dashscope.audio.qwen_omni.omni_realtime import TranslationParams

        errors: list[str] = []

        class _Probe(OmniRealtimeCallback):
            def on_event(self, event: dict) -> None:
                if event.get("type") == "error":
                    error = event.get("error") or {}
                    errors.append(error.get("message") or str(error))

        conversation = OmniRealtimeConversation(
            model=settings.asr_model,
            callback=_Probe(),
            url=settings.websocket_url,
            api_key=settings.dashscope_api_key,
        )
        try:
            conversation.connect()
            conversation.update_session(
                output_modalities=[MultiModality.TEXT],
                voice="Tina",
                input_audio_transcription_model="qwen3-asr-flash-realtime",
                translation_params=TranslationParams(language="zh"),
            )
        except Exception as exc:
            errors.append(str(exc))
        finally:
            conversation.close()
        return (
            (False, errors[0])
            if errors
            else (True, "Qwen3.5 LiveTranslate WebSocket 建连正常")
        )

    # Gummy 只建连并发送一小段静音；几乎不产生费用，但足以暴露鉴权、
    # 额度和地域权限问题。
    import threading

    from dashscope.audio.asr import (
        TranslationRecognizerCallback,
        TranslationRecognizerRealtime,
    )

    from realtalk.config import SAMPLE_RATE

    opened = threading.Event()
    errors: list[str] = []

    class _Probe(TranslationRecognizerCallback):
        def on_open(self) -> None:
            opened.set()

        def on_error(self, message: object) -> None:
            errors.append(str(getattr(message, "message", None) or message))
            opened.set()

        def on_event(self, *args: object, **kwargs: object) -> None:
            pass

    recognizer = TranslationRecognizerRealtime(
        model=settings.asr_model,
        format="pcm",
        sample_rate=SAMPLE_RATE,
        source_language="en",
        transcription_enabled=True,
        translation_enabled=True,
        translation_target_languages=["zh"],
        callback=_Probe(),
    )

    try:
        recognizer.start()
        # 200ms 静音，仅用于确认连接可以正常收发数据
        recognizer.send_audio_frame(b"\x00" * (SAMPLE_RATE // 5 * 2))
    except Exception as exc:
        errors.append(str(exc))
    finally:
        try:
            recognizer.stop()
        except Exception as exc:
            if not errors:
                errors.append(str(exc))

    if errors:
        return False, errors[0]
    if not opened.is_set():
        return False, "连接未能建立（未收到 on_open 回调）"
    return True, "WebSocket 建连与音频收发正常"


def cmd_check(_: argparse.Namespace) -> int:
    settings = _load_or_exit()

    print("[1/5] 配置")
    print(f"      API Key   {settings.masked_api_key}（来源：{settings._loaded_from}）")
    print(f"      识别模型  {settings.asr_model}")
    print(f"      翻译模型  {settings.mt_model}")
    print(f"      合成模型  {settings.tts_model}")

    print("[2/5] 音频设备")
    try:
        from realtalk.audio.recorder import list_input_devices

        devices = list_input_devices()
        if devices:
            print(f"      找到 {len(devices)} 个录音设备")
        else:
            print("      警告：没有录音设备，听译功能无法使用")
    except Exception as exc:
        print(f"      音频子系统不可用：{exc}")

    from realtalk.config import apply_to_dashscope

    apply_to_dashscope(settings)

    # 逐项独立探测。免费额度按模型独立计算，一个模型不可用不代表其他也不可用，
    # 因此这里不提前返回，务必把每个模型的真实状态都报出来。
    probes = (
        ("[3/5]", f"实时识别与翻译（{settings.asr_model}）", _probe_asr),
        ("[4/5]", f"文本翻译（{settings.mt_model}）", _probe_translation),
        ("[5/5]", f"语音合成（{settings.tts_model}）", _probe_tts),
    )

    results: dict[str, bool] = {}
    for tag, title, probe in probes:
        print(f"{tag} {title}")
        ok, detail = probe(settings)
        results[title] = ok
        if ok:
            print(f"      可用：{detail}")
        else:
            print(f"      不可用：{detail}")
            print(f"      {_explain_failure(detail)}")

    print("\n---- 结论 ----")
    for title, ok in results.items():
        print(f"  {'可用  ' if ok else '不可用'}  {title}")

    asr_ok = results[f"实时识别与翻译（{settings.asr_model}）"]
    mt_ok = results[f"文本翻译（{settings.mt_model}）"]
    tts_ok = results[f"语音合成（{settings.tts_model}）"]

    print()
    if asr_ok:
        print("  听译（外语 → 中文）：可用。")
        if not mt_ok:
            print(
                "    注意：文本翻译不可用，Gummy 无法直译成中文的语种"
                "（葡、印尼、阿拉伯、泰）会拿不到译文。"
            )
    else:
        print("  听译（外语 → 中文）：不可用，识别模型无法调用。")

    if mt_ok and tts_ok:
        print("  说译（中文 → 外语）：可用。")
    else:
        missing = "文本翻译" if not mt_ok else "语音合成"
        print(f"  说译（中文 → 外语）：不可用，缺少{missing}能力。")

    return 0 if all(results.values()) else 1


def cmd_listen(args: argparse.Namespace) -> int:
    settings = _load_or_exit()

    from realtalk.core.listen import create_listen_session

    if args.source != AUTO and args.source not in GUMMY_ASR_LANGUAGES:
        print(
            f"源语种 {args.source!r} 不在当前支持的识别语种内。\n"
            f"可选：auto, {', '.join(sorted(GUMMY_ASR_LANGUAGES))}",
            file=sys.stderr,
        )
        return 2

    printed_lines: dict[int, bool] = {}
    lock = threading.Lock()

    def on_sentence(update: SentenceUpdate) -> None:
        with lock:
            # 中间结果用 \r 原地刷新，定稿后换行固定下来
            marker = {
                TranslationSource.GUMMY: "G",
                TranslationSource.QWEN_LIVE: "Q",
                TranslationSource.QWEN_MT: "M",
                TranslationSource.NONE: " ",
            }[update.translation_source]
            line = f"[{update.sentence_id:>3}{marker}] {update.source_text}"
            if update.translated_text:
                line += f"\n        中文 | {update.translated_text}"

            if update.is_final and update.translated_text:
                print(f"\r\033[K{line}\n")
                printed_lines[update.sentence_id] = True
            elif not printed_lines.get(update.sentence_id):
                print(f"\r\033[K{line}", end="", flush=True)

    def on_state(event) -> None:  # noqa: ANN001
        if event.detail:
            print(f"[{event.state.value}] {event.detail}")

    def on_error(event) -> None:  # noqa: ANN001
        print(f"\n错误：{event.message}", file=sys.stderr)

    session = create_listen_session(
        settings,
        on_sentence=on_sentence,
        on_state=on_state,
        on_error=on_error,
        source_language=args.source,
        target_language=args.target,
        device=args.device,
    )

    try:
        session.start()
    except Exception as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1

    print("\n开始说话吧。按 Ctrl+C 结束。")
    print("（行首 Q/G/M 表示译文来自 Qwen 实时/Gummy/Qwen-MT）\n")
    try:
        while True:
            threading.Event().wait(0.2)
    except KeyboardInterrupt:
        print("\n正在结束 …")
    finally:
        session.stop()
    return 0


def cmd_speak(args: argparse.Namespace) -> int:
    settings = _load_or_exit()

    from realtalk.core.speak import SpeakSession
    from realtalk.core.translator import TranslationError
    from realtalk.core.tts import SynthesisError

    if args.target not in tts_supported_languages():
        print(
            f"目标语种 {args.target!r} 目前没有可用的 CosyVoice 系统音色。\n"
            f"可选：{', '.join(tts_supported_languages())}",
            file=sys.stderr,
        )
        return 2

    session = SpeakSession(
        settings,
        on_state=lambda event: print(f"[{event.state.value}] {event.detail}")
        if event.detail
        else None,
        device=args.device,
    )

    try:
        result = session.speak(
            args.text, target_language=args.target, voice_id=args.voice
        )
    except (TranslationError, SynthesisError, ValueError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1

    print(f"\n中文原文  | {result.source_text}")
    print(f"{language_name(result.target_language):<6}译文 | {result.translated_text}")
    print(f"使用音色  | {result.voice}")
    if result.first_package_delay_ms is not None:
        print(f"首包延迟  | {result.first_package_delay_ms:.0f} ms")
    return 0


def cmd_voices(_: argparse.Namespace) -> int:
    print("可用的 TTS 音色（按目标语言分组）：\n")
    for code, voices in TTS_VOICES.items():
        print(f"  {language_name(code)}（{code}）")
        for index, voice in enumerate(voices):
            default_mark = "  ← 默认" if index == 0 else ""
            print(f"      {voice.voice_id:<20} {voice.zh_label}{default_mark}")
        print()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="realtalk",
        description="RealTalk_AI —— 基于阿里云百炼的实时多语言语音翻译",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("check", help="检查配置与 API 连通性").set_defaults(
        func=cmd_check
    )
    subparsers.add_parser("devices", help="列出录音设备").set_defaults(func=cmd_devices)
    subparsers.add_parser("voices", help="列出可用 TTS 音色").set_defaults(
        func=cmd_voices
    )

    listen = subparsers.add_parser(
        "listen", help="方向一：麦克风外语输入，实时输出中文"
    )
    listen.add_argument(
        "--source",
        default=AUTO,
        help="源语种代码，默认 auto 自动检测。例如 en / ja / ko",
    )
    listen.add_argument("--target", default="zh", help="目标语种代码，默认 zh")
    listen.add_argument("--device", type=int, default=None, help="录音设备序号")
    listen.set_defaults(func=cmd_listen)

    speak = subparsers.add_parser(
        "speak", help="方向二：输入中文，翻译并朗读目标语言"
    )
    speak.add_argument("text", help="要翻译并朗读的中文内容")
    speak.add_argument("--target", required=True, help="目标语种代码，例如 en / ja / ko")
    speak.add_argument("--voice", default=None, help="指定音色，默认按语种自动选择")
    speak.add_argument("--device", type=int, default=None, help="播放设备序号")
    speak.set_defaults(func=cmd_speak)

    return parser


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
