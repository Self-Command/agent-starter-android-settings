# 单人自托管 LiveKit

此目录是个人语音助手的后端，复用 Android App 的「Token 接口」连接方式。
App 不保存 STT、LLM、TTS 密钥。已有 APK 可直接填写新的 Token 接口地址。

## 服务和网络

部署目录 `/opt/livekit-personal`，Compose 项目 `livekit-personal`，与其他 Docker 项目独立。
只运行 LiveKit Server、Agent、Redis 和 Token 接口；不安装录制、直播推流、电话网关、GPU 或本地大模型服务。

| 服务 | 内存上限 | 网络 |
| --- | ---: | --- |
| LiveKit Server v1.13.9 | 256 MiB | HTTP 7880 仅本机；音频 UDP 7882；ICE/TCP 7881；TURN/UDP 443 |
| Agent | 1280 MiB | 健康检查 8081 仅本机；连接本机 LiveKit |
| Redis 7.4.2 | 64 MiB | 6380 仅本机；无磁盘持久化 |
| Token 接口 | 128 MiB | 8082 仅本机；经 Nginx HTTPS 443 对外 |

Nginx 使用 `nginx.conf.template` 新增域名，保留已有站点。
媒体优先尝试 UDP 直连，内置 TURN/UDP 作为备选，ICE/TCP 用于 UDP 被阻断的网络。
这套配置不提供 TURN/TLS；无法保证所有公司网络都能连接。
BBR 只影响 TCP，不会缩短模型的生成时间。实际 ICE 协议必须从连接统计确认。

## 私有配置

`.env` 保存审核过的完整镜像 digest，字段为 `LIVEKIT_IMAGE`、`REDIS_IMAGE`、`AGENT_IMAGE`。
不要使用浮动 `latest`，不要在服务器或本地构建镜像。

`private/` 目录权限 700；以下含密钥文件权限 600，均不提交 Git：

- `agent.env`：本机 `LIVEKIT_URL`、LiveKit API Key/Secret、Deepgram Key、LLM Base URL/Key/Model、MiMo Base URL/Key/Model/Voice。
- `token.env`：公开 `LIVEKIT_PUBLIC_URL`、相同 LiveKit API Key/Secret、随机 `TOKEN_ENDPOINT_SECRET`。
- `livekit.yaml`：LiveKit 密钥和网络配置。
- `redis.conf`：仅含本机端口和 32 MiB 缓存上限；权限 644，让 Redis 容器用户可读取。

无凭据的配置样例分别为 `agent.env.example`、`token.env.example`、`livekit.yaml.template`
和 `redis.conf`。将样例复制到私有目录并替换占位符；媒体 IP 过滤只保留服务器公网地址，
避免客户端先探测 Docker 网桥地址。真实域名 Nginx 模板须对应 DNS 和证书。

Token 接口地址为 `https://<域名>/token/<随机秘密>`，完整地址属于私密连接凭据。
App 中只选择「Token 接口」并填写这个地址。接口接受 Android SDK 的 POST JSON 请求，
返回 `server_url` 和 `participant_token`。Token 有效期 10 分钟，固定房间 `personal-voice`，
只允许发布麦克风、订阅和数据通道；房间最多两名参与者（一个用户和一个 Agent）。
新请求生成新用户身份。只有测试身份 `action-client` 会接收数值诊断，普通 App 不接收。
接口限制每 IP 每分钟 6 次、突发 3 次；日志不记录该域名的请求地址、Token 或对话文字。

