package io.livekit.android.example.voiceassistant

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.ui.Modifier
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import io.livekit.android.LiveKit
import io.livekit.android.example.voiceassistant.screen.ConnectRoute
import io.livekit.android.example.voiceassistant.screen.ConnectScreen
import io.livekit.android.example.voiceassistant.screen.SettingsRoute
import io.livekit.android.example.voiceassistant.screen.SettingsScreen
import io.livekit.android.example.voiceassistant.screen.VoiceAssistantRoute
import io.livekit.android.example.voiceassistant.screen.VoiceAssistantScreen
import io.livekit.android.example.voiceassistant.ui.theme.LiveKitVoiceAssistantExampleTheme
import io.livekit.android.example.voiceassistant.viewmodel.SettingsViewModel
import io.livekit.android.example.voiceassistant.viewmodel.VoiceAssistantViewModel
import io.livekit.android.util.LoggingLevel

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Token fetching can produce SDK diagnostics; never enable verbose credential logging.
        LiveKit.loggingLevel = LoggingLevel.OFF
        setContent {
            val navController = rememberNavController()
            LiveKitVoiceAssistantExampleTheme(dynamicColor = false) {
                Scaffold { innerPadding ->
                    Box(modifier = Modifier.padding(innerPadding)) {
                        NavHost(navController, startDestination = ConnectRoute) {
                            composable<ConnectRoute> {
                                ConnectScreen(
                                    navigateToVoiceAssistant = { navController.navigate(VoiceAssistantRoute) },
                                    navigateToSettings = { navController.navigate(SettingsRoute) },
                                )
                            }
                            composable<SettingsRoute> {
                                SettingsScreen(viewModel<SettingsViewModel>(), onBack = { navController.navigateUp() })
                            }
                            composable<VoiceAssistantRoute> {
                                VoiceAssistantScreen(
                                    viewModel = viewModel<VoiceAssistantViewModel>(),
                                    onEndCall = { runOnUiThread { navController.navigateUp() } },
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}
