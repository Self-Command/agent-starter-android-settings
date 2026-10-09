# LiveKit Android：安装后配置连接

基于 `livekit-examples/agent-starter-android`，保留原有语音界面和 LiveKit SDK。
仅新增一组运行时连接设置，不包含模型设置、额度检测或多账号切换。

## 安装和使用

从本仓库 Releases 下载 APK，安装后打开首页的 **LiveKit 设置**。
选择一种连接方式并保存，下次开始通话时生效：

| 方式 | 必填内容 |
| --- | --- |
| 开发 Token 服务 | LiveKit Cloud 项目设置中的 Token 服务 ID |
| 服务器地址 + Token | `wss://...` 服务器地址、连接房间的 JWT Token |
| Token 接口 | 返回连接信息的 HTTP(S) 接口地址 |

首次安装默认使用官方演示 Token 接口。配置无效或连接失败会提示错误，不会切换回演示服务。
手填的 Token 过期后需要更新。Token 不是 API Key；App 不使用 API Key 或 API Secret 签发 Token。
开发 Token 服务仅适合开发测试。Token 接口须符合官方 TokenSource 格式，返回 `server_url` 和 `participant_token`。
本版本没有自定义认证请求头、Agent 名称或业务元数据；目标 Agent 需由后端或 Token 配置正确调度。
HTTP/WS 的可用性遵循 Android 原有网络策略，正式服务建议使用 HTTPS/WSS。

配置使用 Android Keystore 的 AES-GCM 加密，文件保存在不参与备份的应用私有目录。
Token 默认隐藏，不通过导航或日志传递。取消编辑保留原配置。

## 云端构建和测试

本地只编辑代码和进行静态检查；**不要在本地运行 Gradle、编译、打包或启动模拟器**。

在 GitHub Actions 运行 **Android build, test and release**：

- PR：单元测试、Lint，不发布；按当前要求已撤掉模拟器和 UI 测试。
- 手动运行：输入版本号，选择是否发布；发布只能从 `main` 触发。
- 推送 `v*` 版本标签：执行完整检查并发布。

通过测试后发布固定签名的 Release APK、SHA-256 和更新说明。测试报告保存为 Actions artifacts。
自动测试使用虚构连接参数，不验证真实账号的语音连通性。

语音延迟诊断使用独立的 **Fast App requests and voice latency**，测试实际 App 的
Token 请求代码，再用合成语音测服务链路，未达实时指标会失败并保留报告。
详见 [.github/test-audio/APP-VOICE-TEST.md](.github/test-audio/APP-VOICE-TEST.md)。

单人自托管后端和 UDP 部署说明见 [backend/personal/README.md](backend/personal/README.md)。
自托管仍使用现有 APK 的「Token 接口」设置，不增加前端模型配置。

## 固定签名

签名密钥通过 **Initialize signing key** 工作流生成一次，明文仅存在于云端临时目录。
加密文件 `.github/signing/release.keystore.gpg` 使用 AES-256 GPG 加密，密码在仓库 Secrets 中：

- `SIGNING_ARCHIVE_PASSWORD`：加密文件的密码。
- `RELEASE_STORE_PASSWORD`：签名库和签名密钥的密码。

后续版本复用同一签名，不能重新生成或覆盖现有签名文件。妥善保存加密文件和密码，丢失后无法为已安装版本发布覆盖更新。
日常使用的 LiveKit 配置在安装后的设置页填写，不放进源码。
获授权的诊断凭据只放 Actions Secrets，诊断房间 Token 使用短有效期。

## 项目来源

- 原项目：https://github.com/livekit-examples/agent-starter-android
- 官方认证说明：https://docs.livekit.io/frontends/build/authentication/

保留原项目的 MIT 许可证。
