package io.livekit.android.example.voiceassistant.settings

import io.livekit.android.token.TokenSource
import java.net.URI

fun LiveKitSettings.createTokenSource(): TokenSource {
    val settings = normalized()
    require(settings.validationErrors().isEmpty()) { "LiveKit 连接配置不完整，请检查设置" }
    return when (settings.mode) {
        ConnectionMode.DEVELOPMENT_TOKEN_SERVER -> TokenSource.fromDevelopmentTokenServer(settings.tokenServerId)
        ConnectionMode.DIRECT -> TokenSource.fromLiteral(settings.serverUrl, settings.token)
        ConnectionMode.TOKEN_ENDPOINT -> TokenSource.fromEndpoint(URI(settings.tokenEndpoint).toURL())
    }
}
