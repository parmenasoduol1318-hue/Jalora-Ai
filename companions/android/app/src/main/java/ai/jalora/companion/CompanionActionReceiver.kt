package ai.jalora.companion

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

class CompanionActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val serviceIntent = Intent(context, DeviceCompanionService::class.java)
        when (intent.action) {
            ACTION_APPROVE,
            ACTION_REJECT -> {
                serviceIntent
                    .setAction(DeviceCompanionService.ACTION_DECISION)
                    .putExtra(
                        DeviceCompanionService.EXTRA_COMMAND_ID,
                        intent.getStringExtra(DeviceCompanionService.EXTRA_COMMAND_ID),
                    )
                    .putExtra(
                        DeviceCompanionService.EXTRA_NONCE,
                        intent.getStringExtra(DeviceCompanionService.EXTRA_NONCE),
                    )
                    .putExtra(
                        DeviceCompanionService.EXTRA_APPROVE,
                        intent.action == ACTION_APPROVE,
                    )
            }
            ACTION_PAUSE -> serviceIntent.setAction(DeviceCompanionService.ACTION_PAUSE)
            ACTION_RESUME -> serviceIntent.setAction(DeviceCompanionService.ACTION_RESUME)
            ACTION_STOP -> serviceIntent.setAction(DeviceCompanionService.ACTION_STOP)
            else -> return
        }
        context.startService(serviceIntent)
    }

    companion object {
        const val ACTION_APPROVE = "ai.jalora.companion.APPROVE"
        const val ACTION_REJECT = "ai.jalora.companion.REJECT"
        const val ACTION_PAUSE = "ai.jalora.companion.PAUSE"
        const val ACTION_RESUME = "ai.jalora.companion.RESUME"
        const val ACTION_STOP = "ai.jalora.companion.STOP"
    }
}
