package io.livekit.android.example.voiceassistant.settings

import java.net.URI

const val DEFAULT_TOKEN_ENDPOINT = "https://livekit.com/api/homepage-agent/token"

enum class ConnectionMode { DEVELOPMENT_TOKEN_SERVER, DIRECT, TOKEN_ENDPOINT }
enum class ConnectionField { TOKEN_SERVER_ID, SERVER_URL, TOKEN, TOKEN_ENDPOINT }

/** One runtime configuration. Credentials must never be included in logs or navigation. */
data class LiveKitSettings(
    val mode: ConnectionMode = ConnectionMode.TOKEN_ENDPOINT,
    val tokenServerId: String = "",
    val serverUrl: String = "",
    val token: String = "",
    val tokenEndpoint: String = DEFAULT_TOKEN_ENDPOINT,
) {
    fun normalized() = copy(
        tokenServerId = tokenServerId.trim(),
        serverUrl = serverUrl.trim(),
        token = token.trim(),
        tokenEndpoint = tokenEndpoint.trim(),
    )

    fun validationErrors(): Map<ConnectionField, String> = buildMap {
        val settings = normalized()
        when (mode) {
            ConnectionMode.DEVELOPMENT_TOKEN_SERVER -> {
                if (settings.tokenServerId.isEmpty()) put(ConnectionField.TOKEN_SERVER_ID, "请填写 Token 服务 ID")
            }
            ConnectionMode.DIRECT -> {
                if (!validUrl(settings.serverUrl, setOf("wss", "ws"))) {
                    put(ConnectionField.SERVER_URL, "请填写有效的 ws:// 或 wss:// 服务器地址")
                }
                if (settings.token.isEmpty()) put(ConnectionField.TOKEN, "请填写连接 Token")
            }
            ConnectionMode.TOKEN_ENDPOINT -> {
                if (!validUrl(settings.tokenEndpoint, setOf("https", "http"))) {
                    put(ConnectionField.TOKEN_ENDPOINT, "请填写有效的 http:// 或 https:// Token 接口地址")
                }
            }
        }
    }

    // Avoid exposing the Token if an object is included in diagnostics accidentally.
    override fun toString(): String = "LiveKitSettings(mode=$mode, credentials=redacted)"

    private fun validUrl(value: String, schemes: Set<String>): Boolean = runCatching {
        val uri = URI(value)
        uri.scheme?.lowercase() in schemes && !uri.host.isNullOrBlank() &&
            uri.rawUserInfo == null && uri.rawFragment == null &&
            (uri.port == -1 || uri.port in 1..65535)
    }.getOrDefault(false)
}
