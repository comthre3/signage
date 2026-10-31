package com.khanshoof.tv

import android.content.Intent
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.View
import android.view.WindowManager
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.webkit.WebView
import android.widget.FrameLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.annotation.StringRes
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.lifecycle.Lifecycle
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature

private const val TAG = "KhanshoofTV"

/** Debug builds only: `adb shell am start -n com.khanshoof.tv.debug/com.khanshoof.tv.MainActivity -e url http://192.168.1.20:3001/` */
private const val EXTRA_URL = "url"

private const val WATCHDOG_INTERVAL_MS = 60_000L
private const val WATCHDOG_TIMEOUT_MS = 30_000L
private const val RENDERER_RESTART_DELAY_MS = 3_000L
private const val BACK_EXIT_WINDOW_MS = 2_000L

// The watchdog's probe. Any answer proves the page's JavaScript is alive; the
// value says whether the player has paired (it keeps its token under this key
// in player/player.js), which is when the auto-start question is worth asking.
private const val PROBE_JS =
    "(function(){try{return !!localStorage.getItem('screen_token')}catch(e){return false}})()"

/**
 * The whole app: one full-screen WebView running the Khanshoof player, kept
 * alive. If the player can't load it retries (sooner when the network comes
 * back); if its renderer dies or hangs it gets a new one.
 */
class MainActivity : ComponentActivity(), PlayerEvents {

    private lateinit var webContainer: FrameLayout
    private lateinit var overlay: View
    private lateinit var overlayTitle: TextView
    private lateinit var overlayMessage: TextView

    private val handler = Handler(Looper.getMainLooper())
    private var webView: WebView? = null
    private var playerUrl = BuildConfig.PLAYER_URL
    private var playerOrigin = ""

    private var failures = 0          // consecutive failed loads, for the backoff
    private var loadFailed = false    // the current load failed and a retry is scheduled
    private var pageReady = false     // the player page is up
    private var pageGeneration = 0    // bumped by every page load, so a stale probe is ignored
    private var lastBackPress = 0L
    private var networkCallback: ConnectivityManager.NetworkCallback? = null

    private val retry = Runnable { load() }
    private val rebuild = Runnable { if (buildWebView()) load() }
    private val watchdog = object : Runnable {
        override fun run() {
            probe()
            handler.postDelayed(this, WATCHDOG_INTERVAL_MS)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(R.layout.activity_main)
        webContainer = findViewById(R.id.web_container)
        overlay = findViewById(R.id.overlay)
        overlayTitle = findViewById(R.id.overlay_title)
        overlayMessage = findViewById(R.id.overlay_message)

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() = onBack()
        })

        playerUrl = requestedPlayerUrl(intent) ?: BuildConfig.PLAYER_URL
        playerOrigin = checkNotNull(Policy.originOf(playerUrl)) { "Bad player URL: $playerUrl" }

