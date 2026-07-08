package com.example.hello_world

import androidx.compose.runtime.DisposableEffect
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.content.pm.ActivityInfo
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.view.WindowManager
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.OutputStreamWriter
import java.net.Socket

enum class Dir(val key: String, val label: String) {
    F("w", "前"), B("s", "后"), L("a", "左"), R("d", "右"), N("", "停")
}

enum class Screen { MAIN, REMOTE }

class TcpSender(private val host: String, private val port: Int) {
    private var s: Socket? = null
    private var w: OutputStreamWriter? = null
    var ok = false; private set

    suspend fun connect() = withContext(Dispatchers.IO) {
        try { s = Socket(host, port).also { it.tcpNoDelay = true }; w = OutputStreamWriter(s!!.outputStream); ok = true }
        catch (_: Exception) { ok = false }; ok
    }

    suspend fun send(k: String) = withContext(Dispatchers.IO) {
        try { if (ok) w?.let { it.write(k); it.flush() } } catch (_: Exception) { ok = false }
    }

    fun close() { try { w?.close(); s?.close() } catch (_: Exception) {}; ok = false }
}

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 1001)
        }
        handleAlertIntent(intent)
        window.addFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN)
        enableEdgeToEdge()
        setContent { HardwareControlApp() }
        requestCriticalAlertSettingsIfNeeded()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleAlertIntent(intent)
    }

    private fun handleAlertIntent(intent: Intent?) {
        if (intent?.getBooleanExtra("from_alert", false) == true) {
            window.addFlags(
                WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
                        or WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED
                        or WindowManager.LayoutParams.FLAG_DISMISS_KEYGUARD
            )
            val message = intent.getStringExtra("alert_message")
            if (!message.isNullOrBlank()) {
                AlertEvents.emit(
                    level = intent.getStringExtra("alert_level") ?: "warning",
                    message = message
                )
            }
        }
    }

    private fun requestCriticalAlertSettingsIfNeeded() {
        val prefs = getSharedPreferences("critical_alert_settings", Context.MODE_PRIVATE)
        val now = System.currentTimeMillis()
        val settingsIntent = nextCriticalAlertSettingsIntent() ?: return
        val promptKey = "$KEY_LAST_SETTINGS_PROMPT_MS:${settingsIntent.action}:${settingsIntent.dataString.orEmpty()}"

        if (now - prefs.getLong(promptKey, 0L) < SETTINGS_PROMPT_INTERVAL_MS) {
            return
        }

        prefs.edit().putLong(promptKey, now).apply()
        try {
            startActivity(settingsIntent)
        } catch (_: Exception) {
        }
    }

    private fun nextCriticalAlertSettingsIntent(): Intent? {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            val notificationManager = getSystemService(NotificationManager::class.java)
            if (!notificationManager.isNotificationPolicyAccessGranted) {
                return Intent(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS)
            }
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            val notificationManager = getSystemService(NotificationManager::class.java)
            if (!notificationManager.canUseFullScreenIntent()) {
                return Intent(Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT).apply {
                    data = Uri.parse("package:$packageName")
                }
            }
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            val powerManager = getSystemService(PowerManager::class.java)
            if (!powerManager.isIgnoringBatteryOptimizations(packageName)) {
                return Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
                    data = Uri.parse("package:$packageName")
                }
            }
        }

        return null
    }

    private companion object {
        const val KEY_LAST_SETTINGS_PROMPT_MS = "last_settings_prompt_ms"
        const val SETTINGS_PROMPT_INTERVAL_MS = 24 * 60 * 60 * 1000L
    }
}

