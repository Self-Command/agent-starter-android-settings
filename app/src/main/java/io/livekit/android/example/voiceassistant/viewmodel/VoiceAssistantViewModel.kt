package io.livekit.android.example.voiceassistant.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import io.livekit.android.LiveKit
import io.livekit.android.example.voiceassistant.diagnostics.VoiceSessionProbe
import io.livekit.android.example.voiceassistant.settings.LiveKitSettingsStore
import io.livekit.android.example.voiceassistant.settings.createTokenSource
import io.livekit.android.token.TokenSource

/** Keeps one connection snapshot and its Room alive across configuration changes. */
class VoiceAssistantViewModel(application: Application) : AndroidViewModel(application) {
    val connectionSettings = LiveKitSettingsStore(application).load()
    val tokenSource: TokenSource = connectionSettings.createTokenSource()
    val room = LiveKit.create(application)

    init {
        VoiceSessionProbe.roomCreated(room)
    }

    override fun onCleared() {
        super.onCleared()
        room.disconnect()
        room.release()
    }
}
