package io.livekit.android.example.voiceassistant.diagnostics

import io.livekit.android.room.Room

/** Optional instrumentation observer; nothing is logged or saved by the app. */
object VoiceSessionProbe {
    @Volatile var onRoomCreated: ((Room) -> Unit)? = null
    @Volatile var listener: ((String, String?) -> Unit)? = null
    val enabled: Boolean get() = listener != null
    fun roomCreated(room: Room) { onRoomCreated?.invoke(room) }
    fun record(event: String, detail: String? = null) { listener?.invoke(event, detail) }
}
