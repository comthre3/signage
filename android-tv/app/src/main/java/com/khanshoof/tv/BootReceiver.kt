package com.khanshoof.tv

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

private const val ACTION_QUICKBOOT_POWERON = "android.intent.action.QUICKBOOT_POWERON"

/** Opens the player when the device finishes booting (see [AutoStart]). */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED && intent.action != ACTION_QUICKBOOT_POWERON) return
        // Android 10+ silently drops the start without the overlay permission.
        if (!AutoStart.canStartFromBoot(context)) return
        context.startActivity(
            Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        )
    }
}