        if (Policy.isWebViewTooOld(WebViewCompat.getCurrentWebViewPackage(this)?.versionName)) {
            showOverlay(R.string.webview_title, R.string.webview_message)
            return
        }
        if (buildWebView()) load()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        // Opened again (from the launcher, or by the boot receiver) while it is
        // already running: keep playing. Only a debug build's url extra changes anything.
        val url = requestedPlayerUrl(intent) ?: return
        if (url != playerUrl) {
            setIntent(intent)
            recreate()
        }
    }

    override fun onStart() {
        super.onStart()
        watchNetwork()
        handler.postDelayed(watchdog, WATCHDOG_INTERVAL_MS)
    }

    override fun onResume() {
        super.onResume()
        hideSystemBars()
        webView?.onResume()
    }

    override fun onPause() {
        webView?.onPause()
        super.onPause()
    }

    override fun onStop() {
        handler.removeCallbacks(watchdog)
        unwatchNetwork()
        super.onStop()
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        webView?.let {
            webContainer.removeView(it)
            it.destroy()
        }
        webView = null
        super.onDestroy()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemBars()   // a system dialog or toast brings the bars back
    }

    // ── The player WebView ──────────────────────────────────────────────

    /** False when this TV has no working WebView at all (disabled, or mid-update). */
    private fun buildWebView(): Boolean {
        val view = try {
            createPlayerWebView(this, playerOrigin, this)
        } catch (e: RuntimeException) {
            Log.e(TAG, "No usable WebView", e)
            showOverlay(R.string.webview_title, R.string.webview_message)
            return false
        }
        webContainer.addView(view, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
        view.requestFocus()   // D-pad keys go to the page
        webView = view
        return true
    }

    private fun load() {
        handler.removeCallbacks(retry)
        loadFailed = false
        webView?.loadUrl(playerUrl)
    }

    /** Throws the WebView away and builds a fresh one after a short pause. */
    private fun replaceWebView() {
        pageReady = false
        webView?.let {
            webContainer.removeView(it)
            it.destroy()
        }
        webView = null
        showOverlay(R.string.restarting_title, R.string.restarting_message)
        handler.removeCallbacks(rebuild)
        handler.postDelayed(rebuild, RENDERER_RESTART_DELAY_MS)
    }

    override fun onPageStarted() {
        pageReady = false
        pageGeneration++
    }

    override fun onPageReady() {
        failures = 0
        pageReady = true
        hideOverlay()
    }

    override fun onMainFrameFailed() {
        if (loadFailed) return   // one retry per failed load, however many errors it reports
        loadFailed = true
        pageReady = false
        showOverlay(R.string.offline_title, R.string.offline_message)
        handler.postDelayed(retry, Policy.retryDelayMs(failures++))
    }

    override fun onRendererGone() = replaceWebView()

    /** The hang watchdog: a page whose JavaScript stops answering gets a new renderer. */
    private fun probe() {
        val view = webView ?: return
        if (!pageReady) return
        val generation = pageGeneration
        var answered = false
        view.evaluateJavascript(PROBE_JS) { result ->
            answered = true
            if (result == "true") AutoStart.maybeAsk(this)
        }
        handler.postDelayed({
            val stillThatPage = pageReady && generation == pageGeneration && webView === view
            if (!answered && stillThatPage && lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) {
                Log.w(TAG, "Player stopped answering; restarting its renderer")
                restartRenderer(view)
            }
        }, WATCHDOG_TIMEOUT_MS)
    }

    private fun restartRenderer(view: WebView) {
        // Killing the hung renderer is the sure fix: the WebView then reports it
        // gone and is replaced. Older WebViews can't, so replace it directly.
        if (WebViewFeature.isFeatureSupported(WebViewFeature.GET_WEB_VIEW_RENDERER) &&
            WebViewFeature.isFeatureSupported(WebViewFeature.WEB_VIEW_RENDERER_TERMINATE)
        ) {
            if (WebViewCompat.getWebViewRenderProcess(view)?.terminate() == true) return
        }
        replaceWebView()
    }

    // ── Network ─────────────────────────────────────────────────────────

    private fun watchNetwork() {
        val connectivity = getSystemService(ConnectivityManager::class.java) ?: return
        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                // Back online: retry now rather than waiting out the backoff.
                handler.post {
                    if (loadFailed) {
                        failures = 0
                        load()
                    }
                }
            }
        }
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                connectivity.registerDefaultNetworkCallback(callback)
            } else {
                val request = NetworkRequest.Builder()
                    .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                    .build()
                connectivity.registerNetworkCallback(request, callback)
            }
            networkCallback = callback
        } catch (e: RuntimeException) {
            // Some TV boxes refuse; the backoff alone still recovers.
            Log.w(TAG, "Can't watch the network", e)
        }
    }

    private fun unwatchNetwork() {
        val callback = networkCallback ?: return
        networkCallback = null
        try {
            getSystemService(ConnectivityManager::class.java)?.unregisterNetworkCallback(callback)
        } catch (e: RuntimeException) {
            Log.w(TAG, "Can't stop watching the network", e)
        }
    }

    // ── Screen ──────────────────────────────────────────────────────────

    private fun hideSystemBars() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowInsetsControllerCompat(window, window.decorView).apply {
            hide(WindowInsetsCompat.Type.systemBars())
            systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    }

    private fun showOverlay(@StringRes title: Int, @StringRes message: Int) {
        overlayTitle.setText(title)
        overlayMessage.setText(message)
        overlay.visibility = View.VISIBLE
    }

    private fun hideOverlay() {
        overlay.visibility = View.GONE
    }

    /** BACK twice to leave: a remote bumped once must not take the menu off the wall. */
    private fun onBack() {
        val now = SystemClock.elapsedRealtime()
        if (now - lastBackPress <= BACK_EXIT_WINDOW_MS) {
            finish()
            return
        }
        lastBackPress = now
        Toast.makeText(this, R.string.press_back_again, Toast.LENGTH_SHORT).show()
    }

    private fun requestedPlayerUrl(intent: Intent?): String? {
        if (!BuildConfig.DEBUG) return null
        val url = intent?.getStringExtra(EXTRA_URL) ?: return null
        return url.takeIf { Policy.originOf(it) != null }
    }
}
