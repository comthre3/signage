package com.khanshoof.tv

import java.net.URI
import java.net.URISyntaxException

/** The app's decisions that don't need Android, kept here so they are unit-tested. */
object Policy {

    /** Waits between attempts to load the player while it can't be reached. */
    private val RETRY_DELAYS_MS = longArrayOf(5_000, 10_000, 20_000, 30_000, 60_000)

    /** The player's JavaScript needs Chrome 80 (optional chaining). */
    const val MIN_WEBVIEW_MAJOR = 80

    fun retryDelayMs(failures: Int): Long =
        RETRY_DELAYS_MS[failures.coerceIn(0, RETRY_DELAYS_MS.lastIndex)]

    /** "scheme://host[:port]" of an http(s) URL, lower-cased, default port dropped; null otherwise. */
    fun originOf(url: String): String? {
        val uri = try {
            URI(url)
        } catch (_: URISyntaxException) {
            return null
        }
        val scheme = uri.scheme?.lowercase() ?: return null
        if (scheme != "http" && scheme != "https") return null
        val host = uri.host?.lowercase() ?: return null
        val defaultPort = if (scheme == "https") 443 else 80
        return if (uri.port == -1 || uri.port == defaultPort) "$scheme://$host" else "$scheme://$host:${uri.port}"
    }

    fun isSameOrigin(url: String, origin: String): Boolean = originOf(url) == origin

    /** 119 for "119.0.6045.193"; null when the version name isn't a version. */
    fun webViewMajor(versionName: String?): Int? =
        versionName?.substringBefore('.')?.toIntOrNull()

    /**
     * Only a WebView known to be too old is refused. An unreadable version
     * gets the benefit of the doubt: blocking a TV that would have worked is
     * worse than trying.
     */
    fun isWebViewTooOld(versionName: String?): Boolean =
        webViewMajor(versionName)?.let { it < MIN_WEBVIEW_MAJOR } ?: false
}
