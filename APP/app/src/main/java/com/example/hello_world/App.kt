package com.example.hello_world

import android.app.Application

class App : Application() {
    fun startAlertService() {
        AlertForegroundService.start(this)
    }
}
