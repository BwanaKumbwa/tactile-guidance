package com.example.hans

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.media.Image
import android.opengl.GLES20
import android.opengl.GLSurfaceView
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.util.Base64
import android.util.Log
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.constraintlayout.widget.ConstraintLayout
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.example.hans.ai.LocalHansBrain
import com.example.hans.haptics.HapticArbitrator
import com.example.hans.ml.BoundingBoxOverlay
import com.example.hans.ml.CameraImageConverter
import com.example.hans.ml.CvPipelineSingleton
import com.example.hans.ml.DepthUtils
import com.example.hans.ml.DualYoloPipeline
import com.example.hans.ml.tracker.SortTracker
import com.example.hans.ml.tracker.TrackedDetection
import com.google.android.material.bottomnavigation.BottomNavigationView
import com.google.ar.core.ArCoreApk
import com.google.ar.core.Config
import com.google.ar.core.Frame
import com.google.ar.core.Session
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.*
import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import javax.microedition.khronos.egl.EGLConfig
import javax.microedition.khronos.opengles.GL10

class MainActivity : AppCompatActivity(), TextToSpeech.OnInitListener, GLSurfaceView.Renderer {

    // =================================================================
    // CONFIGURATION
    // =================================================================
    private val SERVER_IP = BuildConfig.SERVER_IP
    private val WEBSOCKET_URL = "ws://$SERVER_IP:8000/ws/video"
    private val WAKE_WORD = BuildConfig.WAKE_WORD

    // UI Components
    private lateinit var surfaceView: GLSurfaceView
    private lateinit var overlayView: BoundingBoxOverlay
    private lateinit var tvStatus: TextView
    private lateinit var tvAiResponse: TextView
    private lateinit var btnPtt: ConstraintLayout
    private lateinit var bottomNav: BottomNavigationView
    private lateinit var bottomColorBar: View

    private val PTT_COLOR_IDLE = "#CC2196F3"
    private val PTT_COLOR_ACTIVE = "#CCCC0000"

    // Responsive Tap & PTT Timing State
    @Volatile private var isPttRecording = false
    private var touchDownTime = 0L
    private val MIN_HOLD_DURATION_MS = 400L

    // ARCore Session & OpenGL State
    private var arSession: Session? = null
    private val cameraRenderer = CameraRenderer()
    @Volatile private var isTextureSet = false
    @Volatile private var isActivityPaused = false

    // =================================================================
    // ON-DEVICE CV & TRACKING
    // =================================================================
    private var dualPipeline: DualYoloPipeline? = null
    private val tracker: SortTracker get() = CvPipelineSingleton.tracker
    private val cvExecutor: ExecutorService = Executors.newSingleThreadExecutor()
    private val isInferring = AtomicBoolean(false)

    // Performance Metrics
    private var lastFpsTimestamp = 0L
    private var frameCounter = 0
    private var currentFps = 0

    // On-Device AI Brain (Direct LLM Client)
    private var localBrain: LocalHansBrain? = null

    // Local Haptic Guidance Arbitrator
    private lateinit var hapticArbitrator: HapticArbitrator

    // Networking
    private val client = OkHttpClient()
    private var webSocket: WebSocket? = null

    // Speech & Audio
    private var speechRecognizer: SpeechRecognizer? = null
    private var speechIntent: Intent? = null
    private lateinit var tts: TextToSpeech

    // BLE Singletons
    private lateinit var braceletManager: BleManager
    private lateinit var beltManager: BleManager

    // Preferences
    private val Intensity_Prefs = "FullIntensityPrefs"
    private val Pattern_Prefs = "PatternPrefs"

    // =================================================================
    // LIFECYCLE
    // =================================================================
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        // Bind UI
        surfaceView = findViewById(R.id.surfaceView)
        tvStatus = findViewById(R.id.tvStatus)
        tvAiResponse = findViewById(R.id.tvAiResponse)
        btnPtt = findViewById(R.id.rootLayout)
        bottomNav = findViewById(R.id.bottomNavigation)

        setupOverlayView()
        addBottomColorBar()

        bottomNav.selectedItemId = R.id.menu_camera
        bottomNav.post {
            updateBottomNavBackground(R.id.menu_camera)
            updateBottomNavBarColor(false)
        }

