package com.khanshoof.tv

import android.app.Activity
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.os.Build
import android.provider.Settings
import android.widget.Toast
import androidx.core.content.edit
import androidx.core.net.toUri

/**
 * Opening by itself when the TV or tablet boots, so a screen comes back after
 * a power cut. Android 9 and older let the boot receiver do it. Android 10+
 * block activity starts from the background unless the user has allowed
 * "Display over other apps", so the app asks for that once, after pairing.
 */
object AutoStart {
    private const val PREFS = "autostart"
    private const val KEY_ANSWERED = "answered"
    private const val PROMPT_TIMEOUT_MS = 60_000L

    private var shownThisLaunch = false

    fun canStartFromBoot(context: Context): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.Q || Settings.canDrawOverlays(context)

    fun maybeAsk(activity: Activity) {
        if (shownThisLaunch || activity.isFinishing || canStartFromBoot(activity)) return
        // Many TVs have no "Display over other apps" screen at all; there,
        // asking would only end in "not available", so don't ask.
        val settings = overlaySettingsIntent(activity) ?: return
        val prefs = activity.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        if (prefs.getBoolean(KEY_ANSWERED, false)) return
        shownThisLaunch = true

        val markAnswered = { prefs.edit { putBoolean(KEY_ANSWERED, true) } }
        val dialog = AlertDialog.Builder(activity, R.style.Theme_Khanshoof_Dialog)
            .setTitle(R.string.autostart_title)
            .setMessage(R.string.autostart_message)
            .setPositiveButton(R.string.autostart_allow) { _, _ ->
                markAnswered()
                openOverlaySettings(activity, settings)
            }
            .setNegativeButton(R.string.autostart_later) { _, _ -> markAnswered() }
            .create()
        dialog.show()
        dialog.getButton(AlertDialog.BUTTON_POSITIVE)?.requestFocus()

        // Nobody may be watching: a screen in a cafe must not sit behind a dialog
        // all day. Unanswered, it goes away and is asked again next launch.
        dialog.window?.decorView?.postDelayed({
            if (dialog.isShowing && !activity.isFinishing && !activity.isDestroyed) dialog.dismiss()
        }, PROMPT_TIMEOUT_MS)
    }

    /** The settings screen for this app's overlay permission, if this device has one. */
    private fun overlaySettingsIntent(activity: Activity): Intent? =
        listOf(
            Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, "package:${activity.packageName}".toUri()),
            Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION),
        ).firstOrNull { it.resolveActivity(activity.packageManager) != null }

    private fun openOverlaySettings(activity: Activity, intent: Intent) {
        try {
            activity.startActivity(intent)
        } catch (_: ActivityNotFoundException) {
            Toast.makeText(activity, R.string.autostart_unavailable, Toast.LENGTH_LONG).show()
        } catch (_: SecurityException) {
            Toast.makeText(activity, R.string.autostart_unavailable, Toast.LENGTH_LONG).show()
        }
    }
}
