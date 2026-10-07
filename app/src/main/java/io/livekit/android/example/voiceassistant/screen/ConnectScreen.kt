package io.livekit.android.example.voiceassistant.screen

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import io.livekit.android.example.voiceassistant.R
import io.livekit.android.example.voiceassistant.settings.LiveKitSettingsStore
import io.livekit.android.example.voiceassistant.ui.theme.Blue500
import kotlinx.serialization.Serializable

@Serializable
object ConnectRoute

@Composable
fun ConnectScreen(navigateToVoiceAssistant: () -> Unit, navigateToSettings: () -> Unit) {
    val context = LocalContext.current
    var message by remember { mutableStateOf<String?>(null) }
    Box(
        contentAlignment = Alignment.Center,
        modifier = Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background),
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Image(painter = painterResource(R.drawable.connect_icon), contentDescription = "Connect icon")
            Spacer(Modifier.size(4.dp))
            Text(
                "Start a call to chat with your voice agent.",
                textAlign = TextAlign.Center,
                modifier = Modifier.fillMaxWidth(0.8f),
            )
            message?.let {
                Text(it, color = MaterialTheme.colorScheme.error, modifier = Modifier.fillMaxWidth(0.8f))
            }
            Spacer(Modifier.size(12.dp))
            Button(
                modifier = Modifier.testTag("start_call"),
                colors = ButtonDefaults.buttonColors(containerColor = Blue500, contentColor = Color.White),
                shape = RoundedCornerShape(20),
                onClick = {
                    runCatching { LiveKitSettingsStore(context).load() }
                        .onSuccess { settings ->
                            if (settings.validationErrors().isEmpty()) {
                                message = null
                                navigateToVoiceAssistant()
                            } else {
                                message = "请检查 LiveKit 设置中的连接配置"
                            }
                        }
                        .onFailure { message = "无法读取已保存的配置，请在 LiveKit 设置中重新保存" }
                },
            ) { Text("START CALL") }
            TextButton(modifier = Modifier.testTag("open_settings"), onClick = navigateToSettings) {
                Text("LiveKit 设置")
            }
        }
    }
}