        // Clean Tab Switching: Keep MainActivity alive in background to prevent camera disconnects
        bottomNav.setOnItemSelectedListener { item ->
            when (item.itemId) {
                R.id.menu_home -> {
                    stopAllHaptics()
                    val intent = Intent(this, BluetoothActivity::class.java).apply {
                        flags = Intent.FLAG_ACTIVITY_REORDER_TO_FRONT
                    }
                    startActivity(intent)
                    true
                }
                R.id.menu_camera -> true
                R.id.menu_setting -> {
                    stopAllHaptics()
                    val intent = Intent(this, SettingsActivity::class.java).apply {
                        flags = Intent.FLAG_ACTIVITY_REORDER_TO_FRONT
                    }
                    startActivity(intent)
                    true
                }
                else -> false
            }
        }

        // OpenGL GLSurfaceView configuration
        surfaceView.preserveEGLContextOnPause = true
        surfaceView.setEGLContextClientVersion(2)
        surfaceView.setEGLConfigChooser(8, 8, 8, 8, 16, 0)
        surfaceView.setRenderer(this)
        surfaceView.renderMode = GLSurfaceView.RENDERMODE_CONTINUOUSLY

        // BLE Singletons
        braceletManager = BleManagerSingleton.getBraceletManager(this)
        beltManager = BleManagerSingleton.getBeltManager(this)

        // Local Haptic Arbitrator
        hapticArbitrator = HapticArbitrator(braceletManager, beltManager)

        // Warm up CV models via Singleton
        initLocalCvEngine()

        // Initialize On-Device LLM Brain
        initLocalBrain()

        // Permissions
        if (allPermissionsGranted()) {
            initSystem()
        } else {
            requestPermissionsLauncher.launch(REQUIRED_PERMISSIONS)
        }

        tts = TextToSpeech(this, this)

        val rootLayout = findViewById<ConstraintLayout>(R.id.rootLayout)
        rootLayout.setBackgroundColor(Color.BLACK)

        // Force 16:9 camera layout
        surfaceView.post {
            val screenWidth = surfaceView.width
            val targetHeight = (screenWidth * 16) / 9

            val surfaceParams = surfaceView.layoutParams
            surfaceParams.height = targetHeight
            surfaceView.layoutParams = surfaceParams

            val overlayParams = overlayView.layoutParams
            overlayParams.height = targetHeight
            overlayView.layoutParams = overlayParams
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        bottomNav.selectedItemId = R.id.menu_camera
        updateBottomNavBackground(R.id.menu_camera)
    }

    private fun setupOverlayView() {
        val existingOverlay = findViewById<View>(R.id.overlayView)
        val parent = existingOverlay.parent as ViewGroup
        val index = parent.indexOfChild(existingOverlay)
        val layoutParams = existingOverlay.layoutParams

        parent.removeView(existingOverlay)

        overlayView = BoundingBoxOverlay(this).apply {
            id = R.id.overlayView
            this.layoutParams = layoutParams
            elevation = 50f
        }
        parent.addView(overlayView, index)
    }

    private fun initLocalCvEngine() {
        cvExecutor.execute {
            try {
                dualPipeline = CvPipelineSingleton.getPipeline(this)
                runOnUiThread {
                    tvStatus.text = "Local CV Ready [${dualPipeline?.activeHardware}]"
                }
            } catch (e: Exception) {
                Log.e("HANS", "Failed to obtain local CV pipeline", e)
            }
        }
    }

    private fun initLocalBrain() {
        localBrain = LocalHansBrain(
            context = this,
            apiUrl = BuildConfig.API_URL,
            apiKey = BuildConfig.API_KEY,
            model = BuildConfig.LLM_MODEL,
            hardwareStatusProvider = {
                Pair(braceletManager.isConnected(), beltManager.isConnected())
            }
        )
    }

    private fun initSystem() {
        startWebSocket()
        setupSpeech()
        setupPttButton()
    }

