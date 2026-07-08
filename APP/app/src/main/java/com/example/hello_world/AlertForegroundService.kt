package com.example.hello_world

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import kotlin.math.PI
import kotlin.math.sin

class AlertForegroundService : Service() {
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var alertReceiver: AlertReceiver
    private var alarmAudioTrack: AudioTrack? = null
    private var alarmToneThread: Thread? = null
    @Volatile private var alarmToneRunning = false
    private var vibrator: Vibrator? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var listenerWakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null
    private var previousAlarmVolume: Int? = null
    private var audioFocusRequest: AudioFocusRequest? = null
    private val audioFocusListener = AudioManager.OnAudioFocusChangeListener { }
    private var hasAudioFocus = false
    private var foregroundStarted = false

    override fun onCreate() {
        super.onCreate()
        createNotificationChannels()
        alertReceiver = AlertReceiver(
            host = BOARD_IP,
            port = ALERT_PORT,
            onAlert = ::handleAlert,
            onStatus = ::handleStatus
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action != ACTION_STOP_ALARM && !hasAlertConfig()) {
            AlertEvents.setStatus("告警服务未配置", connected = false)
            stopSelf()
            return START_NOT_STICKY
        }

        when (intent?.action) {
            ACTION_STOP_ALARM -> stopCriticalSignal()
            ACTION_RECONNECT -> {
                if (ensureForeground("前台已监听，正在重新连接")) {
                    acquireListenerLocks()
                    AlertEvents.setStatus("前台已监听，正在重新连接", connected = false)
                    alertReceiver.reconnect()
                }
            }
            else -> {
                if (ensureForeground("前台已监听，正在连接")) {
                    acquireListenerLocks()
                    AlertEvents.setStatus("前台已监听，正在连接", connected = false)
                    alertReceiver.start()
                }
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        stopCriticalSignal()
        alertReceiver.destroy()
        releaseListenerLocks()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun handleStatus(connected: Boolean) {
        val text = if (connected) "后台常驻监听，TCP 已连接" else "后台常驻监听，TCP 重连中"
        AlertEvents.setStatus(text, connected)
        if (!ensureForeground(text)) return
        acquireListenerLocks()
        notifySafely(SERVICE_NOTIFICATION_ID, buildServiceNotification(text))
    }

    private fun handleAlert(level: String, message: String) {
        AlertEvents.emit(level, message)
        startCriticalSignal()
        showCriticalNotification(level, message)
        launchAlertActivity(level, message)
    }

    private fun ensureForeground(text: String): Boolean {
        if (foregroundStarted) return true
        val notification = buildServiceNotification(text)
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(
                    SERVICE_NOTIFICATION_ID,
                    notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE
                )
            } else {
                startForeground(SERVICE_NOTIFICATION_ID, notification)
            }
            foregroundStarted = true
            AlertEvents.setStatus(text, connected = false)
            return true
        } catch (e: Exception) {
            Log.e(TAG, "进入前台服务失败: ${e.message}")
            AlertEvents.setStatus("告警前台服务启动失败: ${e.message}", connected = false)
            stopSelf()
            return false
        }
    }

    private fun buildServiceNotification(text: String): Notification {
        val openIntent = PendingIntent.getActivity(
            this,
            REQUEST_OPEN_APP,
            Intent(this, MainActivity::class.java).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val reconnectIntent = PendingIntent.getService(
            this,
            REQUEST_RECONNECT,
            Intent(this, AlertForegroundService::class.java).setAction(ACTION_RECONNECT),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        return NotificationCompat.Builder(this, SERVICE_CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle("小车告警监听")
            .setContentText(text)
            .setContentIntent(openIntent)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .addAction(android.R.drawable.stat_notify_sync, "重连", reconnectIntent)
            .build()
    }

    private fun showCriticalNotification(level: String, message: String) {
        val fullScreenIntent = PendingIntent.getActivity(
            this,
            REQUEST_ALERT_OPEN,
            Intent(this, MainActivity::class.java).apply {
                addFlags(
                    Intent.FLAG_ACTIVITY_NEW_TASK
                            or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT
                            or Intent.FLAG_ACTIVITY_SINGLE_TOP
                )
                putExtra("from_alert", true)
                putExtra("alert_level", level)
                putExtra("alert_message", message)
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val stopIntent = PendingIntent.getService(
            this,
            REQUEST_STOP_ALARM,
            Intent(this, AlertForegroundService::class.java).setAction(ACTION_STOP_ALARM),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val notification = NotificationCompat.Builder(this, ALERT_CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_alert)
            .setContentTitle(alertTitle(level))
            .setContentText(message)
            .setStyle(NotificationCompat.BigTextStyle().bigText(message))
            .setContentIntent(fullScreenIntent)
            .setFullScreenIntent(fullScreenIntent, true)
            .setDeleteIntent(stopIntent)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setOngoing(true)
            .setAutoCancel(false)
            .setSilent(true)
            .setVibrate(VIBRATION_PATTERN)
            .addAction(android.R.drawable.ic_lock_silent_mode, "停止告警", stopIntent)
            .build()

        notifySafely(ALERT_NOTIFICATION_ID, notification)
    }

    private fun startCriticalSignal() {
        stopCriticalSignal()
        acquireWakeLock()
        requestAlarmAudioFocus()
        maximizeAlarmVolume()
        startAlarmTone()
        startVibration()
        handler.postDelayed({ stopCriticalSignal() }, ALARM_AUTO_STOP_MS)
    }

    private fun stopCriticalSignal() {
        handler.removeCallbacksAndMessages(null)
        stopAlarmTone()
        stopVibration()
        restoreAlarmVolume()
        abandonAlarmAudioFocus()
        releaseWakeLock()
        cancelNotificationSafely(ALERT_NOTIFICATION_ID)
    }

    private fun acquireListenerLocks() {
        acquireListenerWakeLock()
        acquireWifiLock()
    }

    private fun releaseListenerLocks() {
        releaseWifiLock()
        releaseListenerWakeLock()
    }

    private fun acquireListenerWakeLock() {
        if (listenerWakeLock?.isHeld == true) return
        try {
            val powerManager = getSystemService(PowerManager::class.java)
            listenerWakeLock = powerManager.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK,
                "$packageName:HardwareAlertListener"
            ).apply {
                setReferenceCounted(false)
                acquire()
            }
        } catch (e: Exception) {
            Log.e(TAG, "获取告警监听唤醒锁失败: ${e.message}")
        }
    }

    private fun releaseListenerWakeLock() {
        try {
            if (listenerWakeLock?.isHeld == true) listenerWakeLock?.release()
        } catch (_: Exception) {
        }
        listenerWakeLock = null
    }

    private fun acquireWifiLock() {
        if (wifiLock?.isHeld == true) return
        try {
            val wifiManager = applicationContext.getSystemService(WifiManager::class.java)
            wifiLock = wifiManager.createWifiLock(
                WifiManager.WIFI_MODE_FULL_HIGH_PERF,
                "$packageName:HardwareAlertWifi"
            ).apply {
                setReferenceCounted(false)
                acquire()
            }
        } catch (e: Exception) {
            Log.e(TAG, "获取告警 Wi-Fi 保活锁失败: ${e.message}")
        }
    }

    private fun releaseWifiLock() {
        try {
            if (wifiLock?.isHeld == true) wifiLock?.release()
        } catch (_: Exception) {
        }
        wifiLock = null
    }

    private fun startAlarmTone() {
        stopAlarmTone()
        alarmToneRunning = true
        alarmToneThread = Thread({
            var track: AudioTrack? = null
            try {
                val minBufferBytes = AudioTrack.getMinBufferSize(
                    SIREN_SAMPLE_RATE,
                    AudioFormat.CHANNEL_OUT_MONO,
                    AudioFormat.ENCODING_PCM_16BIT
                )
                val bufferSamples = SIREN_BUFFER_SAMPLES
                val trackBufferBytes = maxOf(minBufferBytes, bufferSamples * BYTES_PER_PCM_16_SAMPLE)
                track = AudioTrack.Builder()
                    .setAudioAttributes(alarmAudioAttributes())
                    .setAudioFormat(
                        AudioFormat.Builder()
                            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                            .setSampleRate(SIREN_SAMPLE_RATE)
                            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                            .build()
                    )
                    .setBufferSizeInBytes(trackBufferBytes)
                    .setTransferMode(AudioTrack.MODE_STREAM)
                    .build()

                if (track.state != AudioTrack.STATE_INITIALIZED) {
                    throw IllegalStateException("AudioTrack 未初始化")
                }

                alarmAudioTrack = track
                track.play()
                writeSirenSamples(track, ShortArray(bufferSamples))
            } catch (e: Exception) {
                Log.e(TAG, "启动告警警报音失败: ${e.message}")
            } finally {
                try {
                    track?.pause()
                    track?.flush()
                } catch (_: Exception) {
                }
                try {
                    track?.release()
                } catch (_: Exception) {
                }
                if (alarmAudioTrack === track) {
                    alarmAudioTrack = null
                }
            }
        }, "HardwareAlertSiren").apply {
            isDaemon = true
            start()
        }
    }

    private fun writeSirenSamples(track: AudioTrack, buffer: ShortArray) {
        var phase = 0.0
        var sampleIndex = 0L

        while (alarmToneRunning && !Thread.currentThread().isInterrupted) {
            for (index in buffer.indices) {
                val cyclePosition = (sampleIndex % SIREN_CYCLE_SAMPLES).toDouble() / SIREN_CYCLE_SAMPLES
                val sweep = if (cyclePosition < 0.5) {
                    cyclePosition * 2.0
                } else {
                    (1.0 - cyclePosition) * 2.0
                }
                val frequency = SIREN_LOW_HZ + (SIREN_HIGH_HZ - SIREN_LOW_HZ) * sweep
                phase += TWO_PI * frequency / SIREN_SAMPLE_RATE
                if (phase > TWO_PI) phase -= TWO_PI

                val amplitude = SIREN_VOLUME * Short.MAX_VALUE
                buffer[index] = (sin(phase) * amplitude)
                    .toInt()
                    .coerceIn(Short.MIN_VALUE.toInt(), Short.MAX_VALUE.toInt())
                    .toShort()
                sampleIndex++
            }

            val written = track.write(buffer, 0, buffer.size)
            if (written < 0) {
                throw IllegalStateException("AudioTrack write failed: $written")
            }
        }
    }

    private fun stopAlarmTone() {
        alarmToneRunning = false
        alarmToneThread?.interrupt()
        alarmToneThread = null
        try {
            alarmAudioTrack?.pause()
            alarmAudioTrack?.flush()
        } catch (_: Exception) {
        }
        try {
            alarmAudioTrack?.release()
        } catch (_: Exception) {
        }
        alarmAudioTrack = null
    }

    private fun startVibration() {
        val v = currentVibrator()
        vibrator = v
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                v.vibrate(
                    VibrationEffect.createWaveform(VIBRATION_PATTERN, 0),
                    alarmAudioAttributes()
                )
            } else {
                @Suppress("DEPRECATION")
                v.vibrate(VIBRATION_PATTERN, 0)
            }
        } catch (e: Exception) {
            Log.e(TAG, "启动告警震动失败: ${e.message}")
        }
    }

    private fun stopVibration() {
        try {
            vibrator?.cancel()
        } catch (_: Exception) {
        }
        vibrator = null
    }

    private fun requestAlarmAudioFocus() {
        val audioManager = audioManager()
        try {
            hasAudioFocus = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_EXCLUSIVE)
                    .setAudioAttributes(alarmAudioAttributes())
                    .setOnAudioFocusChangeListener(audioFocusListener)
                    .build()
                audioFocusRequest = request
                audioManager.requestAudioFocus(request) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
            } else {
                @Suppress("DEPRECATION")
                audioManager.requestAudioFocus(
                    audioFocusListener,
                    AudioManager.STREAM_ALARM,
                    AudioManager.AUDIOFOCUS_GAIN_TRANSIENT
                ) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
            }
        } catch (e: Exception) {
            Log.e(TAG, "请求告警音频焦点失败: ${e.message}")
        }
    }

    private fun abandonAlarmAudioFocus() {
        if (!hasAudioFocus) return
        val audioManager = audioManager()
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                audioFocusRequest?.let { audioManager.abandonAudioFocusRequest(it) }
            } else {
                @Suppress("DEPRECATION")
                audioManager.abandonAudioFocus(audioFocusListener)
            }
        } catch (_: Exception) {
        }
        audioFocusRequest = null
        hasAudioFocus = false
    }

