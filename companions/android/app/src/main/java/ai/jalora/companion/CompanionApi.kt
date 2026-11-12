package ai.jalora.companion

import android.net.Uri
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

data class PairingLink(
    val origin: String,
    val requestId: String,
    val pairingCode: String,
)

object CompanionApi {
    fun parsePairingLink(value: String): PairingLink {
        val uri = Uri.parse(value.trim())
        require(
            uri.scheme == "https"
                || (uri.scheme == "http" && uri.host in setOf("localhost", "127.0.0.1", "::1")),
        ) {
            "Pairing links must use HTTPS except for localhost development."
        }
        require(uri.userInfo == null) {
            "Pairing links cannot contain user credentials."
        }
        val values = (uri.fragment ?: "").split("&")
            .mapNotNull { item ->
                val parts = item.split("=", limit = 2)
                if (parts.size == 2) Uri.decode(parts[0]) to Uri.decode(parts[1]) else null
            }
            .groupBy({ it.first }, { it.second })
        val requestId = values["pair_request_id"]?.singleOrNull()
        val pairingCode = values["pair_code"]?.singleOrNull()
        require(!requestId.isNullOrBlank() && !pairingCode.isNullOrBlank()) {
            "The Jalora pairing link is missing its one-time code."
        }
        return PairingLink(
            "${uri.scheme}://${uri.authority}",
            requestId,
            pairingCode,
        )
    }

    fun request(
        origin: String,
        path: String,
        method: String = "GET",
        token: String? = null,
        body: JSONObject? = null,
    ): JSONObject {
        val connection = URL(origin.trimEnd('/') + path).openConnection() as HttpURLConnection
        connection.requestMethod = method
        connection.connectTimeout = 10_000
        connection.readTimeout = 30_000
        connection.instanceFollowRedirects = false
        connection.setRequestProperty("Accept", "application/json")
        token?.let { connection.setRequestProperty("Authorization", "Bearer $it") }
        if (body != null) {
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
        }
        val status = connection.responseCode
        val stream = if (status in 200..299) connection.inputStream else connection.errorStream
        val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: "{}"
        connection.disconnect()
        val response = JSONObject(text)
        if (status !in 200..299) {
            val message = response.optJSONObject("error")?.optString("details")
                ?.takeIf { it.isNotBlank() } ?: "Jalora returned HTTP $status."
            throw CompanionHttpException(status, message)
        }
        return response
    }

    fun manifest(applications: JSONArray, scopes: Set<String>): JSONObject {
        val rows = JSONArray()
        for (index in 0 until applications.length()) {
            val app = applications.getJSONObject(index)
            val capabilities = JSONArray()
            if ("android.apps.launch" in scopes) capabilities.put("application.launch")
            if ("android.screen.read" in scopes) {
                capabilities.put("screen.accessibility.inspect")
            }
            if ("android.screen.control" in scopes) capabilities.put("screen.control")
            if (capabilities.length() == 0) continue
            rows.put(
                JSONObject()
                    .put("id", app.getString("id"))
                    .put("name", app.getString("name"))
                    .put("capabilities", capabilities),
            )
        }
        return JSONObject().put("schema_version", 1).put("applications", rows)
    }

    fun appId(packageName: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
            .digest(packageName.toByteArray(Charsets.UTF_8))
        return digest.take(10).joinToString("") {
            "%02x".format(it.toInt() and 0xff)
        }
    }
}

class CompanionHttpException(val status: Int, message: String) : Exception(message)