    // =================================================================
    // LOCAL CV + ARCORE DEPTH + LOCAL HAPTIC PIPELINE (Non-Blocking)
    // =================================================================
    private fun triggerLocalInferenceIfIdle(frame: Frame) {
        if (isActivityPaused) return
        val pipeline = dualPipeline ?: return

        if (!isInferring.compareAndSet(false, true)) {
            return
        }

        var rgbImage: Image? = null
        var depthImage: Image? = null

        try {
            rgbImage = frame.acquireCameraImage()
            try {
                depthImage = frame.acquireDepthImage16Bits()
            } catch (e: Exception) {
                // Depth initializing
            }

            val rgbImgRef = rgbImage
            val depthImgRef = depthImage

            cvExecutor.execute {
                try {
                    if (isActivityPaused) return@execute

                    val portraitBitmap = CameraImageConverter.yuvToPortraitBitmap(rgbImgRef, 640)
                    val bmpW = portraitBitmap.width
                    val bmpH = portraitBitmap.height

                    // 1. Dual-Model Inference
                    val result = pipeline.detect(portraitBitmap)

                    // 2. Kalman Tracking
                    val trackedDetections = tracker.update(result.mergedDetections)

                    // 3. Metric Depth Extraction
                    if (depthImgRef != null) {
                        DepthUtils.assignDepthToDetections(
                            depthImage = depthImgRef,
                            detections = trackedDetections,
                            sensorRotationDegrees = 90
                        )
                    }

                    localBrain?.latestDetections = trackedDetections

                    // 4. Local Haptic Navigation
                    val activeTarget = localBrain?.activeTarget ?: "none"
                    val intensities = loadIntensityMap()

                    val guidanceState = hapticArbitrator.updateNavigation(
                        activeTarget = activeTarget,
                        detections = trackedDetections,
                        vibrationIntensities = intensities
                    )

                    if (guidanceState.isGrasped) {
                        val graspedItem = activeTarget
                        localBrain?.activeTarget = "none"
                        localBrain?.targetList?.remove(graspedItem)
                        if (graspedItem != "none") {
                            localBrain?.graspedObjectsHistory?.add(graspedItem)
                        }

                        runOnUiThread {
                            val msg = "Grasped $graspedItem."
                            tvAiResponse.text = "AI: $msg"
                            tts.speak(msg, TextToSpeech.QUEUE_FLUSH, null, "GRASP_DONE")
                        }
                    }

                    frameCounter++
                    val now = SystemClock.uptimeMillis()
                    if (now - lastFpsTimestamp >= 1000) {
                        currentFps = frameCounter
                        frameCounter = 0
                        lastFpsTimestamp = now
                    }

                    // 5. Update Overlay
                    runOnUiThread {
                        if (!isActivityPaused) {
                            overlayView.setTrackedResults(
                                results = trackedDetections,
                                srcWidth = bmpW,
                                srcHeight = bmpH,
                                handIdThreshold = 80
                            )

                            if (!isPttRecording) {
                                val targetStr = if (activeTarget != "none") " | Target: $activeTarget [${guidanceState.activeDevice}]" else ""
                                tvStatus.text = "FPS: $currentFps | Infer: ${result.inferenceTimeMs}ms | Active: ${trackedDetections.size}$targetStr"
                            }
                        }
                    }

                    sendTelemetryToServer(trackedDetections)
                    portraitBitmap.recycle()

                } catch (e: Exception) {
                    Log.e("HANS", "Local CV frame error", e)
                } finally {
                    // Always close images immediately to avoid starving Camera2 HAL buffer queues
                    rgbImgRef.close()
                    depthImgRef?.close()
                    isInferring.set(false)
                }
            }
        } catch (e: Exception) {
            rgbImage?.close()
            depthImage?.close()
            isInferring.set(false)
        }
    }

    private fun sendTelemetryToServer(detections: List<TrackedDetection>) {
        val socket = webSocket ?: return
        try {
            val jsonArray = JSONArray()
            for (det in detections) {
                val obj = JSONObject().apply {
                    put("track_id", det.trackId)
                    put("class_id", det.classId)
                    put("label", det.label)
                    put("confidence", det.confidence)
                    put("depth", det.depth)
                    put("x", det.boundingBox.centerX())
                    put("y", det.boundingBox.centerY())
                    put("w", det.boundingBox.width())
                    put("h", det.boundingBox.height())
                }
                jsonArray.put(obj)
            }
            socket.send(jsonArray.toString())
        } catch (e: Exception) {
            // Ignore
        }
    }

