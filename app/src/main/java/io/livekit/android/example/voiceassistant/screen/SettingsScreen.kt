package io.livekit.android.example.voiceassistant.screen

import android.widget.Toast
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import io.livekit.android.example.voiceassistant.settings.ConnectionField
import io.livekit.android.example.voiceassistant.settings.ConnectionMode
import io.livekit.android.example.voiceassistant.viewmodel.SettingsViewModel
import kotlinx.serialization.Serializable

@Serializable
object SettingsRoute

@Composable
fun SettingsScreen(viewModel: SettingsViewModel, onBack: () -> Unit) {
    val settings = viewModel.settings
    val context = LocalContext.current
    var tokenVisible by remember { mutableStateOf(false) }
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack) { Icon(Icons.Default.ArrowBack, contentDescription = "返回") }
            Text("LiveKit 设置", style = MaterialTheme.typography.headlineSmall)
        }
        Text("选择连接方式。保存后，下次开始通话使用新的配置。", style = MaterialTheme.typography.bodyMedium)
        ConnectionMode.entries.forEach { mode ->
            val label = when (mode) {
                ConnectionMode.DEVELOPMENT_TOKEN_SERVER -> "开发 Token 服务"
                ConnectionMode.DIRECT -> "服务器地址 + Token"
                ConnectionMode.TOKEN_ENDPOINT -> "Token 接口"
            }
            Row(
                modifier = Modifier.fillMaxWidth().testTag("mode_${mode.name}").selectable(
                    selected = settings.mode == mode,
                    role = Role.RadioButton,
                    onClick = { tokenVisible = false; viewModel.update(settings.copy(mode = mode)) },
                ).padding(vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                RadioButton(selected = settings.mode == mode, onClick = null)
                Text(label, modifier = Modifier.padding(start = 8.dp))
            }
        }
        when (settings.mode) {
            ConnectionMode.DEVELOPMENT_TOKEN_SERVER -> {
                SettingsField(
                    value = settings.tokenServerId,
                    onChange = { viewModel.update(settings.copy(tokenServerId = it)) },
                    label = "Token 服务 ID",
                    tag = "token_server_id",
                    error = viewModel.errors[ConnectionField.TOKEN_SERVER_ID],
                )
                Text("在 LiveKit Cloud 项目设置中开启开发 Token 服务，然后复制服务 ID。此方式用于开发测试。")
            }
            ConnectionMode.DIRECT -> {
                SettingsField(
                    value = settings.serverUrl,
                    onChange = { viewModel.update(settings.copy(serverUrl = it)) },
                    label = "LiveKit 服务器地址",
                    tag = "server_url",
                    placeholder = "wss://your-project.livekit.cloud",
                    error = viewModel.errors[ConnectionField.SERVER_URL],
                    keyboardType = KeyboardType.Uri,
                )
                OutlinedTextField(
                    value = settings.token,
                    onValueChange = { viewModel.update(settings.copy(token = it)) },
                    label = { Text("连接 Token") },
                    modifier = Modifier.fillMaxWidth().testTag("connection_token"),
                    singleLine = true,
                    isError = viewModel.errors.containsKey(ConnectionField.TOKEN),
                    supportingText = viewModel.errors[ConnectionField.TOKEN]?.let { error -> { Text(error) } },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                    visualTransformation = if (tokenVisible) VisualTransformation.None else PasswordVisualTransformation(),
                    trailingIcon = {
                        IconButton(onClick = { tokenVisible = !tokenVisible }) {
                            Icon(
                                if (tokenVisible) Icons.Default.VisibilityOff else Icons.Default.Visibility,
                                contentDescription = if (tokenVisible) "隐藏 Token" else "显示 Token",
                            )
                        }
                    },
                )
                Text("填写连接房间用的 JWT Token；不是 API Key。Token 过期后需要更新。")
            }
            ConnectionMode.TOKEN_ENDPOINT -> {
                SettingsField(
                    value = settings.tokenEndpoint,
                    onChange = { viewModel.update(settings.copy(tokenEndpoint = it)) },
                    label = "Token 接口地址",
                    tag = "token_endpoint",
                    placeholder = "https://your-server/token",
                    error = viewModel.errors[ConnectionField.TOKEN_ENDPOINT],
                    keyboardType = KeyboardType.Uri,
                )
                Text("接口需要按 LiveKit TokenSource 格式返回服务器地址和连接 Token。初始地址为官方演示服务。")
            }
        }
        viewModel.message?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Button(
                modifier = Modifier.testTag("save_settings"),
                onClick = {
                    if (viewModel.save()) {
                        Toast.makeText(context, "LiveKit 配置已保存", Toast.LENGTH_SHORT).show()
                        onBack()
                    }
                },
            ) { Text("保存") }
            TextButton(modifier = Modifier.testTag("cancel_settings"), onClick = onBack) { Text("取消") }
        }
    }
}

@Composable
private fun SettingsField(
    value: String,
    onChange: (String) -> Unit,
    label: String,
    tag: String,
    error: String?,
    placeholder: String = "",
    keyboardType: KeyboardType = KeyboardType.Text,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onChange,
        label = { Text(label) },
        placeholder = { Text(placeholder) },
        modifier = Modifier.fillMaxWidth().testTag(tag),
        singleLine = true,
        isError = error != null,
        supportingText = error?.let { { Text(it) } },
        keyboardOptions = KeyboardOptions(keyboardType = keyboardType),
    )
}
