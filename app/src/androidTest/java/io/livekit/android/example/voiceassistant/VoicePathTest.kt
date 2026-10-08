package io.livekit.android.example.voiceassistant

import android.content.Context
import android.graphics.Bitmap
import android.os.SystemClock
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import io.livekit.android.events.RoomEvent
import io.livekit.android.events.collect
import io.livekit.android.example.voiceassistant.diagnostics.VoiceSessionProbe
import io.livekit.android.example.voiceassistant.settings.LiveKitSettingsStore
import io.livekit.android.room.Room
import io.livekit.android.room.track.AudioTrack
import io.livekit.android.room.track.Track
import kotlinx.coroutines.*
import livekit.org.webrtc.AudioTrackSink
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.nio.ByteOrder
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong
import kotlin.math.abs

/** Real app UI + Android AudioRecord/WebRTC path; opt-in, never run in PR checks. */
@RunWith(AndroidJUnit4::class)
class VoicePathTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    private val context: Context get() = InstrumentationRegistry.getInstrumentation().targetContext
    private val directory get() = File(context.getExternalFilesDir(null), "voice-e2e").apply { mkdirs() }
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val events = CopyOnWriteArrayList<JSONObject>()
    private val sinks = CopyOnWriteArrayList<Pair<AudioTrack, AudioTrackSink>>()
    private val lastRemoteAudio = AtomicLong(0)
    private val lastLocalAudio = AtomicLong(0)
    @Volatile private var room: Room? = null
    @Volatile private var active = false
    private lateinit var config: JSONObject
    private val startMs = SystemClock.elapsedRealtime()
    private val seenMessages = mutableMapOf<String, String?>()
    private fun lastDetail(event: String) = synchronized(seenMessages) { seenMessages[event] }

    private fun record(event: String, detail: String? = null) {
        events.add(JSONObject().put("event", event).put("elapsed_ms", SystemClock.elapsedRealtime() - startMs)
            .put("uptime_ms", SystemClock.elapsedRealtime()).put("detail", detail ?: JSONObject.NULL))
    }

    @Before fun prepare() {
        assumeTrue(InstrumentationRegistry.getArguments().getString("voiceE2E") == "true")
        config = JSONObject(File(context.noBackupFilesDir, "voice-test-config.json").readText())
        active = true
        File(context.noBackupFilesDir, LiveKitSettingsStore.FILE_NAME).delete()
        InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand(
            "pm grant ${context.packageName} android.permission.RECORD_AUDIO"
        ).close()
        VoiceSessionProbe.listener = { event, detail ->
            synchronized(seenMessages) {
                if (!event.endsWith("message_received") || seenMessages[event] != detail) {
                    seenMessages[event] = detail
                    record(event, detail)
                }
            }
        }
        VoiceSessionProbe.onRoomCreated = { created ->
            room = created
            record("room_created")
            scope.launch(start = CoroutineStart.UNDISPATCHED) {
                created.events.collect { event ->
                    record("room_${event.javaClass.simpleName}")
                    if (event is RoomEvent.TrackSubscribed && event.track is AudioTrack) {
                        attachAudio(event.track as AudioTrack, false)
                    }
                }
            }
        }
    }

    private fun attachAudio(track: AudioTrack, local: Boolean) {
        if (sinks.any { it.first === track }) return
        var lastRecorded = 0L
        val sink = AudioTrackSink { data, bits, _, _, _, _ ->
            if (bits == 16) {
                val buffer = data.duplicate().order(ByteOrder.LITTLE_ENDIAN)
                var peak = 0
                while (buffer.remaining() >= 2) peak = maxOf(peak, abs(buffer.short.toInt()))
                if (peak >= 350) {
                    val now = SystemClock.elapsedRealtime()
                    (if (local) lastLocalAudio else lastRemoteAudio).set(now)
                    if (now - lastRecorded >= 100) {
                        lastRecorded = now
                        record(if (local) "microphone_pcm" else "remote_audio_pcm", peak.toString())
                    }
                }
            }
        }
        sinks.add(track to sink)
        track.addSink(sink)
        record(if (local) "microphone_sink_attached" else "speaker_sink_attached")
    }

    private fun await(description: String, timeout: Long = 60000, condition: () -> Boolean) {
        val deadline = SystemClock.elapsedRealtime() + timeout
        while (SystemClock.elapsedRealtime() < deadline) {
            if (condition()) return
            SystemClock.sleep(100)
        }
        throw AssertionError("Timed out: $description (see voice-timeline.json)")
    }

    private fun screenshot(name: String) {
        val image = InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot() ?: return
        File(directory, "$name.png").outputStream().use { image.compress(Bitmap.CompressFormat.PNG, 100, it) }
        image.recycle()
    }

    private fun networkStats(stage: String) {
        val current = room ?: return
        for (subscriber in listOf(false, true)) {
            val ready = CountDownLatch(1)
            val callback = livekit.org.webrtc.RTCStatsCollectorCallback { report ->
                val stats = JSONArray()
                report.statsMap.values.forEach { stat ->
                    if (stat.type in setOf("candidate-pair", "inbound-rtp", "outbound-rtp", "local-candidate", "remote-candidate")) {
                        val safe = JSONObject().put("type", stat.type)
                        // No IP addresses, URLs, identities, or credentials in reports.
                        for (key in listOf("state", "nominated", "protocol", "candidateType", "currentRoundTripTime",
                            "availableOutgoingBitrate", "kind", "packetsSent", "packetsReceived", "packetsLost", "jitter",
                            "totalSamplesReceived", "concealedSamples", "totalAudioEnergy")) {
                            stat.members[key]?.let { safe.put(key, it) }
                        }
                        stats.put(safe)
                    }
                }
                record("rtc_stats_${if (subscriber) "downlink" else "uplink"}_$stage", stats.toString())
                ready.countDown()
            }
            if (subscriber) current.getSubscriberRTCStats(callback) else current.getPublisherRTCStats(callback)
            ready.await(3, TimeUnit.SECONDS)
        }
    }

    private fun uiText(tag: String): String = compose.onAllNodesWithTag(tag, useUnmergedTree = true)
        .fetchSemanticsNodes(atLeastOneRootRequired = false)
        .flatMap { it.config.getOrElse(SemanticsProperties.Text) { emptyList() } }
        .joinToString(" ") { it.text }

    private fun turn(number: Int) {
        val previousUser = uiText("user_transcript")
        val previousAgent = uiText("agent_transcript")
        val requested = SystemClock.elapsedRealtime()
        record("turn_${number}_ready")
        File(directory, "inject-$number.ready").writeText(requested.toString())
        await("virtual microphone injection", 30000) { File(directory, "inject-$number.started").exists() }
        record("turn_${number}_injection_started")
        await("AudioRecord receives injected speech", 30000) { lastLocalAudio.get() > requested }
        val microphoneTime = lastLocalAudio.get()
        record("turn_${number}_microphone_confirmed")
        await("user transcription in app UI", 60000) {
            val text = uiText("user_transcript")
            text.isNotBlank() && text != previousUser
        }
        record("turn_${number}_user_visible", uiText("user_transcript"))
        screenshot("turn-$number-user")
        await("injected speech finished", 30000) { File(directory, "inject-$number.finished").exists() }
        record("turn_${number}_speech_finished")
        await("agent audio reply", 90000) { lastRemoteAudio.get() > microphoneTime }
        record("turn_${number}_reply_audio_confirmed")
        await("agent reply in app UI", 30000) {
            val text = uiText("agent_transcript")
            text.isNotBlank() && text != previousAgent
        }
        record("turn_${number}_agent_visible", uiText("agent_transcript"))
        await("reply playback completed", 90000) {
            SystemClock.elapsedRealtime() - lastRemoteAudio.get() >= 1800
        }
        screenshot("turn-$number-reply")
        networkStats("turn_$number")
    }

    @Test fun realAppMicrophoneTranscriptionAndPlayback() {
        // Exercise the settings UI itself. Never echo credentials in assertions.
        try {
            compose.onNodeWithTag("open_settings").performClick()
            compose.onNodeWithTag("mode_DIRECT").performClick()
            compose.onNodeWithTag("server_url").performTextReplacement(config.getString("url"))
            compose.onNodeWithTag("connection_token").performTextReplacement(config.getString("token"))
            compose.onNodeWithTag("save_settings").performScrollTo().performClick()
        } catch (_: Throwable) {
            throw AssertionError("Could not save runtime connection configuration")
        }
        record("configuration_saved")
        screenshot("home")
        File(directory, "video.ready").writeText("ready")
        compose.onNodeWithTag("start_call").performClick()
        record("start_call_clicked")
        await("RTC connected and microphone published") {
            val current = room
            val track = current?.localParticipant?.getTrackPublication(Track.Source.MICROPHONE)?.track
            if (track is AudioTrack) attachAudio(track, true)
            current?.state == Room.State.CONNECTED && track != null
        }
        record("microphone_published")
        networkStats("connected")
        compose.onNodeWithContentDescription("Toggle Chat").performClick()
        await("greeting audio", 90000) { lastRemoteAudio.get() > 0 }
        await("greeting finished", 90000) { SystemClock.elapsedRealtime() - lastRemoteAudio.get() >= 1800 }
        screenshot("connected")
        turn(1)
        // Reproduce toggling the microphone, a separate publish/mute failure path.
        compose.onNodeWithContentDescription("Toggle Microphone").performClick()
        await("microphone muted") { lastDetail("microphone_state") == "false" }
        SystemClock.sleep(700)
        record("microphone_reenable_clicked")
        compose.onNodeWithContentDescription("Toggle Microphone").performClick()
        await("microphone re-enabled", 45000) { lastDetail("microphone_state") == "true" }
        turn(2)
        compose.onNodeWithContentDescription("End Call").performClick()
        compose.onNodeWithTag("start_call").assertExists()
        record("end_call_clicked")
        assertTrue("Captured microphone and agent PCM", lastLocalAudio.get() > 0 && lastRemoteAudio.get() > 0)
        ProviderLatencyProbe(context, config).run()
        record("test_completed")
    }

    @After fun finish() {
        if (!active) return
        VoiceSessionProbe.listener = null
        VoiceSessionProbe.onRoomCreated = null
        sinks.forEach { (track, sink) -> runCatching { track.removeSink(sink) } }
        scope.cancel()
        File(directory, "voice-timeline.json").writeText(JSONObject()
            .put("clock", "Android elapsedRealtime milliseconds")
            .put("input", "synthetic bilingual speech injected into emulator microphone")
            .put("events", JSONArray(events.toList())).toString(2))
        // Avoid taking a failure screenshot while the settings token is editable.
        File(directory, "test.finished").writeText("finished")
        File(context.noBackupFilesDir, "voice-test-config.json").delete()
    }
}
