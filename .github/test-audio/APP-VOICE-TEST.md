# 快速请求及语音延迟测试

手动触发 **Fast App requests and voice latency**。所有代码执行、依赖安装、
单元测试都在 Actions；本地只修改和静态检查。工作流不启动模拟器，不测试 UI。

App 的连接选择抽象在 `LiveKitConnectionRequest.kt`。JVM 测试调用生产代码
`createTokenSource()` 和现有 LiveKit Android SDK 2.28.0，验证三个配置分支、
必填校验、真实 HTTP POST Token 协议及实际配置选择。测试不是另写一份 Token 请求。
解析出的真实连接地址和 JWT 仅写入 runner 临时目录，随后删除，不进入报告。

接着 Python RTC 客户端使用这些连接信息，发布固定的虚构中英文合成语音，测量
实时转写、最终转写、首段回复音频和完整回复。连续 1 或 3 轮；输入按 20ms 节奏发送，
报告时钟滞后；使用 WAV 中最后一个有效声音样本作为说完的时点。
这部分复用连接配置和相同 LiveKit 服务链路，但不执行 Android 的麦克风、WebRTC
原生库、扬声器或页面，不能当作手机端或真人说话的验证。

验收指标：首段转写不超过 1.5s；说完后最终转写不超过 1s；说完后回复音频中位数
不超过 2s，每个样本不超过 3s。样本量很小，不报告统计意义的 p95。
语音能完成与实时延迟达标分别判断；实时不达标会失败并保留报告。

独立 HTTP 对照在 Actions runner 执行，包括 LLM 三次首段文字，以及 TTS 的完整
WAV、流式 PCM16、短句和两次并发。先预热再交替比较，相同文本及音色。
并发对照只能显示当前有没有 429，不能证明持续请求配额。
这些对照不当作 Cloud 机房的延迟；Cloud 实际会话的 LLM/TTS/EOU 指标通过
`voice.pipeline.metrics` 数据消息另行记录，后端只对测试身份发送数值指标。

`ANDROID_VOICE_TEST_CONFIG` Secret 格式：`url`、短期测试房间 `token`，以及
`llm` (`base_url`, `api_key`, `model`) 和 `tts` (`base_url`, `api_key`, `model`, `voice`)。
可选 `mode` 为三种 App 连接方式之一，配合 `token_server_id` 或 `token_endpoint`。
模型信息仅供诊断对照，App 本身仍不调用或配置 STT/LLM/TTS。
日志和 artifacts 不包含凭据，转写只来自已公开的虚构测试音频。
