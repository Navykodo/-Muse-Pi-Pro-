package com.example.hello_world

import io.ktor.client.HttpClient
import io.ktor.client.engine.cio.CIO
import io.ktor.client.plugins.HttpTimeout
import io.ktor.client.request.get
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.contentType
import io.ktor.http.isSuccess
import org.json.JSONObject
import java.io.IOException

object HardwareApiClient {
    private val client = HttpClient(CIO) {
        expectSuccess = false
        install(HttpTimeout) {
            connectTimeoutMillis = CONNECT_TIMEOUT_MS
            requestTimeoutMillis = REQUEST_TIMEOUT_MS
            socketTimeoutMillis = REQUEST_TIMEOUT_MS
        }
    }

    suspend fun health(): String {
        return formatJson(get("/health"))
    }

    suspend fun tools(): String {
        return formatJson(get("/tools"))
    }

    suspend fun callTool(tool: String, args: Map<String, Any?> = emptyMap()): String {
        val body = JSONObject().apply {
            put("tool", tool)
            put("args", JSONObject(args))
        }.toString()

        return formatJson(postTool(body))
    }

    private suspend fun get(path: String): String {
        val response = client.get(apiUrl(path))
        val text = response.bodyAsText()
        if (!response.status.isSuccess()) {
            throw IOException("HTTP ${response.status.value}: $text")
        }
        return text
    }

    private suspend fun postTool(body: String): String {
        val response = client.post(apiUrl("/tool")) {
            contentType(ContentType.Application.Json)
            setBody(body)
        }
        val text = response.bodyAsText()
        if (!response.status.isSuccess()) {
            throw IOException("HTTP ${response.status.value}: $text")
        }
        return text
    }

    private fun apiUrl(path: String): String {
        check(hasApiConfig()) { "硬件 HTTP API 未配置" }
        return "http://$BOARD_IP:$API_PORT$path"
    }

    private fun formatJson(text: String): String {
        return JSONObject(text).toString(2)
    }

    private const val CONNECT_TIMEOUT_MS = 5000L
    private const val REQUEST_TIMEOUT_MS = 30000L
}
