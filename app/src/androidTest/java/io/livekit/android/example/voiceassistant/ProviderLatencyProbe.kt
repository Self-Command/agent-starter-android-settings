package io.livekit.android.example.voiceassistant

import android.content.Context
import android.os.SystemClock
import okhttp3.Call
import okhttp3.EventListener
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.InetSocketAddress
import java.net.Proxy
import java.util.Base64
import java.util.concurrent.Callable
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** Android-only direct-provider controls, separate from the real app RTC test. */
internal class ProviderLatencyProbe(private val context: Context, private val config: JSONObject) {
    private val client = OkHttpClient.Builder().callTimeout(75, TimeUnit.SECONDS).build()
    private val sentence = "连接正常，我正在使用 LiveKit 开发 Android App。"

    private fun measure(name: String, provider: JSONObject, payload: JSONObject, audio: Boolean): JSONObject {
        val start = SystemClock.elapsedRealtime()
        val result = JSONObject().put("case", name)
        val network = JSONObject()
        val listener = object : EventListener() {
            override fun dnsStart(call: Call, domainName: String) { network.put("dns_start_ms", SystemClock.elapsedRealtime() - start) }
            override fun dnsEnd(call: Call, domainName: String, inetAddressList: List<java.net.InetAddress>) { network.put("dns_end_ms", SystemClock.elapsedRealtime() - start) }
            override fun connectStart(call: Call, inetSocketAddress: InetSocketAddress, proxy: Proxy) { network.put("connect_start_ms", SystemClock.elapsedRealtime() - start) }
            override fun secureConnectStart(call: Call) { network.put("tls_start_ms", SystemClock.elapsedRealtime() - start) }
            override fun secureConnectEnd(call: Call, handshake: okhttp3.Handshake?) { network.put("tls_end_ms", SystemClock.elapsedRealtime() - start) }
        }
        try {
            val request = Request.Builder().url(provider.getString("base_url").trimEnd('/') + "/chat/completions")
                .header("Authorization", "Bearer " + provider.getString("api_key"))
                .post(payload.toString().toRequestBody("application/json".toMediaType())).build()
            client.newBuilder().eventListener(listener).build().newCall(request).execute().use { response ->
                result.put("http_status", response.code).put("headers_ms", SystemClock.elapsedRealtime() - start)
                    .put("content_type", response.header("content-type"))
                    .put("retry_after", response.header("retry-after"))
                if (!response.isSuccessful) return result.put("total_ms", SystemClock.elapsedRealtime() - start).put("network", network)
                val body = checkNotNull(response.body)
                var chunks = 0
                var bytes = 0
                var first: Long? = null
                if (response.header("content-type", "")!!.contains("text/event-stream")) {
                    val source = body.source()
                    while (!source.exhausted()) {
                        val line = source.readUtf8Line() ?: break
                        if (!line.startsWith("data:")) continue
                        val data = line.substringAfter(':').trim()
                        if (data == "[DONE]") break
                        val event = runCatching { JSONObject(data) }.getOrNull() ?: continue
                        val choice = event.optJSONArray("choices")?.optJSONObject(0)
                        val delta = choice?.optJSONObject("delta")
                        val encoded = delta?.optJSONObject("audio")?.optString("data").orEmpty()
                        val text = delta?.optString("content").orEmpty()
                        if ((audio && encoded.isNotBlank()) || (!audio && text.isNotBlank())) {
                            if (first == null) first = SystemClock.elapsedRealtime() - start
                            chunks++
                            bytes += if (audio) Base64.getDecoder().decode(encoded).size else text.toByteArray().size
                        }
                        if (event.has("error")) result.put("stream_error", true)
                    }
                    result.put("stream_response", true)
                } else {
                    val json = JSONObject(body.string())
                    val message = json.optJSONArray("choices")?.optJSONObject(0)?.optJSONObject("message")
                    val encoded = message?.optJSONObject("audio")?.optString("data").orEmpty()
                    if (audio && encoded.isNotBlank()) {
                        bytes = Base64.getDecoder().decode(encoded).size
                        first = SystemClock.elapsedRealtime() - start
                        chunks = 1
                    } else if (!audio && message?.optString("content").orEmpty().isNotBlank()) {
                        first = SystemClock.elapsedRealtime() - start
                        chunks = 1
                    }
                    result.put("stream_response", false)
                }
                result.put("first_payload_ms", first ?: JSONObject.NULL).put("payload_chunks", chunks)
                    .put("payload_bytes", bytes)
                if (audio && payload.optJSONObject("audio")?.optString("format") == "pcm16") {
                    result.put("audio_seconds", bytes / 48000.0)
                }
            }
        } catch (error: Exception) {
            // Exception messages may contain request URLs; retain only the type.
            result.put("error_type", error.javaClass.simpleName)
        }
        return result.put("total_ms", SystemClock.elapsedRealtime() - start).put("network", network)
    }

    private fun ttsPayload(text: String, stream: Boolean) = JSONObject()
        .put("model", config.getJSONObject("tts").getString("model"))
        .put("messages", JSONArray().put(JSONObject().put("role", "assistant").put("content", text)))
        .put("audio", JSONObject().put("format", if (stream) "pcm16" else "wav")
            .put("voice", config.getJSONObject("tts").getString("voice")))
        .put("stream", stream)

    fun run() {
        val results = JSONArray()
        val llm = config.getJSONObject("llm")
        val tts = config.getJSONObject("tts")
        val voicePrompt = "你是一个自然、简洁的中英双语语音助手，主要使用中文回答；用户使用英语时可以用英语回答。先直接回答问题，通常一到两句话，尽量不超过80个汉字。不使用Markdown，不假装执行未执行的操作。"
        for ((name, prompt) in listOf("llm_simple" to "只回答一句话。", "llm_voice_context" to voicePrompt)) {
            val payload = JSONObject().put("model", llm.getString("model")).put("stream", true)
                .put("max_completion_tokens", 256)
                .put("messages", JSONArray().put(JSONObject().put("role", "system").put("content", prompt))
                    .put(JSONObject().put("role", "user").put("content", "你好，请只回答连接正常。")))
            results.put(measure(name, llm, payload, false))
        }
        results.put(measure("tts_current_wav", tts, ttsPayload(sentence, false), true))
        results.put(measure("tts_stream_pcm16", tts, ttsPayload(sentence, true), true))
        results.put(measure("tts_stream_short", tts, ttsPayload("连接正常。", true), true))
        val executor = Executors.newFixedThreadPool(2)
        try {
            val futures = (1..2).map { number -> executor.submit(Callable {
                measure("tts_concurrent_$number", tts, ttsPayload("连接正常。", true), true)
            }) }
            futures.forEach { results.put(it.get(90, TimeUnit.SECONDS)) }
        } finally {
            executor.shutdownNow()
        }
        File(context.getExternalFilesDir(null), "voice-e2e/provider-comparison.json").writeText(JSONObject()
            .put("origin", "Android emulator instrumentation; direct HTTP controls after app call")
            .put("llm_model", llm.getString("model")).put("tts_model", tts.getString("model"))
            .put("rate_probe", "two concurrent short TTS requests; does not establish sustained quota")
            .put("results", results).toString(2))
    }
}
