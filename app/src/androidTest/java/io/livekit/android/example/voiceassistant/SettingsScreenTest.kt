package io.livekit.android.example.voiceassistant

import android.app.Application
import android.content.Context
import android.graphics.Bitmap
import androidx.compose.ui.test.*
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.lifecycle.ViewModelProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import io.livekit.android.example.voiceassistant.settings.ConnectionMode
import io.livekit.android.example.voiceassistant.settings.LiveKitSettings
import io.livekit.android.example.voiceassistant.settings.LiveKitSettingsStore
import io.livekit.android.example.voiceassistant.viewmodel.VoiceAssistantViewModel
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

@RunWith(AndroidJUnit4::class)
class SettingsScreenTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    private val context: Context get() = InstrumentationRegistry.getInstrumentation().targetContext
    private val store get() = LiveKitSettingsStore(context)

    @Before fun resetSettings() {
        File(context.noBackupFilesDir, LiveKitSettingsStore.FILE_NAME).delete()
    }

    @After fun captureScreen() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val screenshot = instrumentation.uiAutomation.takeScreenshot() ?: return
        val directory = File(context.getExternalFilesDir(null), "test-screenshots").apply { mkdirs() }
        File(directory, "settings-${System.nanoTime()}.png").outputStream().use {
            screenshot.compress(Bitmap.CompressFormat.PNG, 100, it)
        }
        screenshot.recycle()
    }

    private fun openSettings() {
        compose.onNodeWithTag("open_settings").performClick()
    }

    private fun save() {
        compose.onNodeWithTag("save_settings").performScrollTo().performClick()
    }

    @Test fun defaultsAndModeFieldsAreDisplayedCorrectly() {
        openSettings()
        compose.onNodeWithTag("token_endpoint").assertTextContains("https://livekit.com/api/homepage-agent/token")
        compose.onNodeWithTag("mode_DEVELOPMENT_TOKEN_SERVER").performClick()
        compose.onNodeWithTag("token_server_id").assertExists()
        compose.onNodeWithTag("token_endpoint").assertDoesNotExist()
        compose.onNodeWithTag("mode_DIRECT").performClick()
        compose.onNodeWithTag("server_url").assertExists()
        compose.onNodeWithTag("connection_token").assertExists()
        compose.onNodeWithTag("token_server_id").assertDoesNotExist()
    }

    @Test fun invalidConfigurationShowsAnErrorAndDoesNotSave() {
        openSettings()
        compose.onNodeWithTag("mode_DEVELOPMENT_TOKEN_SERVER").performClick()
        save()
        compose.onNodeWithText("请填写 Token 服务 ID").assertExists()
        assertEquals(ConnectionMode.TOKEN_ENDPOINT, store.load().mode)
    }

    @Test fun developmentSettingsPersistAcrossActivityRecreation() {
        openSettings()
        compose.onNodeWithTag("mode_DEVELOPMENT_TOKEN_SERVER").performClick()
        compose.onNodeWithTag("token_server_id").performTextInput("test-development-id")
        save()
        compose.onNodeWithTag("open_settings").assertExists()
        compose.activityRule.scenario.recreate()
        openSettings()
        compose.onNodeWithTag("token_server_id").assertTextContains("test-development-id")
        assertEquals("test-development-id", LiveKitSettingsStore(context).load().tokenServerId)
    }

    @Test fun cancelAndBackKeepTheExistingSavedConfiguration() {
        store.save(LiveKitSettings(tokenEndpoint = "https://saved.example.com/token"))
        openSettings()
        compose.onNodeWithTag("token_endpoint").performTextReplacement("https://cancelled.example.com/token")
        compose.onNodeWithTag("cancel_settings").performScrollTo().performClick()
        assertEquals("https://saved.example.com/token", store.load().tokenEndpoint)
        openSettings()
        compose.onNodeWithTag("token_endpoint").assertTextContains("https://saved.example.com/token")
        compose.onNodeWithTag("token_endpoint").performTextReplacement("https://back.example.com/token")
        compose.onNodeWithContentDescription("返回").performClick()
        assertEquals("https://saved.example.com/token", store.load().tokenEndpoint)
    }

    @Test fun tokenIsHiddenAndStoredAsAuthenticatedCiphertext() {
        openSettings()
        compose.onNodeWithTag("mode_DIRECT").performClick()
        compose.onNodeWithTag("server_url").performTextInput("wss://test.livekit.cloud")
        compose.onNodeWithTag("connection_token").performTextInput("test-secret-not-a-real-token")
        compose.onNodeWithTag("connection_token").assert(SemanticsMatcher.keyIsDefined(SemanticsProperties.Password))
        compose.onNodeWithContentDescription("显示 Token").assertExists().performClick()
        compose.onNodeWithContentDescription("隐藏 Token").assertExists().performClick()
        save()
        val saved = store.load()
        assertEquals("test-secret-not-a-real-token", saved.token)
        val bytes = File(context.noBackupFilesDir, LiveKitSettingsStore.FILE_NAME).readBytes()
        assertFalse(String(bytes, Charsets.ISO_8859_1).contains(saved.token))
        openSettings()
        compose.onNodeWithContentDescription("显示 Token").assertExists()
    }

    @Test fun nextCallSnapshotReadsTheLatestSavedConfiguration() {
        val app = context.applicationContext as Application
        store.save(LiveKitSettings(mode = ConnectionMode.DIRECT, serverUrl = "wss://first.livekit.cloud", token = "fake-first"))
        compose.runOnIdle {
            val firstOwner = androidx.lifecycle.ViewModelStore()
            val first = ViewModelProvider(firstOwner, ViewModelProvider.AndroidViewModelFactory.getInstance(app))[VoiceAssistantViewModel::class.java]
            store.save(LiveKitSettings(mode = ConnectionMode.DIRECT, serverUrl = "wss://second.livekit.cloud", token = "fake-second"))
            val secondOwner = androidx.lifecycle.ViewModelStore()
            val second = ViewModelProvider(secondOwner, ViewModelProvider.AndroidViewModelFactory.getInstance(app))[VoiceAssistantViewModel::class.java]
            assertEquals("fake-first", first.connectionSettings.token)
            assertEquals("fake-second", second.connectionSettings.token)
            firstOwner.clear()
            secondOwner.clear()
        }
    }

    @Test fun corruptedSavedDataBlocksConnectionInsteadOfUsingDemo() {
        File(context.noBackupFilesDir, LiveKitSettingsStore.FILE_NAME).writeText("invalid encrypted data")
        compose.onNodeWithTag("start_call").performClick()
        compose.onNodeWithText("无法读取已保存的配置，请在 LiveKit 设置中重新保存").assertExists()
        openSettings()
        compose.onNodeWithTag("token_endpoint").performTextReplacement("https://recovered.example.com/token")
        save()
        assertEquals("https://recovered.example.com/token", store.load().tokenEndpoint)
    }
}