@Composable
fun HardwareControlApp() {
    val context = LocalContext.current
    val activity = context as? ComponentActivity
    var screen by remember { mutableStateOf(Screen.MAIN) }
    var isLandscape by remember { mutableStateOf(true) }

    var alertMsg by remember { mutableStateOf<String?>(null) }
    var alertLv by remember { mutableStateOf("warning") }
    var alertStatus by remember { mutableStateOf("告警服务未启动") }
    var alertConnected by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        delay(500L)
        if (hasAlertConfig()) {
            AlertForegroundService.start(context.applicationContext)
        } else {
            AlertEvents.setStatus("告警服务未配置", connected = false)
        }
        activity?.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE
    }

    DisposableEffect(Unit) {
        val listener: (AlertEvents.Alert) -> Unit = { alert ->
            alertMsg = alert.message
            alertLv = alert.level
        }
        AlertEvents.addListener(listener)?.let(listener)
        onDispose { AlertEvents.removeListener(listener) }
    }

    DisposableEffect(Unit) {
        val listener: (AlertEvents.Status) -> Unit = { status ->
            alertStatus = status.text
            alertConnected = status.connected
        }
        listener(AlertEvents.addStatusListener(listener))
        onDispose { AlertEvents.removeStatusListener(listener) }
    }

    fun dismissAlert() {
        alertMsg = null
        AlertEvents.clear()
        AlertForegroundService.stopAlarm(context.applicationContext)
    }

    Box(modifier = Modifier.fillMaxSize()) {
        when (screen) {
            Screen.MAIN -> MainPanel(
                isLandscape = isLandscape,
                onToggleOrientation = {
                    isLandscape = !isLandscape
                    activity?.requestedOrientation = if (isLandscape)
                        ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE
                    else ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
                },
                onSwitchToRemote = { screen = Screen.REMOTE }
            )
            Screen.REMOTE -> RemoteScreen(
                onBack = { screen = Screen.MAIN }
            )
        }

        AlertConnectionTopBar(
            alertStatus = alertStatus,
            alertConnected = alertConnected,
            modifier = Modifier.align(Alignment.TopCenter)
        )
    }

    if (alertMsg != null) {
        AlertDialog(
            onDismissRequest = { dismissAlert() },
            title = { Text(when (alertLv) { "danger" -> "紧急告警"; "warning" -> "警告"; else -> "提示" }) },
            text = { Text(alertMsg!!) },
            confirmButton = { TextButton(onClick = { dismissAlert() }) { Text("确定") } },
            containerColor = when (alertLv) { "danger" -> Color(0xFFFFCDD2); "warning" -> Color(0xFFFFF9C4); else -> Color(0xFFE3F2FD) }
        )
    }
}

