package com.example.hello_world

import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.InetSocketAddress
import java.net.SocketTimeoutException
import java.net.Socket

class AlertReceiver(
    private val host: String,
    private val port: Int,
    private val onAlert: (level: String, message: String) -> Unit,
    private val onStatus: (connected: Boolean) -> Unit = {}
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val lock = Any()
    private var socket: Socket? = null
    private var job: Job? = null
    @Volatile private var running = false
    @Volatile private var reconnectImmediately = false

    fun start() {
        synchronized(lock) {
            if (job?.isActive == true) return
            running = true
            job = scope.launch { runLoop() }
        }
    }

    fun reconnect() {
        reconnectImmediately = true
        start()
        closeSocket()
    }

    fun stop() {
        running = false
        closeSocket()
        val oldJob = synchronized(lock) {
            job.also { job = null }
        }
        scope.launch {
            oldJob?.cancelAndJoin()
        }
    }

    fun destroy() {
        stop()
        scope.cancel()
    }

    private suspend fun runLoop() {
        while (running && scope.isActive) {
            try {
                Socket().also { candidate ->
                    candidate.tcpNoDelay = true
                    candidate.keepAlive = true
                    candidate.soTimeout = READ_TIMEOUT_MS
                    candidate.connect(InetSocketAddress(host, port), CONNECT_TIMEOUT_MS)
                    socket = candidate
                }
                Log.d("AlertReceiver", "已连接到告警服务")
                withContext(Dispatchers.Main) { onStatus(true) }

                val reader = socket!!.getInputStream().bufferedReader()

                while (running) {
                    val line = try {
                        reader.readLine()
                    } catch (_: SocketTimeoutException) {
                        Log.d("AlertReceiver", "告警连接空闲超时，准备重连")
                        break
                    } ?: break

                    if (line.isBlank()) continue

                    try {
                        val json = JSONObject(line)
                        val type = json.optString("type", "")
                        if (type == "alert") {
                            val level = json.optString("level", "warning")
                            val message = json.optString("message", "告警")
                            Log.d("AlertReceiver", "收到告警: $level")
                            withContext(Dispatchers.Main) {
                                onAlert(level, message)
                            }
                        }
                    } catch (e: Exception) {
                        Log.e("AlertReceiver", "解析失败: ${e.message}")
                    }
                }
            } catch (e: Exception) {
                if (running) {
                    Log.e("AlertReceiver", "连接失败: ${e.message}")
                }
            } finally {
                closeSocket()
                withContext(Dispatchers.Main) { onStatus(false) }
            }
            if (running && !reconnectImmediately) {
                delay(RECONNECT_DELAY_MS)
            }
            reconnectImmediately = false
        }
    }

    private fun closeSocket() {
        try { socket?.close() } catch (_: Exception) {}
        socket = null
    }

    /*
     * Android 把应用切到后台后，TCP 有时会处于半断开状态，阻塞读不会立刻失败。
     * 设置读超时可以定期重建连接，回到前台时再主动 close socket 来立即触发重连。
     */
    private companion object {
        const val CONNECT_TIMEOUT_MS = 5000
        const val READ_TIMEOUT_MS = 60000
        const val RECONNECT_DELAY_MS = 3000L
    }
}