    // =================================================================
    // ARCORE LIFECYCLE (Hardened Order)
    // =================================================================
    override fun onResume() {
        super.onResume()
        isActivityPaused = false
        isTextureSet = false

        if (arSession == null && allPermissionsGranted()) {
            try {
                if (ArCoreApk.getInstance().requestInstall(this, true) == ArCoreApk.InstallStatus.INSTALLED) {
                    arSession = Session(this)
                    val config = Config(arSession)
                    if (arSession!!.isDepthModeSupported(Config.DepthMode.AUTOMATIC)) {
                        config.depthMode = Config.DepthMode.AUTOMATIC
                    }
                    config.updateMode = Config.UpdateMode.LATEST_CAMERA_IMAGE
                    config.planeFindingMode = Config.PlaneFindingMode.HORIZONTAL_AND_VERTICAL
                    arSession!!.configure(config)
                }
            } catch (e: Exception) {
                Log.e("HANS", "ARCore session init failed: $e")
            }
        }

        // CRITICAL ORDER: Resume ARCore first, then GLSurfaceView
        try {
            arSession?.resume()
        } catch (e: Exception) {
            Log.e("HANS", "ARCore resume failed: $e")
        }

        try {
            surfaceView.onResume()
        } catch (e: Exception) {
            Log.e("HANS", "GLSurfaceView resume failed: $e")
        }

        updateBottomNavBackground(R.id.menu_camera)
    }

    override fun onPause() {
        isActivityPaused = true
        stopAllHaptics()

        // CRITICAL ORDER: Pause GLSurfaceView first to halt render calls to session.update()
        try {
            surfaceView.onPause()
        } catch (e: Exception) {
            Log.e("HANS", "GLSurfaceView pause failed: $e")
        }

        // Pause ARCore session second so camera hardware releases cleanly
        try {
            arSession?.pause()
        } catch (e: Exception) {
            Log.e("HANS", "ARCore pause failed: $e")
        }

        super.onPause()
    }

    override fun onStop() {
        stopAllHaptics()
        super.onStop()
    }

    override fun onDestroy() {
        stopAllHaptics()
        super.onDestroy()
        cvExecutor.shutdown()
        // Safely close the ARCore session so Camera 0 is released cleanly for the next activity
        try {
            arSession?.close()
            arSession = null
        } catch (e: Exception) {
            Log.e("HANS", "Error closing arSession: $e")
        }
        webSocket?.close(1000, "App closed")
        try { speechRecognizer?.destroy() } catch (e: Exception) {}
        if (::tts.isInitialized) { tts.stop() }
    }

    // =================================================================
    // OPENGL RENDERER
    // =================================================================
    override fun onSurfaceCreated(gl: GL10?, config: EGLConfig?) {
        GLES20.glClearColor(0.1f, 0.1f, 0.1f, 1.0f)
        cameraRenderer.createOnGlThread()
        arSession?.setCameraTextureName(cameraRenderer.textureId)
    }

    override fun onSurfaceChanged(gl: GL10?, width: Int, height: Int) {
        GLES20.glViewport(0, 0, width, height)
        arSession?.setDisplayGeometry(android.view.Surface.ROTATION_0, width, height)
    }

    override fun onDrawFrame(gl: GL10?) {
        GLES20.glClear(GLES20.GL_COLOR_BUFFER_BIT or GLES20.GL_DEPTH_BUFFER_BIT)
        val session = arSession ?: return
        if (isActivityPaused) return

        try {
            if (!isTextureSet && cameraRenderer.textureId != -1) {
                session.setCameraTextureName(cameraRenderer.textureId)
                isTextureSet = true
            }

            val frame = session.update()
            cameraRenderer.draw(frame)
            triggerLocalInferenceIfIdle(frame)

        } catch (e: Exception) {
            Log.e("HANS", "Error in onDrawFrame: $e")
        }
    }