@Composable
fun MainPanel(
    isLandscape: Boolean,
    onToggleOrientation: () -> Unit,
    onSwitchToRemote: () -> Unit
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var resultText by remember { mutableStateOf("点击按钮调用 API，结果显示在这里") }

    fun runApi(request: suspend () -> String) {
        scope.launch {
            withContext(Dispatchers.IO) {
                try {
                    val formatted = request()
                    withContext(Dispatchers.Main) {
                        resultText = formatted
                    }
                } catch (e: Exception) {
                    withContext(Dispatchers.Main) {
                        resultText = "失败: ${e.message}"
                    }
                }
            }
        }
    }

    fun callApi(tool: String, args: Map<String, Any?> = emptyMap()) {
        runApi { HardwareApiClient.callTool(tool, args) }
    }

    fun openBrowser() {
        try {
            val url = BROWSER_URL
            if (url.isBlank()) {
                resultText = "浏览器地址未配置"
                return
            }
            val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url)).apply {
                addCategory(Intent.CATEGORY_BROWSABLE)
            }
            context.startActivity(intent)
            resultText = "已打开配置网页"
        } catch (e: Exception) {
            resultText = "打开浏览器失败: ${e.message}"
        }
    }

    Column(modifier = Modifier.fillMaxSize().background(Color(0xFF121212)).padding(top = 42.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text("硬件中控", color = Color.White, fontSize = 20.sp, fontWeight = FontWeight.Bold)
            }
            Spacer(modifier = Modifier.width(12.dp))
            Row {
                Button(
                    onClick = onToggleOrientation,
                    modifier = Modifier.size(36.dp),
                    shape = CircleShape,
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF333333)),
                    contentPadding = androidx.compose.foundation.layout.PaddingValues(0.dp)
                ) { Text(if (isLandscape) "⬐" else "⬓", fontSize = 14.sp, color = Color.White) }
                Spacer(modifier = Modifier.width(8.dp))
                Button(
                    onClick = onSwitchToRemote,
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF4CAF50))
                ) { Text("视频模式", color = Color.White, fontSize = 14.sp) }
            }
        }

        Row(modifier = Modifier.fillMaxSize()) {
            LazyColumn(
                modifier = Modifier.weight(1f).padding(horizontal = 8.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp)
            ) {
                item { SectionTitle("基础") }
                item { ApiButton("健康检查") { runApi { HardwareApiClient.health() } } }
                item { ApiButton("查看 tools") { runApi { HardwareApiClient.tools() } } }
                item { ApiButton("打开配置网页") { openBrowser() } }

                item { SectionTitle("小车普通运动") }
                item {
                    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        ApiButton("状态", Modifier.weight(1f)) { callApi("get_car_status") }
                        ApiButton("停止", Modifier.weight(1f)) { callApi("car_stop") }
                    }
                }
                item {
                    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        ApiButton("前进", Modifier.weight(1f)) { callApi("car_forward") }
                        ApiButton("后退", Modifier.weight(1f)) { callApi("car_backward") }
                    }
                }
                item {
                    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        ApiButton("左移", Modifier.weight(1f)) { callApi("car_left") }
                        ApiButton("右移", Modifier.weight(1f)) { callApi("car_right") }
                    }
                }
                item {
                    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        ApiButton("顺时针转", Modifier.weight(1f)) { callApi("car_turn_clockwise", mapOf("angle_degrees" to 90)) }
                        ApiButton("逆时针转", Modifier.weight(1f)) { callApi("car_turn_counterclockwise", mapOf("angle_degrees" to 90)) }
                    }
                }

                item { SectionTitle("导航") }
                item { ApiButton("导航状态") { callApi("car_nav_status") } }
                item { ApiButton("列出地点") { callApi("car_nav_places") } }
                item { ApiButton("导航等待") { callApi("car_nav_wait") } }
                item { ApiButton("取消导航") { callApi("car_nav_stop") } }

                item { SectionTitle("C6 唤醒") }
                item { ApiButton("最近唤醒方位") { callApi("get_latest_c6_wake_direction") } }

                item { SectionTitle("传感器") }
                item { ApiButton("DHT11 温湿度") { callApi("get_dht11_latest") } }
                item { ApiButton("DHT11 统计") { callApi("get_dht11_summary", mapOf("limit" to 10)) } }

                item { SectionTitle("语音") }
                item {
                    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        ApiButton("播报测试", Modifier.weight(1f)) { callApi("speak_text", mapOf("text" to "你好，我是机器人")) }
                        ApiButton("停止播报", Modifier.weight(1f)) { callApi("stop_speaking") }
                    }
                }
                item { ApiButton("播报状态") { callApi("is_speaking") } }

                item { SectionTitle("视觉") }
                item { ApiButton("拍照描述") { callApi("camera_describe") } }

                item { SectionTitle("音乐") }
                item {
                    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        ApiButton("搜索播放", Modifier.weight(1f)) { callApi("music_play_search", mapOf("query" to "示例音乐")) }
                        ApiButton("停止音乐", Modifier.weight(1f)) { callApi("music_stop") }
                    }
                }
                item { ApiButton("音乐状态") { callApi("music_status") } }

                item { SectionTitle("哨兵") }
                item {
                    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        ApiButton("开启", Modifier.weight(1f)) { callApi("sentry_set_mode", mapOf("enabled" to true, "mode" to "watch")) }
                        ApiButton("关闭", Modifier.weight(1f)) { callApi("sentry_set_mode", mapOf("enabled" to false)) }
                    }
                }
                item { ApiButton("哨兵状态") { callApi("sentry_get_status", mapOf("recent_events" to 5)) } }
                item { ApiButton("哨兵观察") { callApi("sentry_observe_once") } }

                item { SectionTitle("工具") }
                item { ApiButton("等待 3 秒") { callApi("wait_seconds", mapOf("seconds" to 3)) } }

                item { Spacer(modifier = Modifier.height(80.dp)) }
            }

            Card(
                modifier = Modifier.weight(1f).padding(8.dp),
                colors = CardDefaults.cardColors(containerColor = Color(0xFF1A1A2E))
            ) {
                Text(
                    resultText,
                    color = Color(0xFFCCCCCC),
                    fontSize = 11.sp,
                    modifier = Modifier.padding(12.dp),
                    lineHeight = 16.sp
                )
            }
        }
    }
}

