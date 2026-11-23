package com.jalora.companion

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build

class CompanionActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val serviceIntent = Intent(context, DeviceCompanionService::class.java)
        when (intent.action) {
            ACTION_PAUSE -> serviceIntent.setAction(DeviceCompanionService.ACTION_PAUSE)
            ACTION_RESUME -> serviceIntent.setAction(DeviceCompanionService.ACTION_RESUME)
            ACTION_STOP -> serviceIntent.setAction(DeviceCompanionService.ACTION_STOP)
            else -> return
        }
        if (intent.action == ACTION_RESUME && Build.VERSION.SDK_INT >= 26) {
            context.startForegroundService(serviceIntent)
        } else {
            context.startService(serviceIntent)
        }
    }

    companion object {
        const val ACTION_PAUSE = "com.jalora.companion.PAUSE"
        const val ACTION_RESUME = "com.jalora.companion.RESUME"
        const val ACTION_STOP = "com.jalora.companion.STOP"
    }
}
