package io.livekit.android.example.voiceassistant.settings

/** Platform-independent request selection, shared by the App and cloud JVM tests. */
sealed interface LiveKitConnectionRequest {
    class Development(val id: String) : LiveKitConnectionRequest {
        override fun toString() = "Development(credentials=redacted)"
    }
    class Direct(val url: String, val token: String) : LiveKitConnectionRequest {
        override fun toString() = "Direct(credentials=redacted)"
    }
    class Endpoint(val url: String) : LiveKitConnectionRequest {
        override fun toString() = "Endpoint(credentials=redacted)"
    }
}

fun LiveKitSettings.connectionRequest(): LiveKitConnectionRequest {
    val settings = normalized()
    require(settings.validationErrors().isEmpty()) { "LiveKit 连接配置不完整，请检查设置" }
    return when (settings.mode) {
        ConnectionMode.DEVELOPMENT_TOKEN_SERVER -> LiveKitConnectionRequest.Development(settings.tokenServerId)
        ConnectionMode.DIRECT -> LiveKitConnectionRequest.Direct(settings.serverUrl, settings.token)
        ConnectionMode.TOKEN_ENDPOINT -> LiveKitConnectionRequest.Endpoint(settings.tokenEndpoint)
    }
}