@Composable
fun AlertConnectionTopBar(
    alertStatus: String,
    alertConnected: Boolean,
    modifier: Modifier = Modifier
) {
    val signalColor = if (alertConnected) Color(0xFF69F0AE) else Color(0xFFFFD54F)
    val backgroundColor = if (alertConnected) Color(0xFF0F2A1E) else Color(0xFF31270B)
    val title = if (alertConnected) "告警 TCP 已连接" else "告警 TCP 未连接"

    Row(
        modifier = modifier
            .fillMaxWidth()
            .height(42.dp)
            .background(backgroundColor)
            .padding(horizontal = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        Box(
            modifier = Modifier
                .size(9.dp)
                .clip(CircleShape)
                .background(signalColor)
        )
        Text(title, color = signalColor, fontSize = 13.sp, fontWeight = FontWeight.Bold)
        Text(
            alertEndpointLabel(),
            color = Color(0xFFE0E0E0),
            fontSize = 11.sp,
            fontWeight = FontWeight.Bold
        )
        Text(alertStatus, color = Color(0xFFE0E0E0), fontSize = 11.sp, maxLines = 1)
    }
}

@Composable
fun SectionTitle(title: String) {
    Text(title, color = Color(0xFF888888), fontSize = 12.sp, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 8.dp, bottom = 2.dp))
}

@Composable
fun ApiButton(label: String, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Button(
        onClick = onClick,
        modifier = modifier.height(36.dp),
        shape = RoundedCornerShape(6.dp),
        colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF333333)),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 8.dp, vertical = 0.dp)
    ) { Text(label, fontSize = 11.sp, color = Color.White, textAlign = TextAlign.Center) }
}

@Composable
fun RemoteScreen(onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    val tcp = remember { TcpSender(BOARD_IP, CAR_PORT) }
    val carConfigured = hasCarConfig()
    val videoConfigured = MJPEG_URL.isNotBlank()
    var dir by remember { mutableStateOf(Dir.N) }
    var job by remember { mutableStateOf<Job?>(null) }

    LaunchedEffect(carConfigured) {
        if (carConfigured) {
            while (isActive) {
                if (!tcp.ok) tcp.connect()
                delay(3000)
            }
        }
    }

    fun onDir(d: Dir) {
        if (!carConfigured) return
        dir = d; job?.cancel(); job = null
        if (d == Dir.N) { job = scope.launch { repeat(5) { tcp.send("\n"); delay(100L) } }; return }
        job = scope.launch { while (isActive) { tcp.send(d.key); delay(SEND_MS) } }
    }

    Box(modifier = Modifier.fillMaxSize().background(Color.Black)) {
        AndroidView(
            factory = { ctx ->
                WebView(ctx).apply {
                    webViewClient = object : WebViewClient() {
                        override fun onReceivedSslError(
                            view: WebView?,
                            handler: android.webkit.SslErrorHandler?,
                            error: android.net.http.SslError?
                        ) {
                            handler?.proceed()
                        }

                        override fun onReceivedHttpAuthRequest(
                            view: WebView?,
                            handler: android.webkit.HttpAuthHandler?,
                            host: String?,
                            realm: String?
                        ) {
                            if (CAMERA_USER.isNotBlank() || CAMERA_PASS.isNotBlank()) {
                                handler?.proceed(CAMERA_USER, CAMERA_PASS)
                            } else {
                                handler?.cancel()
                            }
                        }
                    }
                    settings.apply {
                        javaScriptEnabled = false
                        loadWithOverviewMode = true
                        useWideViewPort = true
                        setRenderPriority(android.webkit.WebSettings.RenderPriority.HIGH)
                        cacheMode = android.webkit.WebSettings.LOAD_NO_CACHE
                        mixedContentMode = android.webkit.WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
                    }
                    setLayerType(android.view.View.LAYER_TYPE_HARDWARE, null)
                    setBackgroundColor(0xFF000000.toInt())
                }
            },
            update = { webView ->
                if (videoConfigured) {
                    webView.loadUrl(MJPEG_URL)
                }
            },
            modifier = Modifier.fillMaxSize()
        )

        if (!videoConfigured) {
            Text(
                "视频流未配置",
                color = Color.White.copy(alpha = 0.85f),
                fontSize = 14.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.align(Alignment.Center)
            )
        }

        Button(
            onClick = onBack,
            modifier = Modifier.align(Alignment.TopStart).padding(start = 8.dp, top = 50.dp).size(36.dp),
            shape = CircleShape,
            colors = ButtonDefaults.buttonColors(containerColor = Color.Black.copy(alpha = 0.5f)),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(0.dp)
        ) { Text("←", color = Color.White, fontSize = 16.sp) }

        Box(modifier = Modifier.align(Alignment.BottomStart).padding(20.dp)) { Joystick { onDir(it) } }
        if (dir != Dir.N) Text(dir.label, color = Color.White.copy(alpha = 0.8f), fontSize = 14.sp, fontWeight = FontWeight.Bold, modifier = Modifier.align(Alignment.BottomStart).padding(start = 20.dp, bottom = 160.dp))
    }

    DisposableEffect(Unit) { onDispose { tcp.close() } }
}