    private fun maximizeAlarmVolume() {
        val audioManager = audioManager()
        try {
            previousAlarmVolume = audioManager.getStreamVolume(AudioManager.STREAM_ALARM)
            audioManager.setStreamVolume(
                AudioManager.STREAM_ALARM,
                audioManager.getStreamMaxVolume(AudioManager.STREAM_ALARM),
                0
            )
        } catch (e: Exception) {
            Log.e(TAG, "提升告警音量失败: ${e.message}")
        }
    }

    private fun restoreAlarmVolume() {
        val previous = previousAlarmVolume ?: return
        try {
            audioManager().setStreamVolume(AudioManager.STREAM_ALARM, previous, 0)
        } catch (_: Exception) {
        }
        previousAlarmVolume = null
    }

    private fun acquireWakeLock() {
        try {
            val powerManager = getSystemService(PowerManager::class.java)
            wakeLock = powerManager.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK,
                "$packageName:HardwareAlert"
            ).apply {
                setReferenceCounted(false)
                acquire(ALARM_AUTO_STOP_MS + 5000L)
            }
        } catch (e: Exception) {
            Log.e(TAG, "获取告警唤醒锁失败: ${e.message}")
        }
    }

    private fun releaseWakeLock() {
        try {
            if (wakeLock?.isHeld == true) wakeLock?.release()
        } catch (_: Exception) {
        }
        wakeLock = null
    }

    private fun launchAlertActivity(level: String, message: String) {
        try {
            startActivity(Intent(this, MainActivity::class.java).apply {
                addFlags(
                    Intent.FLAG_ACTIVITY_NEW_TASK
                            or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT
                            or Intent.FLAG_ACTIVITY_SINGLE_TOP
                )
                putExtra("from_alert", true)
                putExtra("alert_level", level)
                putExtra("alert_message", message)
            })
        } catch (e: Exception) {
            Log.e(TAG, "启动告警界面失败: ${e.message}")
        }
    }

    private fun createNotificationChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return

        val manager = getSystemService(NotificationManager::class.java)
        val serviceChannel = NotificationChannel(
            SERVICE_CHANNEL_ID,
            "告警监听服务",
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = "保持硬件告警 TCP 长连接"
            setShowBadge(false)
        }

        val alertChannel = NotificationChannel(
            ALERT_CHANNEL_ID,
            "强制硬件告警",
            NotificationManager.IMPORTANCE_HIGH
        ).apply {
            description = "收到机器人告警时响铃、震动、全屏提示"
            enableVibration(true)
            enableLights(true)
            setSound(null, null)
            setBypassDnd(true)
            lockscreenVisibility = Notification.VISIBILITY_PRIVATE
        }

        manager.createNotificationChannel(serviceChannel)
        manager.createNotificationChannel(alertChannel)
    }

    private fun notifySafely(id: Int, notification: Notification) {
        try {
            NotificationManagerCompat.from(this).notify(id, notification)
        } catch (e: SecurityException) {
            Log.e(TAG, "发送通知失败，可能缺少通知权限: ${e.message}")
        }
    }

    private fun cancelNotificationSafely(id: Int) {
        try {
            NotificationManagerCompat.from(this).cancel(id)
        } catch (_: Exception) {
        }
    }

    private fun currentVibrator(): Vibrator {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            getSystemService(VibratorManager::class.java).defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            getSystemService(VIBRATOR_SERVICE) as Vibrator
        }
    }

    private fun audioManager(): AudioManager = getSystemService(AudioManager::class.java)

    private fun alarmAudioAttributes(): AudioAttributes {
        return AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_ALARM)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
            .build()
    }

    private fun alertTitle(level: String): String {
        return when (level) {
            "danger" -> "紧急告警"
            "warning" -> "警告"
            else -> "提示"
        }
    }

    companion object {
        private const val TAG = "AlertForegroundService"
        private const val SERVICE_CHANNEL_ID = "hardware_alert_service"
        private const val ALERT_CHANNEL_ID = "hardware_critical_siren_alert_v1"
        private const val SERVICE_NOTIFICATION_ID = 100
        private const val ALERT_NOTIFICATION_ID = 101
        private const val REQUEST_OPEN_APP = 201
        private const val REQUEST_RECONNECT = 202
        private const val REQUEST_ALERT_OPEN = 203
        private const val REQUEST_STOP_ALARM = 204
        private const val ALARM_AUTO_STOP_MS = 120000L
        private const val SIREN_SAMPLE_RATE = 22050
        private const val SIREN_BUFFER_SAMPLES = 2048
        private const val SIREN_CYCLE_MS = 1100
        private const val SIREN_LOW_HZ = 720.0
        private const val SIREN_HIGH_HZ = 1550.0
        private const val SIREN_VOLUME = 0.72
        private const val BYTES_PER_PCM_16_SAMPLE = 2
        private const val TWO_PI = 2.0 * PI
        private const val SIREN_CYCLE_SAMPLES = SIREN_SAMPLE_RATE * SIREN_CYCLE_MS / 1000
        private val VIBRATION_PATTERN = longArrayOf(0, 700, 250, 700, 250, 1200)

        const val ACTION_STOP_ALARM = "com.example.hello_world.action.STOP_ALARM"
        const val ACTION_RECONNECT = "com.example.hello_world.action.RECONNECT_ALERT"

        fun start(context: Context): Boolean {
            val intent = Intent(context, AlertForegroundService::class.java)
            return try {
                AlertEvents.setStatus("正在启动告警前台服务", connected = false)
                ContextCompat.startForegroundService(context, intent)
                true
            } catch (e: Exception) {
                Log.e(TAG, "启动告警前台服务失败: ${e.message}")
                AlertEvents.setStatus("告警前台服务启动失败: ${e.message}", connected = false)
                false
            }
        }

        fun reconnect(context: Context): Boolean {
            val intent = Intent(context, AlertForegroundService::class.java).setAction(ACTION_RECONNECT)
            return try {
                AlertEvents.setStatus("正在请求告警 TCP 重连", connected = false)
                ContextCompat.startForegroundService(context, intent)
                true
            } catch (e: Exception) {
                Log.e(TAG, "重连告警前台服务失败: ${e.message}")
                AlertEvents.setStatus("告警重连请求失败: ${e.message}", connected = false)
                false
            }
        }

        fun stopAlarm(context: Context) {
            try {
                context.startService(
                    Intent(context, AlertForegroundService::class.java).setAction(ACTION_STOP_ALARM)
                )
            } catch (e: Exception) {
                Log.e(TAG, "停止告警信号失败: ${e.message}")
            }
        }
    }
}
