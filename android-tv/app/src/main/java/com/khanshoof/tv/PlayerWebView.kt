package com.khanshoof.tv

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.os.Build
import android.util.Log
import android.view.View
import android.webkit.ConsoleMessage
import android.webkit.GeolocationPermissions
import android.webkit.PermissionRequest
import android.webkit.RenderProcessGoneDetail
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.annotation.RequiresApi
import androidx.core.content.ContextCompat
import androidx.core.graphics.createBitmap
import androidx.webkit.ServiceWorkerControllerCompat
import androidx.webkit.WebViewFeature

/** What the activity needs to hear from the player's WebView. */
interface PlayerEvents {
    /** A new page load began (also the player reloading itself after an update). */
    fun onPageStarted()

    /** The player page finished loading without a main-frame error. */
    fun onPageReady()

    /** The player page itself could not be loaded (network, DNS, 5xx). */
    fun onMainFrameFailed()

    /** The renderer crashed or was killed; this WebView is dead and must be replaced. */
    fun onRendererGone()
}

private const val TAG = "KhanshoofTV"

/**
 * A WebView set up to run the Khanshoof player as a kiosk: storage for the
 * screen token, autoplaying muted video, service workers for the offline media
 * cache, and nothing that lets the page wander off the player origin.
 */
@SuppressLint("SetJavaScriptEnabled")
fun createPlayerWebView(context: Context, playerOrigin: String, events: PlayerEvents): WebView {
    configureServiceWorkers()

    return WebView(context).apply {
        setBackgroundColor(ContextCompat.getColor(context, R.color.cream))
        isFocusable = true
        isFocusableInTouchMode = true

        // Touch screens in public: a long press must not select text or pop a
        // copy/share bar, and a swipe must not drag the page or flash a glow.
        isLongClickable = false
        setOnLongClickListener { true }
        isHapticFeedbackEnabled = false
        overScrollMode = View.OVER_SCROLL_NEVER

        settings.apply {
            javaScriptEnabled = true
            // The player keeps its screen token and last content in localStorage.
            domStorageEnabled = true
            // Signage plays muted video unattended; nobody is there to tap play.
            mediaPlaybackRequiresUserGesture = false
            cacheMode = WebSettings.LOAD_DEFAULT
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            allowFileAccess = false
            allowContentAccess = false
            // The player sizes itself to the screen; a TV's accessibility font
            // scale would push its layout off the edges.
            textZoom = 100
            // Nor may a pinch zoom the menu.
            setSupportZoom(false)
            builtInZoomControls = false
            displayZoomControls = false
            // Lets the backend and player tell app TVs from browser TVs.
            userAgentString = "$userAgentString KhanshoofTV/${BuildConfig.VERSION_NAME}"
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            setRendererPriorityPolicy(WebView.RENDERER_PRIORITY_IMPORTANT, false)
        }

        webViewClient = PlayerClient(playerOrigin, events)
        webChromeClient = PlayerChromeClient()
    }
}

private fun configureServiceWorkers() {
    // Service workers run in WebView by default; these settings only make
    // their requests follow the same rules as the page's. Optional, so a
    // WebView that refuses them must not stop the player from starting.
    if (!WebViewFeature.isFeatureSupported(WebViewFeature.SERVICE_WORKER_BASIC_USAGE)) return
    val settings = try {
        ServiceWorkerControllerCompat.getInstance().serviceWorkerWebSettings
    } catch (e: RuntimeException) {
        Log.w(TAG, "Service worker settings unavailable", e)
        return
    } catch (e: LinkageError) {
        // The controller is made in a static initializer: a broken WebView
        // fails it with ExceptionInInitializerError, and every later call
        // (a rebuilt WebView) with NoClassDefFoundError.
        Log.w(TAG, "Service worker settings unavailable", e)
        return
    }
    if (WebViewFeature.isFeatureSupported(WebViewFeature.SERVICE_WORKER_CACHE_MODE)) {
        settings.cacheMode = WebSettings.LOAD_DEFAULT
    }
    if (WebViewFeature.isFeatureSupported(WebViewFeature.SERVICE_WORKER_FILE_ACCESS)) {
        settings.allowFileAccess = false
    }
    if (WebViewFeature.isFeatureSupported(WebViewFeature.SERVICE_WORKER_CONTENT_ACCESS)) {
        settings.allowContentAccess = false
    }
}

private class PlayerClient(
    private val playerOrigin: String,
    private val events: PlayerEvents,
) : WebViewClient() {

    private var mainFrameFailed = false

    override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
        // Websites shown as media play inside iframes, which this never sees.
        // A top-level navigation away from the player would strand the TV on
        // some other page, so it is ignored.
        if (!request.isForMainFrame) return false
        val stay = Policy.isSameOrigin(request.url.toString(), playerOrigin)
        if (!stay) Log.w(TAG, "Ignored navigation away from the player: ${request.url}")
        return !stay
    }

    override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
        mainFrameFailed = false
        events.onPageStarted()
    }

    override fun onPageFinished(view: WebView, url: String?) {
        // Also called after an error page; only a clean load counts.
        if (!mainFrameFailed && url != null && Policy.isSameOrigin(url, playerOrigin)) {
            events.onPageReady()
        }
    }

    override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
        if (!request.isForMainFrame) return   // a broken image or iframe is the player's business
        Log.w(TAG, "Player failed to load: ${error.errorCode}")
        mainFrameFailed = true
        events.onMainFrameFailed()
    }

    override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, errorResponse: WebResourceResponse) {
        // A 5xx (or Cloudflare's 52x while the tunnel is down) for the player
        // page itself; once the player has loaded, its service worker serves it.
        if (!request.isForMainFrame || errorResponse.statusCode < 500) return
        Log.w(TAG, "Player page answered ${errorResponse.statusCode}")
        mainFrameFailed = true
        events.onMainFrameFailed()
    }

    @RequiresApi(Build.VERSION_CODES.O)
    override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
        // Without this the whole app dies with the renderer (an out-of-memory
        // kill after weeks of video, say). Returning true keeps the app alive;
        // the activity replaces this WebView.
        Log.w(TAG, "Renderer gone (crashed=${detail.didCrash()})")
        events.onRendererGone()
        return true
    }
}

private class PlayerChromeClient : WebChromeClient() {

    // WebView draws a grey play-button poster over a <video> until its first
    // frame. On a TV in a cafe that flashes between every clip.
    override fun getDefaultVideoPoster(): Bitmap = createBitmap(1, 1)

    override fun onPermissionRequest(request: PermissionRequest) = request.deny()

    override fun onGeolocationPermissionsShowPrompt(origin: String?, callback: GeolocationPermissions.Callback) =
        callback.invoke(origin, false, false)

    override fun onConsoleMessage(message: ConsoleMessage): Boolean {
        if (BuildConfig.DEBUG) Log.d(TAG, "console: ${message.message()} (${message.sourceId()}:${message.lineNumber()})")
        return true
    }
}