@Composable
fun Joystick(onDir: (Dir) -> Unit) {
    val sr = 56.dp; val kr = 26.dp
    var ox by remember { mutableStateOf(0f) }; var oy by remember { mutableStateOf(0f) }
    var drag by remember { mutableStateOf(false) }

    Box(modifier = Modifier.size(sr * 2).clip(CircleShape).background(Color.Black.copy(alpha = 0.4f))
        .pointerInput(Unit) { detectDragGestures(
            onDragStart = { drag = true },
            onDragEnd = { drag = false; ox = 0f; oy = 0f; onDir(Dir.N) },
            onDragCancel = { drag = false; ox = 0f; oy = 0f; onDir(Dir.N) },
            onDrag = { c, a -> c.consume()
                val m = with(density) { sr.toPx() }
                var nx = ox + a.x / m; var ny = oy + a.y / m
                val d = kotlin.math.sqrt(nx*nx + ny*ny)
                if (d > 1f) { nx /= d; ny /= d }
                ox = nx; oy = ny
                onDir(when {
                    kotlin.math.abs(ny) > kotlin.math.abs(nx) -> if (ny < -0.3f) Dir.F else if (ny > 0.3f) Dir.B else Dir.N
                    else -> if (nx < -0.3f) Dir.L else if (nx > 0.3f) Dir.R else Dir.N
                })
            }
        ) }, contentAlignment = Alignment.Center
    ) {
        Canvas(modifier = Modifier.fillMaxSize()) {
            val c = Color.White.copy(alpha = 0.6f)
            drawCircle(c, radius = size.minDimension / 2, style = Stroke(width = 2.dp.toPx()))
            drawLine(c.copy(alpha = 0.35f), Offset(size.width / 2, 0f), Offset(size.width / 2, size.height))
            drawLine(c.copy(alpha = 0.35f), Offset(0f, size.height / 2), Offset(size.width, size.height / 2))
        }
        val gap = sr - 12.dp
        Text("W", color = Color.White.copy(alpha = 0.4f), fontSize = 9.sp, modifier = Modifier.offset(y = -gap))
        Text("S", color = Color.White.copy(alpha = 0.4f), fontSize = 9.sp, modifier = Modifier.offset(y = gap))
        Text("A", color = Color.White.copy(alpha = 0.4f), fontSize = 9.sp, modifier = Modifier.offset(x = -gap))
        Text("D", color = Color.White.copy(alpha = 0.4f), fontSize = 9.sp, modifier = Modifier.offset(x = gap))
        Box(modifier = Modifier.size(kr * 2).offset(x = (sr - kr) * ox, y = (sr - kr) * oy).clip(CircleShape)
            .background(if (drag) Color(0xFFFF7043).copy(alpha = 0.85f) else Color(0xFF4FC3F7).copy(alpha = 0.75f)))
    }
}
