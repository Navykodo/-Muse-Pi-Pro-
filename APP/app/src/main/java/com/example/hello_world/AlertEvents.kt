package com.example.hello_world

import java.util.concurrent.CopyOnWriteArrayList

object AlertEvents {
    data class Alert(
        val level: String,
        val message: String,
        val timestampMillis: Long = System.currentTimeMillis()
    )

    data class Status(
        val text: String,
        val connected: Boolean,
        val timestampMillis: Long = System.currentTimeMillis()
    )

    private val listeners = CopyOnWriteArrayList<(Alert) -> Unit>()
    private val statusListeners = CopyOnWriteArrayList<(Status) -> Unit>()

    @Volatile
    private var latestAlert: Alert? = null

    @Volatile
    private var latestStatus: Status = Status("告警服务未启动", connected = false)

    fun addListener(listener: (Alert) -> Unit): Alert? {
        listeners.add(listener)
        return latestAlert
    }

    fun removeListener(listener: (Alert) -> Unit) {
        listeners.remove(listener)
    }

    fun addStatusListener(listener: (Status) -> Unit): Status {
        statusListeners.add(listener)
        return latestStatus
    }

    fun removeStatusListener(listener: (Status) -> Unit) {
        statusListeners.remove(listener)
    }

    fun emit(level: String, message: String) {
        val alert = Alert(level = level, message = message)
        latestAlert = alert
        listeners.forEach { it(alert) }
    }

    fun clear() {
        latestAlert = null
    }

    fun setStatus(text: String, connected: Boolean) {
        val status = Status(text = text, connected = connected)
        latestStatus = status
        statusListeners.forEach { it(status) }
    }
}
