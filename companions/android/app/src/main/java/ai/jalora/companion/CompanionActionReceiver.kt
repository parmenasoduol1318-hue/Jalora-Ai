package ai.jalora.companion

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

class CompanionActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val serviceIntent = Intent(context, DeviceCompanionService::class.java)
        when (intent.action) {
            ACTION_PAUSE -> serviceIntent.setAction(DeviceCompanionService.ACTION_PAUSE)
            ACTION_RESUME -> serviceIntent.setAction(DeviceCompanionService.ACTION_RESUME)
            ACTION_STOP -> serviceIntent.setAction(DeviceCompanionService.ACTION_STOP)
            else -> return
        }
        context.startService(serviceIntent)
    }

    companion object {
        const val ACTION_PAUSE = "ai.jalora.companion.PAUSE"
        const val ACTION_RESUME = "ai.jalora.companion.RESUME"
        const val ACTION_STOP = "ai.jalora.companion.STOP"
    }
}
