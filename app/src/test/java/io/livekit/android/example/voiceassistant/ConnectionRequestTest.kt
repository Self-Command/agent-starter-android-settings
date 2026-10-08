package io.livekit.android.example.voiceassistant

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import com.sun.net.httpserver.HttpServer
import io.livekit.android.example.voiceassistant.settings.*
import io.livekit.android.token.ConfigurableTokenSource
import io.livekit.android.token.FixedTokenSource
import io.livekit.android.token.TokenRequestOptions
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.net.InetSocketAddress
import java.nio.file.Files
import java.nio.file.Path
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
        val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/token") { exchange ->
            assertEquals("POST", exchange.requestMethod)
            assertEquals("application/json", exchange.requestHeaders.getFirst("Content-Type"))
            received.set(JsonParser.parseString(exchange.requestBody.bufferedReader().readText()).asJsonObject)
            val body = """{"server_url":"wss://chosen.livekit.cloud","participant_token":"fictional-token"}""".toByteArray()
            exchange.sendResponseHeaders(200, body.size.toLong())
            exchange.responseBody.use { it.write(body) }
            exchange.close()
        }
        server.start()
        try {
            val source = LiveKitSettings(tokenEndpoint = "http://127.0.0.1:${server.address.port}/token").createTokenSource()
            val response = (source as ConfigurableTokenSource).fetch(TokenRequestOptions(roomName = "fictional-room", participantIdentity = "fictional-user")).getOrThrow()
            assertEquals("wss://chosen.livekit.cloud", response.serverUrl)
            assertEquals("fictional-token", response.participantToken)
            assertEquals("fictional-room", received.get()["room_name"].asString)
            assertEquals("fictional-user", received.get()["participant_identity"].asString)
        } finally { server.stop(0) }
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
            is ConfigurableTokenSource -> source.fetch().getOrThrow()
            else -> error("Unsupported SDK TokenSource")
        }
        // Secrets go only into the runner's private temporary file, never test output/artifacts.
        val resolved = JsonObject().apply {
            addProperty("url", response.serverUrl)
            addProperty("token", response.participantToken)
        }
        val target = Path.of(System.getenv("RESOLVED_CONNECTION_FILE"))
        Files.writeString(target, resolved.toString())
        Files.setPosixFilePermissions(target, setOf(java.nio.file.attribute.PosixFilePermission.OWNER_READ, java.nio.file.attribute.PosixFilePermission.OWNER_WRITE))
        val report = JsonObject().apply {
            addProperty("implementation", "production App createTokenSource and LiveKit Android SDK 2.28.0")
            addProperty("mode", mode.name)
            addProperty("resolution_ms", (System.nanoTime() - start) / 1_000_000)
        }
        val reportPath = Path.of("build/reports/connection-request.json")
        Files.createDirectories(reportPath.parent)
        Files.writeString(reportPath, report.toString())
        Unit
    }
}
