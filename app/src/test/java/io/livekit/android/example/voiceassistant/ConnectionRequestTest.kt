package io.livekit.android.example.voiceassistant

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import io.livekit.android.example.voiceassistant.settings.*
import io.livekit.android.token.ConfigurableTokenSource
import io.livekit.android.token.FixedTokenSource
import io.livekit.android.token.TokenRequestOptions
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.net.ServerSocket
import java.util.concurrent.Executors
import java.nio.file.Files
import java.nio.file.Paths
import java.util.concurrent.atomic.AtomicReference

/** Calls the production App factory and the actual SDK HTTP implementation on the JVM. */
class ConnectionRequestTest {
    @Test fun selectedRequestContainsOnlyItsNormalizedFields() {
        val settings = LiveKitSettings(mode = ConnectionMode.DIRECT, serverUrl = " wss://chosen.livekit.cloud ", token = " secret ", tokenServerId = "ignored")
        val direct = settings.connectionRequest() as LiveKitConnectionRequest.Direct
        assertEquals("wss://chosen.livekit.cloud", direct.url)
        assertEquals("secret", direct.token)
        assertFalse(direct.toString().contains("secret"))
        val dev = settings.copy(mode = ConnectionMode.DEVELOPMENT_TOKEN_SERVER, tokenServerId = " chosen-id ").connectionRequest()
        assertEquals("chosen-id", (dev as LiveKitConnectionRequest.Development).id)
        val endpoint = settings.copy(mode = ConnectionMode.TOKEN_ENDPOINT, tokenEndpoint = " https://chosen.example/token ").connectionRequest()
        assertEquals("https://chosen.example/token", (endpoint as LiveKitConnectionRequest.Endpoint).url)
    }

    @Test fun appEndpointUsesSdkPostProtocolAndReturnsServerCredentials() = runBlocking {
        val received = AtomicReference<JsonObject>()
        val server = ServerSocket(0, 1, java.net.InetAddress.getByName("127.0.0.1"))
        val executor = Executors.newSingleThreadExecutor()
        val serving = executor.submit {
            server.accept().use { socket ->
                socket.soTimeout = 5000
                val reader = socket.getInputStream().bufferedReader()
                assertEquals("POST /token HTTP/1.1", reader.readLine())
                val headers = mutableMapOf<String, String>()
                var line = reader.readLine()
                while (!line.isNullOrEmpty()) {
                    headers[line.substringBefore(':').lowercase()] = line.substringAfter(':').trim()
                    line = reader.readLine()
                }
                assertEquals("application/json", headers["content-type"])
                val content = CharArray(headers.getValue("content-length").toInt())
                var offset = 0
                while (offset < content.size) {
                    val n = reader.read(content, offset, content.size - offset)
                    require(n > 0)
                    offset += n
                }
                received.set(JsonParser.parseString(String(content)).asJsonObject)
                val body = """{"server_url":"wss://chosen.livekit.cloud","participant_token":"fictional-token"}""".toByteArray()
                socket.getOutputStream().apply {
                    write("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ${body.size}\r\nConnection: close\r\n\r\n".toByteArray())
                    write(body)
                    flush()
                }
            }
        }
        try {
            val source = LiveKitSettings(tokenEndpoint = "http://127.0.0.1:${server.localPort}/token").createTokenSource()
            val response = (source as ConfigurableTokenSource).fetch(TokenRequestOptions(roomName = "fictional-room", participantIdentity = "fictional-user")).getOrThrow()
            assertEquals("wss://chosen.livekit.cloud", response.serverUrl)
            assertEquals("fictional-token", response.participantToken)
            assertEquals("fictional-room", received.get()["room_name"].asString)
            assertEquals("fictional-user", received.get()["participant_identity"].asString)
            serving.get(5, java.util.concurrent.TimeUnit.SECONDS)
            Unit
        } finally { server.close(); executor.shutdownNow() }
    }

    @Test fun invalidConfigurationNeverCreatesADemoRequest() {
        assertThrows(IllegalArgumentException::class.java) {
            LiveKitSettings(mode = ConnectionMode.DIRECT, token = "fictional").createTokenSource()
        }
    }

    @Test fun resolveRuntimeConfigurationThroughTheAppFactory() = runBlocking {
        val raw = System.getenv("VOICE_TEST_CONFIG")
        assumeTrue("Live credentials are only injected into the manual diagnostic", !raw.isNullOrBlank())
        val config = JsonParser.parseString(raw).asJsonObject
        val mode = config.get("mode")?.asString?.let(ConnectionMode::valueOf) ?: ConnectionMode.DIRECT
        val settings = LiveKitSettings(mode = mode,
            serverUrl = config.get("url")?.asString.orEmpty(), token = config.get("token")?.asString.orEmpty(),
            tokenServerId = config.get("token_server_id")?.asString.orEmpty(),
            tokenEndpoint = config.get("token_endpoint")?.asString ?: DEFAULT_TOKEN_ENDPOINT)
        val start = System.nanoTime()
        val response = when (val source = settings.createTokenSource()) {
            is FixedTokenSource -> source.fetch().getOrThrow()
            is ConfigurableTokenSource -> source.fetch(TokenRequestOptions(participantIdentity = "action-client")).getOrThrow()
            else -> error("Unsupported SDK TokenSource")
        }
        // Secrets go only into the runner's private temporary file, never test output/artifacts.
        val resolved = JsonObject().apply {
            addProperty("url", response.serverUrl)
            addProperty("token", response.participantToken)
        }
        val target = Paths.get(System.getenv("RESOLVED_CONNECTION_FILE"))
        Files.write(target, resolved.toString().toByteArray())
        Files.setPosixFilePermissions(target, setOf(java.nio.file.attribute.PosixFilePermission.OWNER_READ, java.nio.file.attribute.PosixFilePermission.OWNER_WRITE))
        val report = JsonObject().apply {
            addProperty("implementation", "production App createTokenSource and LiveKit Android SDK 2.28.0")
            addProperty("mode", mode.name)
            addProperty("resolution_ms", (System.nanoTime() - start) / 1_000_000)
        }
        val reportPath = Paths.get("build/reports/connection-request.json")
        Files.createDirectories(reportPath.parent)
        Files.write(reportPath, report.toString().toByteArray())
        Unit
    }
}
