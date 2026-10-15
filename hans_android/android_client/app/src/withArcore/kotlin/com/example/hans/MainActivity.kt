package com.example.hans

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.RectF
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
import android.widget.ImageView
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

    // UI Components
    private lateinit var surfaceView: GLSurfaceView
    private lateinit var overlayView: BoundingBoxOverlay
    private var ivWidePreview: ImageView? = null
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
    // ON-DEVICE CV, WIDE CAMERA & TRACKING
    // =================================================================
    private var dualPipeline: DualYoloPipeline? = null
    private val tracker: SortTracker get() = CvPipelineSingleton.tracker
    private val openVocabTracker = com.example.hans.ml.tracker.OpenVocabTracker()
    private val cvExecutor: ExecutorService = Executors.newSingleThreadExecutor()
    private val isInferring = AtomicBoolean(false)
    private lateinit var wideCameraManager: WideCameraManager
    @Volatile private var isWideScanning = false

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
        ivWidePreview = findViewById(R.id.ivWidePreview)

        setupOverlayView()
        addBottomColorBar()

        bottomNav.selectedItemId = R.id.menu_camera
        bottomNav.post {
            updateBottomNavBackground(R.id.menu_camera)
            updateBottomNavBarColor(false)
        }

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

        surfaceView.preserveEGLContextOnPause = true
        surfaceView.setEGLContextClientVersion(2)
        surfaceView.setEGLConfigChooser(8, 8, 8, 8, 16, 0)
        surfaceView.setRenderer(this)
        surfaceView.renderMode = GLSurfaceView.RENDERMODE_CONTINUOUSLY

        braceletManager = BleManagerSingleton.getBraceletManager(this)
        beltManager = BleManagerSingleton.getBeltManager(this)

        hapticArbitrator = HapticArbitrator(braceletManager, beltManager)
        wideCameraManager = WideCameraManager(this)

        initLocalCvEngine()
        initLocalBrain()

        if (allPermissionsGranted()) {
            initSystem()
        } else {
            requestPermissionsLauncher.launch(REQUIRED_PERMISSIONS)
        }

        tts = TextToSpeech(this, this)

        findViewById<ConstraintLayout>(R.id.rootLayout).setBackgroundColor(Color.BLACK)

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
            },
            wideScanProvider = {
                performWideScan()
            },
            wideImageProvider = {
                captureWideSnapshotWithSaving()
            },
            openVocabTargetSetter = { targetLabel, targetBox ->
                runOnUiThread {
                    openVocabTracker.setTarget(targetLabel, targetBox)
                    tvAiResponse.text = "AI: Tracking $targetLabel"
                    tvStatus.text = "Target locked: $targetLabel"
                }
            },
            diagnosticListener = { statusText ->
                runOnUiThread {
                    tvAiResponse.text = "AI: $statusText"
                    Log.i("HANS_DIAG", statusText)
                }
            },
            onGraspConfirmed = { graspedItem ->
                // Fires when user says "Got it" / "Grasped" or tool executes
                runOnUiThread {
                    hapticArbitrator.sendGraspConfirmationPulse()
                    openVocabTracker.clearTarget()
                    val msg = if (graspedItem != "none") "Grasped $graspedItem." else "Grasp confirmed."
                    tvAiResponse.text = "AI: $msg"
                    tvStatus.text = "Status: Target completed"
                    tts.speak(msg, TextToSpeech.QUEUE_FLUSH, null, "GRASP_DONE")
                }
            }
        )
    }

    /**
     * APPROACH A: SAFE PAUSE-RESUME ULTRA-WIDE SNAPSHOT WITH DUAL SAVING (RAW & ANNOTATED)
     */
    private suspend fun performWideScan(): List<String> = withContext(Dispatchers.Main) {
        if (isWideScanning) return@withContext emptyList()
        isWideScanning = true

        tvStatus.text = "📷 Scanning surroundings (Ultra-Wide)..."

        var snapshot: Bitmap? = null
        val wideTarget = wideCameraManager.findUltraWideTarget()

        if (wideTarget != null) {
            try {
                surfaceView.onPause()
                try { arSession?.pause() } catch (e: Exception) { Log.w("HANS", "AR pause: $e") }

                kotlinx.coroutines.delay(120)

                snapshot = withContext(Dispatchers.IO) {
                    wideCameraManager.captureWideSnapshot()
                }

            } catch (e: Exception) {
                Log.e("HANS", "Wide scan capture error", e)
            } finally {
                try {
                    arSession?.resume()
                    surfaceView.onResume()
                } catch (e: Exception) {
                    Log.e("HANS", "Failed to resume ARCore after wide scan", e)
                }
                isWideScanning = false
            }
        } else {
            isWideScanning = false
            val currentObjects = localBrain?.latestDetections?.filter { it.classId < 80 }?.map { it.label }?.distinct() ?: emptyList()
            tvStatus.text = "Wide lens unavailable - scanned main view"
            return@withContext currentObjects
        }

        if (snapshot == null) {
            tvStatus.text = "Ultra-Wide capture unavailable"
            return@withContext emptyList()
        }

        // --- Save raw snapshot if toggle is enabled ---
        val settingsPrefs = getSharedPreferences("HansSettingsPrefs", MODE_PRIVATE)
        val shouldSave = settingsPrefs.getBoolean("SAVE_DEBUG_IMAGES", false)

        if (shouldSave) {
            withContext(Dispatchers.IO) {
                DebugImageSaver.saveSnapshotToGallery(this@MainActivity, snapshot, "wide_raw")
            }
        }

        // --- Run YOLO on wide snapshot ---
        val pipeline = dualPipeline
        val (detectedLabels, detections) = withContext(Dispatchers.Default) {
            if (pipeline != null) {
                val origThresh = pipeline.confThreshold
                pipeline.confThreshold = 0.25f
                val wideResult = pipeline.detect(snapshot)
                pipeline.confThreshold = origThresh

                val labels = wideResult.objectDetections.map { it.label }.distinct()
                Pair(labels, wideResult.objectDetections)
            } else {
                Pair(emptyList(), emptyList())
            }
        }

        // --- Burn boxes and save annotated photo if toggle is enabled ---
        var annotatedBitmapRef: Bitmap? = null
        if (shouldSave) {
            withContext(Dispatchers.IO) {
                val (_, annotated) = DebugImageSaver.saveAnnotatedSnapshotToGallery(
                    this@MainActivity,
                    snapshot,
                    detections,
                    "wide_annotated"
                )
                annotatedBitmapRef = annotated
            }
        }

        // Display preview on screen for 2.0s
        val displayBitmap = annotatedBitmapRef ?: snapshot
        ivWidePreview?.setImageBitmap(displayBitmap)
        ivWidePreview?.visibility = View.VISIBLE

        tvStatus.text = if (detectedLabels.isNotEmpty()) {
            "Wide detected: ${detectedLabels.joinToString(", ")}"
        } else {
            "Wide: No objects recognized"
        }

        lifecycleScope.launch(Dispatchers.Main) {
            kotlinx.coroutines.delay(2000)
            ivWidePreview?.visibility = View.GONE
            snapshot.recycle()
            annotatedBitmapRef?.recycle()
        }

        return@withContext detectedLabels
    }

    /**
     * Captures a single wide snapshot for Multimodal VLM analysis and guarantees saving if enabled.
     */
    private suspend fun captureWideSnapshotWithSaving(): Bitmap? = withContext(Dispatchers.Main) {
        if (isWideScanning) return@withContext null
        isWideScanning = true

        tvStatus.text = "📷 Capturing wide snapshot..."

        var snapshot: Bitmap? = null
        try {
            surfaceView.onPause()
            try { arSession?.pause() } catch (e: Exception) { Log.w("HANS", "AR pause: $e") }

            kotlinx.coroutines.delay(120)

            snapshot = withContext(Dispatchers.IO) {
                wideCameraManager.captureWideSnapshot()
            }
        } catch (e: Exception) {
            Log.e("HANS", "Capture error: $e")
        } finally {
            try {
                arSession?.resume()
                surfaceView.onResume()
            } catch (e: Exception) {
                Log.e("HANS", "AR resume error: $e")
            }
            isWideScanning = false
        }

        if (snapshot != null) {
            val settingsPrefs = getSharedPreferences("HansSettingsPrefs", MODE_PRIVATE)
            val shouldSave = settingsPrefs.getBoolean("SAVE_DEBUG_IMAGES", false)

            if (shouldSave) {
                withContext(Dispatchers.IO) {
                    val saved = DebugImageSaver.saveSnapshotToGallery(this@MainActivity, snapshot, "wide_raw")
                    Log.i("HANS", "📸 Saved raw snapshot to gallery: $saved")
                }
            }

            ivWidePreview?.setImageBitmap(snapshot)
            ivWidePreview?.visibility = View.VISIBLE
            lifecycleScope.launch(Dispatchers.Main) {
                kotlinx.coroutines.delay(1800)
                ivWidePreview?.visibility = View.GONE
            }
        }

        return@withContext snapshot
    }

    private fun initSystem() {
        startWebSocket()
        setupSpeech()
        setupPttButton()
    }

    // =================================================================
    // LOCAL CV + ARCORE DEPTH + LOCAL HAPTIC PIPELINE (Zero-Copy)
    // =================================================================
    private fun triggerLocalInferenceIfIdle(frame: Frame) {
        if (isActivityPaused || isWideScanning) return
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
                    if (isActivityPaused || isWideScanning) return@execute

                    // 1. Zero-Copy YUV -> Direct TFLite FloatBuffer conversion
                    CameraImageConverter.yuvToNormalizedRgbBuffer(
                        image = rgbImgRef,
                        targetSize = 640,
                        outputBuffer = pipeline.objInputBuffer
                    )

                    // 2. Dual-Model Inference
                    val result = pipeline.detectDirect()

                    // 3. Kalman Tracking
                    val rawTracked = tracker.update(result.mergedDetections)

                    // 4. Metric Depth Extraction
                    if (depthImgRef != null) {
                        DepthUtils.assignDepthToDetections(
                            depthImage = depthImgRef,
                            detections = rawTracked,
                            sensorRotationDegrees = 90
                        )
                    }

                    // 4b. PERSISTENT OPEN-VOCABULARY BINDING:
                    // Binds 'blue cup' to the nearest 'cup', or maintains persistent spatial track
                    val trackedDetections = openVocabTracker.processFrame(rawTracked)

                    localBrain?.latestDetections = trackedDetections

                    // 5. Local Haptic Navigation
                    val activeTarget = localBrain?.activeTarget ?: "none"
                    val intensities = loadIntensityMap()

                    val guidanceState = hapticArbitrator.updateNavigation(
                        activeTarget = activeTarget,
                        detections = trackedDetections,
                        vibrationIntensities = intensities
                    )

                    if (guidanceState.isGrasped) {
                        val graspedItem = activeTarget
                        openVocabTracker.clearTarget()
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

                    // 6. Update Overlay
                    runOnUiThread {
                        if (!isActivityPaused && !isWideScanning) {
                            overlayView.setTrackedResults(
                                results = trackedDetections,
                                srcWidth = 640,
                                srcHeight = 640,
                                handIdThreshold = 80
                            )

                            if (!isPttRecording) {
                                val targetStr = if (activeTarget != "none") " | Target: $activeTarget [${guidanceState.activeDevice}]" else ""
                                tvStatus.text = "FPS: $currentFps | Infer: ${result.inferenceTimeMs}ms | Active: ${trackedDetections.size}$targetStr"
                            }
                        }
                    }

                    sendTelemetryToServer(trackedDetections)

                } catch (e: Exception) {
                    Log.e("HANS", "Local CV frame error", e)
                } finally {
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
            // Ignore socket telemetry errors
        }
    }

    // =================================================================
    // ARCORE LIFECYCLE
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

        try {
            surfaceView.onPause()
        } catch (e: Exception) {
            Log.e("HANS", "GLSurfaceView pause failed: $e")
        }

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
        if (isActivityPaused || isWideScanning) return

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
            if (!isPttRecording && !isWideScanning) {
                tvStatus.text = "Status: Tap screen to speak"
            }
        }
    }

    private fun stopAllHaptics() {
        try {
            openVocabTracker.clearTarget()
            hapticArbitrator.stopAllFeedback()
        } catch (e: Exception) {
            Log.e("HANS", "Failed to stop haptics", e)
        }
    }

    private fun loadIntensityMap(): Map<String, Int> {
        val prefs = getSharedPreferences("FullIntensityPrefs", MODE_PRIVATE)
        return mapOf(
            "left" to prefs.getInt("leftIntensity", prefs.getInt("left", 50)),
            "bottom" to prefs.getInt("bottomIntensity", prefs.getInt("down", 50)),
            "right" to prefs.getInt("rightIntensity", prefs.getInt("right", 50)),
            "top" to prefs.getInt("topIntensity", prefs.getInt("top", 50)),
            "topFront" to prefs.getInt("topFrontIntensity", prefs.getInt("topFront", 50)),
            "topBack" to prefs.getInt("topBackIntensity", prefs.getInt("topBack", 50)),
            "belt" to prefs.getInt("beltIntensity", prefs.getInt("belt", 50))
        )
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
