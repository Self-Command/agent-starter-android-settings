package io.livekit.android.example.voiceassistant.diagnostics

import io.livekit.android.room.Room

/** Release builds never collect voice diagnostic data. */
object VoiceSessionProbe {
    const val enabled = false
    @Suppress("UNUSED_PARAMETER") fun roomCreated(room: Room) = Unit
    @Suppress("UNUSED_PARAMETER") fun record(event: String, detail: String? = null) = Unit
}
