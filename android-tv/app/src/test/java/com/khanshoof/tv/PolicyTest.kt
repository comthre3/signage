package com.khanshoof.tv

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PolicyTest {

    @Test
    fun retryDelayBacksOffThenStaysAtTheCeiling() {
        assertEquals(5_000L, Policy.retryDelayMs(0))
        assertEquals(10_000L, Policy.retryDelayMs(1))
        assertEquals(20_000L, Policy.retryDelayMs(2))
        assertEquals(30_000L, Policy.retryDelayMs(3))
        assertEquals(60_000L, Policy.retryDelayMs(4))
        assertEquals(60_000L, Policy.retryDelayMs(500))
        assertEquals(5_000L, Policy.retryDelayMs(-1))
    }

    @Test
    fun originKeepsSchemeHostAndExplicitPort() {
        assertEquals("https://play.khanshoof.com", Policy.originOf("https://play.khanshoof.com/"))
        assertEquals("https://play.khanshoof.com", Policy.originOf("https://PLAY.khanshoof.com/x?y=1#z"))
        assertEquals("http://192.168.1.20:3001", Policy.originOf("http://192.168.1.20:3001/?api_base=x"))
        assertNull(Policy.originOf("not a url"))
        assertNull(Policy.originOf("intent://scan/#Intent;end"))
    }

    @Test
    fun onlyThePlayerOriginStaysInTheApp() {
        val player = "https://play.khanshoof.com"
        assertTrue(Policy.isSameOrigin("https://play.khanshoof.com/?token=abc", player))
        assertFalse(Policy.isSameOrigin("https://play.khanshoof.com.evil.example/", player))
        assertFalse(Policy.isSameOrigin("http://play.khanshoof.com/", player))
        assertFalse(Policy.isSameOrigin("https://app.khanshoof.com/pair", player))
        assertFalse(Policy.isSameOrigin("javascript:alert(1)", player))
    }

    @Test
    fun webViewMajorVersionIsReadFromVersionName() {
        assertEquals(119, Policy.webViewMajor("119.0.6045.193"))
        assertEquals(66, Policy.webViewMajor("66"))
        assertNull(Policy.webViewMajor(null))
        assertNull(Policy.webViewMajor("unknown"))
    }

    @Test
    fun onlyAKnownOldWebViewIsRefused() {
        assertTrue(Policy.isWebViewTooOld("61.0.3163.98"))    // Android 8.1's stock WebView
        assertTrue(Policy.isWebViewTooOld("65.0.3325.109"))
        assertFalse(Policy.isWebViewTooOld("66.0.3359.158"))  // Android 9's
        assertFalse(Policy.isWebViewTooOld("74.0.3729.186"))  // Android 10's
        assertFalse(Policy.isWebViewTooOld("138.0.7204.63"))
        // Unknown versions get the benefit of the doubt: refusing to run on a
        // TV that would have worked is worse than trying.
        assertFalse(Policy.isWebViewTooOld(null))
        assertFalse(Policy.isWebViewTooOld("dev-build"))
    }
}
