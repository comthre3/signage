package com.khanshoof.tv

import android.view.View
import android.webkit.WebView
import android.widget.FrameLayout
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * Launch smoke tests: the activity starts on a TV and on a portrait tablet,
 * opens the player, and BACK behaves as each device needs. No emulator here,
 * so Robolectric stands in for "it opens without crashing".
 */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [34])
class MainActivityTest {

    @Test
    @Config(qualifiers = "land-television")
    fun onATvThePlayerOpensAndOneBackGoesHome() {
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            scenario.onActivity { activity ->
                assertEquals(BuildConfig.PLAYER_URL, shadowOf(playerOf(activity)).lastLoadedUrl)
                assertEquals(View.GONE, activity.findViewById<View>(R.id.overlay).visibility)

                activity.onBackPressedDispatcher.onBackPressed()
                assertTrue("TV-DB: one BACK leaves the app", activity.isFinishing)
            }
        }
    }

    @Test
    @Config(qualifiers = "xlarge-port-finger")
    fun onATabletThePlayerOpensAndLeavingTakesTwoBacks() {
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            scenario.onActivity { activity ->
                assertEquals(BuildConfig.PLAYER_URL, shadowOf(playerOf(activity)).lastLoadedUrl)

                activity.onBackPressedDispatcher.onBackPressed()
                assertFalse("one accidental BACK keeps playing", activity.isFinishing)
                activity.onBackPressedDispatcher.onBackPressed()
                assertTrue(activity.isFinishing)
            }
        }
    }

    private fun playerOf(activity: MainActivity): WebView =
        activity.findViewById<FrameLayout>(R.id.web_container).getChildAt(0) as WebView
}
