package com.example.hello_world

val BOARD_IP: String
    get() = BuildConfig.HARDWARE_BOARD_HOST.trim()

val API_PORT: Int
    get() = BuildConfig.HARDWARE_API_PORT

val CAR_PORT: Int
    get() = BuildConfig.HARDWARE_CAR_PORT

val ALERT_PORT: Int
    get() = BuildConfig.HARDWARE_ALERT_PORT

val MJPEG_URL: String
    get() = BuildConfig.HARDWARE_MJPEG_URL.trim()

val CAMERA_USER: String
    get() = BuildConfig.HARDWARE_CAMERA_USER

val CAMERA_PASS: String
    get() = BuildConfig.HARDWARE_CAMERA_PASS

val BROWSER_URL: String
    get() = BuildConfig.HARDWARE_BROWSER_URL.trim()

val SEND_MS: Long
    get() = BuildConfig.HARDWARE_SEND_MS.coerceAtLeast(10L)

fun hasApiConfig(): Boolean = BOARD_IP.isNotBlank() && API_PORT > 0

fun hasCarConfig(): Boolean = BOARD_IP.isNotBlank() && CAR_PORT > 0

fun hasAlertConfig(): Boolean = BOARD_IP.isNotBlank() && ALERT_PORT > 0

fun alertEndpointLabel(): String {
    return if (hasAlertConfig()) "告警已配置" else "告警未配置"
}
