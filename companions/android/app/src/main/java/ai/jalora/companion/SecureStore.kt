package ai.jalora.companion

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONObject
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

object SecureStore {
    private const val alias = "jalora-device-companion-v1"
    private const val preferences = "jalora_companion_secure"
    private const val valueKey = "encrypted_state"

    @Synchronized
    fun read(context: Context): JSONObject? {
        val encoded = context.getSharedPreferences(preferences, Context.MODE_PRIVATE)
            .getString(valueKey, null) ?: return null
        val payload = Base64.decode(encoded, Base64.NO_WRAP)
        require(payload.size > 12) { "Saved companion data is invalid." }
        val iv = payload.copyOfRange(0, 12)
        val ciphertext = payload.copyOfRange(12, payload.size)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, iv))
        return JSONObject(String(cipher.doFinal(ciphertext), Charsets.UTF_8))
    }

    @Synchronized
    fun write(context: Context, state: JSONObject) {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val encrypted = cipher.doFinal(state.toString().toByteArray(Charsets.UTF_8))
        val payload = cipher.iv + encrypted
        val saved = context.getSharedPreferences(preferences, Context.MODE_PRIVATE)
            .edit()
            .putString(valueKey, Base64.encodeToString(payload, Base64.NO_WRAP))
            .commit()
        check(saved) { "The encrypted companion state could not be saved." }
    }

    @Synchronized
    fun clear(context: Context) {
        context.getSharedPreferences(preferences, Context.MODE_PRIVATE)
            .edit()
            .remove(valueKey)
            .commit()
    }

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(
                alias,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build(),
        )
        return generator.generateKey()
    }
}
