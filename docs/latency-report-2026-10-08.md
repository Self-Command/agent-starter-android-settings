# 语音延迟测试结果（2026-10-08）

已撤掉模拟器和 UI。App 请求代码及现有 SDK 的 Token HTTP 协议在 Actions 的 JVM
单元测试中运行，再使用解析出的连接信息发送合成语音，测实际 LiveKit 会话。
本地没有编译、运行 Gradle、启动模拟器或执行 Agent。

**结论：三轮语音都能完成，但仍未达到实时验收标准。** 当前等待主要来自 LLM
中转的首段生成和 TTS 首块生成，另有约 1.3 秒最终转写等待。

## 已完成的调整

- 保留 Deepgram Nova-3 中英双路、用户指定的 `gpt-5.6-luna`、小米 `mimo-v2.5-tts` 和白桦音色。
- TTS 改为 `stream=true`、24kHz 单声道 PCM16 SSE；每块立即交给 LiveKit，不等完整 WAV。
- 开启 LLM 预生成，将首句 tokenizer 最小长度从 20 改为 6、上下文从 10 改为 1。
- 仅向测试参与者发送 LLM/TTS/EOU 数值指标，普通 App 用户不接收诊断数据。
- Cloud 构建中的 12 项适配测试通过，包含第一块在 HTTP 结束前已发送的验证。
- App 配置选择及 SDK Token 协议的 15 项 JVM 测试通过，直接连接配置解析约 3ms。

调整已部署到原有 Agent。现有 APK 结束旧通话再重新连接即可使用，无需修改连接配置。

## 实际会话结果

| 轮次 | 说话后首段转写 | 说完后最终转写 | 说完后首段回复音频 | Cloud LLM 首段文字 | Cloud TTS 首块音频 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 1.572s | 1.359s | 8.751s | 5.105s | 1.890s |
| 2 | 1.383s | 1.329s | 7.019s | 3.134s | 1.779s |
| 3 | 1.666s | 1.343s | 6.070s | 2.632s | 1.549s |

回复首音中位数 **7.019s**，最大 **8.751s**。房间连接约 0.189s；输入按 20ms
发送，最大时钟滞后约 1.1ms。第二轮还有一次提前生成的 LLM 请求（TTFT 2.381s），
未用于表中的最终回复；预生成不能保证降低每一轮延迟。

验收要求：首段转写 <=1.5s、最终转写 <=1s、回复首音中位数 <=2s、每次 <=3s。
本次不达标，Actions 保持失败并保存报告，没有放宽阈值。

调整前单轮首音等待 **24.136s**，测试超时，未验证完整回复结束。
历史另外两次会话约 9.7–11.2s；单次基线有波动，不能据此声称稳定提升某个百分比。

## 独立 HTTP 对照

这些请求来自 Actions runner，区别于上表 Cloud 实际会话指标。相同文本和音色，
预热后交替测试：

| 接口 | 首段可用内容 |
| --- | ---: |
| LLM Chat（三次） | 3.753 / 2.170 / 2.785s |
| LLM Responses（两次） | 2.747 / 2.630s |
| TTS 完整 WAV（两次） | 3.336 / 3.821s |
| TTS 流式 PCM16（两次） | 2.102 / 2.197s，13–14 个块 |
| TTS 两次并发短句 | 1.689 / 2.143s，均 HTTP 200 |

LLM Responses 没有显示足够的首段优势，完整输出更长，因此没有切换后端协议。
没有观察到 429；两个并发请求不能证明持续额度或限速规则。

从此次测量推断，仅换传输方式或缩短少量静默等待不能保证 2–3 秒实时交流。
若继续追求这一目标，优先降低同一 LLM 模型的中转首段耗时，并降低 TTS 首块耗时。
本次没有擅自更换用户指定的模型、服务商或账号。

## 范围和证据

这是固定合成音频的小样本诊断，未测试 Android 真机的麦克风、扬声器、手机网络、
蓝牙和 UI，不宣称真机通过或统计意义上的 p95。功能检查只要求中文以及 LiveKit、API、
settings 等关键词和完整回复；转写中仍有 Android 拆字和重复词，不能宣称中英识别完全准确。

- [调整前基线及 JVM 请求测试](https://github.com/Self-Command/agent-starter-android-settings/actions/runs/37717717066)
- [优化后三轮、HTTP 对照及 Cloud 分段指标](https://github.com/Self-Command/agent-starter-android-settings/actions/runs/37718463953)
- [快速测试方法和 Secret 格式](../.github/test-audio/APP-VOICE-TEST.md)

Artifacts 包含 `conversation.json`、`provider-controls.json`、`summary.md`、
`connection-request.json` 和 JVM 报告。凭据不进入报告、源码或 APK。
