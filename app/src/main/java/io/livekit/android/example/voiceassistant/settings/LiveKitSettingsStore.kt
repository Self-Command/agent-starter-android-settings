package io.livekit.android.example.voiceassistant.settings

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import org.json.JSONObject
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Authenticated encryption in noBackupFilesDir; the key never leaves Android Keystore. */
class LiveKitSettingsStore(context: Context) {
    private val file = AtomicFile(File(context.applicationContext.noBackupFilesDir, FILE_NAME))

    fun load(): LiveKitSettings = synchronized(lock) {
        if (!file.baseFile.exists()) return@synchronized LiveKitSettings()
        try {
            val bytes = file.readFully()
            require(bytes.size > 1 + IV_SIZE + 16 && bytes[0] == FORMAT_VERSION)
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes.copyOfRange(1, 1 + IV_SIZE)))
            val json = JSONObject(String(cipher.doFinal(bytes.copyOfRange(1 + IV_SIZE, bytes.size)), Charsets.UTF_8))
            val settings = LiveKitSettings(
                mode = ConnectionMode.valueOf(json.getString("mode")),
                tokenServerId = json.getString("tokenServerId"),
                serverUrl = json.getString("serverUrl"),
                token = json.getString("token"),
                tokenEndpoint = json.getString("tokenEndpoint"),
            )
            require(settings.validationErrors().isEmpty())
            settings
        } catch (_: Exception) {
            // Do not include the exception or decrypted input in diagnostics.
            throw IllegalStateException("无法读取已保存的 LiveKit 配置，请在设置中重新保存")
        }
    }

    fun save(settings: LiveKitSettings) = synchronized(lock) {
        val normalized = settings.normalized()
        require(normalized.validationErrors().isEmpty())
        val json = JSONObject().apply {
            put("mode", normalized.mode.name)
            put("tokenServerId", normalized.tokenServerId)
            put("serverUrl", normalized.serverUrl)
            put("token", normalized.token)
            put("tokenEndpoint", normalized.tokenEndpoint)
        }
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val bytes = byteArrayOf(FORMAT_VERSION) + cipher.iv + cipher.doFinal(json.toString().toByteArray(Charsets.UTF_8))
        val stream = file.startWrite()
        try {
            stream.write(bytes)
            file.finishWrite(stream)
        } catch (exception: Exception) {
            file.failWrite(stream)
            throw exception
        }
    }

    private fun key(): SecretKey {
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        val existing = keyStore.getKey(KEY_ALIAS, null) as? SecretKey
        if (existing != null) return existing
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(
                KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .build()
            )
        }.generateKey()
    }

    companion object {
        const val FILE_NAME = "livekit-settings.enc"
        private const val KEY_ALIAS = "livekit-connection-settings-v1"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
        private const val IV_SIZE = 12
        private const val FORMAT_VERSION: Byte = 1
        private val lock = Any()
    }
}
