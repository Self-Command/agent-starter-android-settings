package io.livekit.android.example.voiceassistant.viewmodel

import android.app.Application
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import io.livekit.android.example.voiceassistant.settings.ConnectionField
import io.livekit.android.example.voiceassistant.settings.LiveKitSettings
import io.livekit.android.example.voiceassistant.settings.LiveKitSettingsStore

class SettingsViewModel(application: Application) : AndroidViewModel(application) {
    private val store = LiveKitSettingsStore(application)
    var settings by mutableStateOf(LiveKitSettings())
        private set
    var errors by mutableStateOf<Map<ConnectionField, String>>(emptyMap())
        private set
    var message by mutableStateOf<String?>(null)
        private set

    init {
        runCatching { store.load() }
            .onSuccess { settings = it }
            .onFailure { message = "无法读取已保存的配置，请填写并重新保存" }
    }

    fun update(value: LiveKitSettings) {
        settings = value
        errors = emptyMap()
        message = null
    }

    fun save(): Boolean {
        errors = settings.validationErrors()
        if (errors.isNotEmpty()) return false
        return runCatching { store.save(settings) }
            .onSuccess { settings = settings.normalized() }
            .onFailure { message = "保存失败，请重试" }
            .isSuccess
    }
}
