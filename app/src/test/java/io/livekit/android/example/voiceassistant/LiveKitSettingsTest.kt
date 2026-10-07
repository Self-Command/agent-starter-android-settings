package io.livekit.android.example.voiceassistant

import io.livekit.android.example.voiceassistant.settings.ConnectionField
import io.livekit.android.example.voiceassistant.settings.ConnectionMode
import io.livekit.android.example.voiceassistant.settings.DEFAULT_TOKEN_ENDPOINT
import io.livekit.android.example.voiceassistant.settings.LiveKitSettings
import io.livekit.android.example.voiceassistant.settings.createTokenSource
import io.livekit.android.token.FixedTokenSource
import io.livekit.android.token.ConfigurableTokenSource
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

class LiveKitSettingsTest {
    @Test fun initialInstallUsesTheExistingDemoEndpoint() {
        val settings = LiveKitSettings()
        assertEquals(ConnectionMode.TOKEN_ENDPOINT, settings.mode)
        assertEquals(DEFAULT_TOKEN_ENDPOINT, settings.tokenEndpoint)
        assertTrue(settings.validationErrors().isEmpty())
    }

    @Test fun developmentModeRequiresItsIdEvenWhenOtherModesHaveValidValues() {
        val settings = LiveKitSettings(mode = ConnectionMode.DEVELOPMENT_TOKEN_SERVER, serverUrl = "wss://demo.livekit.cloud", token = "fake-token")
        assertEquals(setOf(ConnectionField.TOKEN_SERVER_ID), settings.validationErrors().keys)
    }

    @Test fun developmentModeIgnoresInactiveFields() {
        val settings = LiveKitSettings(mode = ConnectionMode.DEVELOPMENT_TOKEN_SERVER, tokenServerId = " dev-id ", tokenEndpoint = "invalid")
        assertTrue(settings.validationErrors().isEmpty())
        assertTrue(settings.createTokenSource() is ConfigurableTokenSource)
    }

    @Test fun directModeRequiresBothAddressAndToken() {
        val settings = LiveKitSettings(mode = ConnectionMode.DIRECT)
        assertEquals(setOf(ConnectionField.SERVER_URL, ConnectionField.TOKEN), settings.validationErrors().keys)
        assertEquals(setOf(ConnectionField.TOKEN), settings.copy(serverUrl = "wss://demo.livekit.cloud").validationErrors().keys)
    }

    @Test fun directModeRejectsHttpCredentialsFragmentsAndInvalidPorts() {
        listOf("https://demo.livekit.cloud", "wss://", "wss://user:pass@demo.livekit.cloud", "wss://demo.livekit.cloud#fragment", "wss://demo.livekit.cloud:65536").forEach { url ->
            assertTrue(url, LiveKitSettings(mode = ConnectionMode.DIRECT, serverUrl = url, token = "fake").validationErrors().containsKey(ConnectionField.SERVER_URL))
        }
    }

    @Test fun directTokenSourceUsesOnlyTheSelectedSavedPair() = runBlocking {
        val settings = LiveKitSettings(mode = ConnectionMode.DIRECT, tokenServerId = "inactive-id", serverUrl = " wss://chosen.livekit.cloud ", token = " fake-token ")
        val source = settings.createTokenSource()
        assertTrue(source is FixedTokenSource)
        val response = (source as FixedTokenSource).fetch().getOrThrow()
        assertEquals("wss://chosen.livekit.cloud", response.serverUrl)
        assertEquals("fake-token", response.participantToken)
    }

    @Test fun endpointModeRejectsWebsocketAndBlankUrls() {
        listOf("", " ", "wss://demo.livekit.cloud", "https://", "invalid address").forEach { url ->
            assertEquals(setOf(ConnectionField.TOKEN_ENDPOINT), LiveKitSettings(tokenEndpoint = url).validationErrors().keys)
        }
    }

    @Test fun endpointModeAcceptsHttpAndHttpsWithoutUsingInactiveIds() {
        listOf("https://example.com/token", "http://127.0.0.1:8080/token").forEach { url ->
            val settings = LiveKitSettings(tokenEndpoint = url, tokenServerId = "inactive-id")
            assertTrue(settings.validationErrors().isEmpty())
            assertTrue(settings.createTokenSource() is ConfigurableTokenSource)
        }
    }

    @Test fun invalidSelectedModeCannotFallBackToDemo() {
        val settings = LiveKitSettings(mode = ConnectionMode.DIRECT)
        assertThrows(IllegalArgumentException::class.java) { settings.createTokenSource() }
    }

    @Test fun credentialsAreNormalizedAndRedactedFromDiagnostics() {
        val settings = LiveKitSettings(tokenServerId = " id ", serverUrl = " url ", token = " unique-secret ", tokenEndpoint = " endpoint ").normalized()
        assertEquals("id", settings.tokenServerId)
        assertEquals("url", settings.serverUrl)
        assertEquals("unique-secret", settings.token)
        assertEquals("endpoint", settings.tokenEndpoint)
        assertFalse(settings.toString().contains("unique-secret"))
    }
}
