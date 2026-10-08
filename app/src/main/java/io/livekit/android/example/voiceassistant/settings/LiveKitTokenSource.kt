package io.livekit.android.example.voiceassistant.settings

import io.livekit.android.token.TokenSource
import java.net.URI

fun LiveKitSettings.createTokenSource(): TokenSource {
    return when (val request = connectionRequest()) {
        is LiveKitConnectionRequest.Development -> TokenSource.fromDevelopmentTokenServer(request.id)
        is LiveKitConnectionRequest.Direct -> TokenSource.fromLiteral(request.url, request.token)
        is LiveKitConnectionRequest.Endpoint -> TokenSource.fromEndpoint(URI(request.url).toURL())
    }
}