Agent 使用 Nova-3 中文和英文实时流并合并词时间戳；中文主流没有覆盖的高置信度英文
在最多 300 ms 的主流等待后独立输出，避免纯英文静默丢失；低置信度英文猜测不提升为最终转录。
LLM 流式输出，Azure Speech 以 24 kHz PCM 分块返回音频；默认使用
`zh-CN-XiaoxiaoMultilingualNeural`，适合中英文混说。MiMo 配置仍保留在私有环境中，
将 `TTS_PROVIDER` 改回 `mimo` 可回滚。
`AZURE_SPEECH_ENDPOINT` 必须是合成端点
`https://<region>.tts.speech.microsoft.com/cognitiveservices/v1`；
门户显示的 `https://<region>.api.cognitive.microsoft.com/` 不能直接用于 TTS。
也可删除该环境变量，让官方插件根据 Region 自动生成合成端点。
`LLM_API_STYLE=openai` 兼容原网关；DeepSeek 官方接口使用 `deepseek`、
`LLM_BASE_URL=https://api.deepseek.com` 和 `LLM_MODEL=deepseek-flash`，
显式关闭默认思考模式，并使用官方 `max_tokens` 参数。供应商 Key 仍只存后端。
默认一个预热进程、一次会话。`VOICE_ENDPOINT_DELAY`、`VOICE_PREEMPTIVE` 和 `VOICE_REVISION`
在 `agent.env` 中设置；变更后只重启本项目的 Agent，避免并行测速干扰供应商限流。

## CPU 兼容

Agents SDK 固定为 1.8.5。旧 Xeon E5-2650 v2 缺少 AVX2，SDK 自动预加载的
`livekit-local-inference` 原生库在此主机触发非法指令，即使应用没有使用它。
`compat_preload.py` 在云端构建时仅跳过这一项不需要的预加载，保留显式 Silero ONNX VAD。
精确版本和源码匹配检查会在 SDK 升级时阻止构建，要求重新审查。
容器健康检查同时检查已完成 VAD 预热的子进程存活，不能只凭 HTTP 200 判断语音就绪。

## 构建和维护

GitHub Actions「Personal LiveKit backend」运行提供者协议和 Token 接口单元测试，
验证 Agent 导入和 VAD 加载，然后发布带提交 SHA 标签的 GHCR 镜像及 digest。
拉取已通过构建的 digest 后运行：

```sh
cd /opt/livekit-personal
docker compose config --quiet
docker compose pull
docker compose up -d
docker compose ps
docker compose stats --no-stream
```

只更新 Agent 配置时使用 `docker compose up -d --force-recreate agent`。
回滚时恢复 `.env` 中上一个已测试 digest，再执行 `docker compose up -d agent token`。
操作应限定本目录、本项目，不停止或清理其他容器或卷。
日志按每服务 3 × 10 MiB 轮换。

## 验收和测速

在「Fast App requests and voice latency」手动工作流选择 `target=personal`，
私有 GitHub Secret `PERSONAL_VOICE_TEST_CONFIG` 提供 Token 接口和 TTS 测试音频凭据。
工作流在 GitHub Actions 中执行 App 的真实 `createTokenSource` 和 Android SDK POST 请求，
随后用合成音频 RTC 客户端检测中文、英文、混说、250 ms 停顿、打断和重连。
没有模拟器、UI、本地 Gradle 或本地 Agent 运行。临时 JWT 不进入报告或制品。

目标：首段转录 ≤1.5 秒，最终转录在说完后 ≤1 秒；10 轮说完到回复音频的中位数 ≤2 秒、每轮 ≤3 秒。
报告保留实际 ICE 协议、RTT、丢包、抖动以及 STT/LLM/TTS 数值指标。
速度未达标时工作流明确失败，不把正常连通等同于实时验收通过。
合成测试不能替代真实手机麦克风、扬声器和网络体验。

`provider_probe.py` 可在部署主机以同一镜像和私有环境执行，独立测三次 LLM 首字和 TTS 首音频，
并记录 DNS、包含 TLS 的建连耗时和 HTTP 状态。它只输出数值，不输出密钥或回复内容。
三次串行采样不能证明没有持续限流。

参考：[LiveKit 端口配置](https://docs.livekit.io/transport/self-hosting/ports-firewall/)、
[官方轮次处理](https://docs.livekit.io/agents/logic/turns/)。