    // =================================================================
    // TTS
    // =================================================================
    override fun onInit(status: Int) {
        if (status == TextToSpeech.SUCCESS) {
            tts.language = Locale.US
            tts.setOnUtteranceProgressListener(object : android.speech.tts.UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) {}
                override fun onDone(utteranceId: String?) { restartListening() }
                @Deprecated("Deprecated in Java")
                override fun onError(utteranceId: String?) { restartListening() }
            })
        }
    }

    // =================================================================
    // RESPONSIVE TAP & PUSH-TO-TALK LOGIC
    // =================================================================
    private fun setupSpeech() {
        speechIntent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-US")
        }
    }

    private fun setupPttButton() {
        resetPttButton()

        btnPtt.setOnTouchListener { _, event ->
            when (event.action) {
                MotionEvent.ACTION_DOWN -> {
                    touchDownTime = SystemClock.uptimeMillis()

                    if (::tts.isInitialized && tts.isSpeaking) {
                        tts.stop()
                        tvAiResponse.text = "AI: (Interrupted)"
                    }

                    if (!isPttRecording) {
                        startPttRecording()
                    }
                    true
                }
                MotionEvent.ACTION_UP -> {
                    val duration = SystemClock.uptimeMillis() - touchDownTime
                    if (duration >= MIN_HOLD_DURATION_MS) {
                        if (isPttRecording) stopPttRecording()
                    }
                    true
                }
                MotionEvent.ACTION_CANCEL -> {
                    if (isPttRecording) stopPttRecording()
                    true
                }
                else -> false
            }
        }
    }

    private fun startPttRecording() {
        if (isPttRecording) return
        isPttRecording = true

        runOnUiThread {
            tvStatus.text = "🔴 Listening... (Speak now)"
            btnPtt.setBackgroundColor(Color.parseColor(PTT_COLOR_ACTIVE))
            updateBottomNavBarColor(true)
        }

        try {
            speechRecognizer?.destroy()
            speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this@MainActivity).apply {
                setRecognitionListener(object : RecognitionListener {
                    override fun onReadyForSpeech(params: Bundle?) {
                        runOnUiThread { tvStatus.text = "🔴 Listening..." }
                    }

                    override fun onBeginningOfSpeech() {
                        runOnUiThread { tvStatus.text = "🔴 Recording speech..." }
                    }

                    override fun onRmsChanged(rmsdB: Float) {}
                    override fun onBufferReceived(buffer: ByteArray?) {}

                    override fun onEndOfSpeech() {
                        runOnUiThread {
                            tvStatus.text = "Status: Thinking..."
                            btnPtt.setBackgroundColor(Color.parseColor(PTT_COLOR_IDLE))
                            updateBottomNavBarColor(false)
                        }
                    }

                    override fun onError(error: Int) {
                        val msg = when (error) {
                            SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "No speech detected"
                            SpeechRecognizer.ERROR_NO_MATCH -> "Couldn't understand"
                            SpeechRecognizer.ERROR_AUDIO -> "Audio error"
                            else -> "Speech error ($error)"
                        }
                        Log.w("HANS", "Speech recognition error: $msg ($error)")
                        runOnUiThread {
                            tvStatus.text = msg
                            resetPttButton()
                        }
                        restartListening()
                    }

                    override fun onResults(results: Bundle?) {
                        val matches = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                        resetPttButton()

                        if (!matches.isNullOrEmpty()) {
                            val spokenText = matches[0].lowercase().trim()
                            if (spokenText.isNotBlank()) {
                                runOnUiThread { tvAiResponse.text = "You: \"$spokenText\"" }
                                handleUserQuery(spokenText)
                            } else {
                                restartListening()
                            }
                        } else {
                            restartListening()
                        }
                    }

                    override fun onPartialResults(partialResults: Bundle?) {}
                    override fun onEvent(eventType: Int, params: Bundle?) {}
                })
            }

            speechRecognizer?.startListening(speechIntent)

        } catch (e: Exception) {
            Log.e("HANS", "Failed to start speech recording", e)
            resetPttButton()
            restartListening()
        }
    }

    private fun stopPttRecording() {
        if (!isPttRecording) return

        runOnUiThread {
            tvStatus.text = "Status: Thinking..."
            btnPtt.isEnabled = false
        }

        lifecycleScope.launch(Dispatchers.Default) {
            try {
                speechRecognizer?.stopListening()
            } catch (e: Exception) {
                runOnUiThread { resetPttButton() }
                restartListening()
            }
        }
    }

    private fun resetPttButton() {
        runOnUiThread {
            isPttRecording = false
            btnPtt.isEnabled = true
            btnPtt.setBackgroundColor(Color.parseColor(PTT_COLOR_IDLE))
            updateBottomNavBarColor(false)
        }
    }

    private fun restartListening() {
        runOnUiThread {
            if (!isPttRecording) {
                tvStatus.text = "Status: Tap screen to speak"
            }
        }
    }

    private fun stopAllHaptics() {
        try {
            hapticArbitrator.stopAllFeedback()
        } catch (e: Exception) {
            Log.e("HANS", "Failed to stop haptics", e)
        }
    }

    private fun loadIntensityMap(): Map<String, Int> {
        val prefs = getSharedPreferences("FullIntensityPrefs", MODE_PRIVATE)
        return mapOf(
            // Handles both direct calibration keys and the full intensity map keys
            "left" to prefs.getInt("leftIntensity", prefs.getInt("left", 50)),
            "bottom" to prefs.getInt("bottomIntensity", prefs.getInt("down", 50)),
            "right" to prefs.getInt("rightIntensity", prefs.getInt("right", 50)),
            "top" to prefs.getInt("topIntensity", prefs.getInt("top", 50)),
            "topFront" to prefs.getInt("topFrontIntensity", prefs.getInt("topFront", 50)),
            "topBack" to prefs.getInt("topBackIntensity", prefs.getInt("topBack", 50)),
            "belt" to prefs.getInt("beltIntensity", prefs.getInt("belt", 50))
        )
    }

    private fun loadActivePattern(): Int {
        val prefs = getSharedPreferences("PatternPrefs", MODE_PRIVATE)
        return prefs.getInt("PATTERN_INDEX", 0) // 0 = Single, 1 = Multi, 2 = Sequence
    }

    // =================================================================
    // ON-DEVICE LLM QUERY HANDLER
    // =================================================================
    private fun handleUserQuery(spokenText: String) {
        val brain = localBrain ?: return

        lifecycleScope.launch(Dispatchers.IO) {
            try {
                var answer = brain.processUserSpeech(spokenText)

                withContext(Dispatchers.Main) {
                    when {
                        answer.contains("[SPEED:SLOW]") -> { tts.setSpeechRate(0.5f); answer = answer.replace("[SPEED:SLOW]", "") }
                        answer.contains("[SPEED:NORMAL]") -> { tts.setSpeechRate(1.0f); answer = answer.replace("[SPEED:NORMAL]", "") }
                        answer.contains("[SPEED:FAST]") -> { tts.setSpeechRate(1.5f); answer = answer.replace("[SPEED:FAST]", "") }
                    }

                    tvAiResponse.text = "AI: $answer"
                    val params = Bundle()
                    params.putString(TextToSpeech.Engine.KEY_PARAM_UTTERANCE_ID, "TTS_REPLY")
                    tts.speak(answer, TextToSpeech.QUEUE_FLUSH, params, "TTS_REPLY")
                }
            } catch (e: Exception) {
                Log.e("HANS", "Query error", e)
                withContext(Dispatchers.Main) {
                    tvAiResponse.text = "AI: Error analyzing query."
                    restartListening()
                }
            }
        }
    }

    // =================================================================
    // WEBSOCKET (Optional Remote Telemetry/Commands)
    // =================================================================
    private fun startWebSocket() {
        val request = Request.Builder().url(WEBSOCKET_URL).build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                runOnUiThread { tvStatus.text = "Server: Connected" }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                runOnUiThread { tvStatus.text = "Server: Standalone Mode" }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                runOnUiThread { tvStatus.text = "Server: Standalone" }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    if (text.startsWith("{")) {
                        val jsonObj = JSONObject(text)

                        if (jsonObj.has("tts_command")) {
                            val msg = jsonObj.getString("tts_command")
                            runOnUiThread {
                                val params = Bundle()
                                params.putString(TextToSpeech.Engine.KEY_PARAM_UTTERANCE_ID, "TTS_CMD")
                                tts.speak(msg, TextToSpeech.QUEUE_ADD, params, "TTS_CMD")
                            }
                        }

                        if (jsonObj.has("vibration_command")) {
                            val b64Command = jsonObj.getString("vibration_command")
                            val commandBytes = Base64.decode(b64Command, Base64.NO_WRAP)
                            val targetDevice = jsonObj.optString("target_device", "all")

                            when (targetDevice) {
                                "belt" -> beltManager.writeRawCommand(commandBytes)
                                "bracelet" -> braceletManager.writeRawCommand(commandBytes)
                                else -> {
                                    beltManager.writeRawCommand(commandBytes)
                                    braceletManager.writeRawCommand(commandBytes)
                                }
                            }
                        }
                    }
                } catch (e: Exception) {
                    Log.e("HANS", "WebSocket message error", e)
                }
            }
        })
    }

    // =================================================================
    // PERMISSIONS & UTILS
    // =================================================================
    private val REQUIRED_PERMISSIONS = if (Build.VERSION.SDK_INT >= 31) {
        arrayOf(
            Manifest.permission.CAMERA,
            Manifest.permission.RECORD_AUDIO,
            Manifest.permission.BLUETOOTH_SCAN,
            Manifest.permission.BLUETOOTH_CONNECT
        )
    } else {
        arrayOf(
            Manifest.permission.CAMERA,
            Manifest.permission.RECORD_AUDIO,
            Manifest.permission.BLUETOOTH,
            Manifest.permission.BLUETOOTH_ADMIN,
            Manifest.permission.ACCESS_FINE_LOCATION
        )
    }

    private fun allPermissionsGranted() = REQUIRED_PERMISSIONS.all {
        ContextCompat.checkSelfPermission(baseContext, it) == PackageManager.PERMISSION_GRANTED
    }

    private val requestPermissionsLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { permissions ->
            if (permissions.all { it.value }) {
                initSystem()
            } else {
                Toast.makeText(this, "Permissions required", Toast.LENGTH_SHORT).show()
                finish()
            }
        }

    private fun dpToPx(dp: Int): Int = (dp * resources.displayMetrics.density).toInt()

    private fun addBottomColorBar() {
        bottomColorBar = View(this).apply {
            setBackgroundColor(Color.parseColor(PTT_COLOR_IDLE))
            isClickable = false
            isFocusable = false
        }
        val params = FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, dpToPx(30)).apply {
            gravity = Gravity.BOTTOM
        }
        bottomNav.addView(bottomColorBar, 0, params)
    }

    private fun updateBottomNavBarColor(active: Boolean) {
        bottomColorBar.setBackgroundColor(
            Color.parseColor(if (active) PTT_COLOR_ACTIVE else PTT_COLOR_IDLE)
        )
    }

    private fun updateBottomNavBackground(page: Int) {
        val menuView = bottomNav.getChildAt(1) as ViewGroup
        val home = menuView.getChildAt(0)
        val camera = menuView.getChildAt(1)
        val setting = menuView.getChildAt(2)

        val rectangle = ContextCompat.getDrawable(this, R.drawable.bottom_rectangle)
        val rectangleSelected = ContextCompat.getDrawable(this, R.drawable.bottom_rectangle_selected)
        val topLeft = ContextCompat.getDrawable(this, R.drawable.bottom_top_left)
        val topLeftSelected = ContextCompat.getDrawable(this, R.drawable.bottom_top_left_selected)
        val topRight = ContextCompat.getDrawable(this, R.drawable.bottom_top_right)
        val topRightSelected = ContextCompat.getDrawable(this, R.drawable.bottom_top_right_selected)

        when (page) {
            R.id.menu_home -> {
                home.background = rectangleSelected
                camera.background = topLeft
                setting.background = rectangle
            }
            R.id.menu_camera -> {
                home.background = topRight
                camera.background = rectangleSelected
                setting.background = topLeft
            }
            R.id.menu_setting -> {
                home.background = rectangle
                camera.background = topRight
                setting.background = rectangleSelected
            }
        }
    }
}
