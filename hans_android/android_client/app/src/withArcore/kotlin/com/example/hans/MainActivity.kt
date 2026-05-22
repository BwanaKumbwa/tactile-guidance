package com.example.hans

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageFormat
import android.graphics.Rect
import android.graphics.YuvImage
import android.media.AudioManager
import android.media.Image
import android.opengl.GLES20
import android.opengl.GLSurfaceView
import android.os.Build
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.util.Base64
import android.util.Log
import android.view.MotionEvent
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.google.ar.core.ArCoreApk
import com.google.ar.core.Config
import com.google.ar.core.Frame
import com.google.ar.core.Session
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import okio.ByteString.Companion.toByteString
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.nio.ByteBuffer
import java.util.Locale
import javax.microedition.khronos.egl.EGLConfig
import javax.microedition.khronos.opengles.GL10

class MainActivity : AppCompatActivity(), TextToSpeech.OnInitListener, GLSurfaceView.Renderer {

    // =================================================================
    // CONFIGURATION
    // =================================================================
    private val SERVER_IP = "" // UPDATE
    private val WEBSOCKET_URL = "ws://$SERVER_IP:8000/ws/video"
    private val COMMAND_URL = "http://$SERVER_IP:8000/api/command"
    private val WAKE_WORD = "hans"

    // BLUETOOTH MAC ADDRESSES
    private val MAC_BRACELET = "00:A0:50:93:8A:AA" // UPDATE
    private val MAC_BELT     = "00:A0:50:DA:2B:54" // UPDATE
    // =================================================================

    // UI Components
    private lateinit var surfaceView: GLSurfaceView
    private lateinit var overlayView: OverlayView
    private lateinit var tvStatus: TextView
    private lateinit var tvAiResponse: TextView
    private lateinit var btnPtt: Button

    // PTT State
    // true only while the PTT button is physically held down.
    // Both PTT and wake-word detection coexist — this flag tells onResults which path to take.
    @Volatile private var isPttRecording = false
    private val PTT_COLOR_IDLE   = android.graphics.Color.parseColor("#CC2196F3") // Blue
    private val PTT_COLOR_ACTIVE = android.graphics.Color.parseColor("#CCCC0000") // Red

    // ARCore Session
    private var arSession: Session? = null
    @Volatile private var lastFrameTime = 0L
    @Volatile private var currentFrameThrottle = 100L
    private val cameraRenderer = CameraRenderer()

    // Networking
    private val client = OkHttpClient()
    private var webSocket: WebSocket? = null

    // Speech
    private lateinit var speechRecognizer: SpeechRecognizer
    private var isListening = false
    private var speechIntent: android.content.Intent? = null
    private lateinit var tts: TextToSpeech
    private lateinit var audioManager: AudioManager

    // Bluetooth Managers
    private lateinit var braceletManager: BleManager
    private lateinit var beltManager: BleManager

    private lateinit var pttRecognitionListener: RecognitionListener

    // =================================================================
    // LIFECYCLE
    // =================================================================
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        audioManager = getSystemService(AUDIO_SERVICE) as AudioManager

        // Link UI
        surfaceView = findViewById(R.id.surfaceView)
        overlayView = findViewById(R.id.overlayView)
        tvStatus    = findViewById(R.id.tvStatus)
        tvAiResponse = findViewById(R.id.tvAiResponse)
        btnPtt      = findViewById(R.id.btnPtt)

        // OpenGL surface for ARCore
        surfaceView.preserveEGLContextOnPause = true
        surfaceView.setEGLContextClientVersion(2)
        surfaceView.setEGLConfigChooser(8, 8, 8, 8, 16, 0)
        surfaceView.setRenderer(this)
        surfaceView.renderMode = GLSurfaceView.RENDERMODE_CONTINUOUSLY

        // BLE
        braceletManager = BleManager(this)
        beltManager     = BleManager(this)

        // Permissions
        if (allPermissionsGranted()) {
            initSystem()
        } else {
            requestPermissionsLauncher.launch(REQUIRED_PERMISSIONS)
        }

        tts = TextToSpeech(this, this)

        // Tap anywhere to interrupt TTS
        val rootLayout = findViewById<androidx.constraintlayout.widget.ConstraintLayout>(R.id.rootLayout)
        rootLayout.setOnClickListener {
            if (::tts.isInitialized && tts.isSpeaking) {
                Log.d("HANS", "User interrupted AI. Stopping TTS.")
                tts.stop()
                tvAiResponse.text = "AI: (Interrupted)"
                restartListening()
            }
        }

        rootLayout.setBackgroundColor(android.graphics.Color.BLACK)

        // Force 16:9 camera area
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

    private fun initSystem() {
        startWebSocket()
        setupSpeech()      // Starts background wake-word listening
        setupPttButton()   // Adds PTT as an additional input method
        connectBleDevices()
    }

    // =================================================================
    // ARCORE LIFECYCLE
    // =================================================================
    private fun processArFrame(frame: Frame) {
        if (webSocket == null) return

        var rgbImage: Image? = null
        var depthImage: Image? = null

        try {
            rgbImage = frame.acquireCameraImage()

            try {
                depthImage = frame.acquireDepthImage16Bits()
            } catch (e: Exception) {
                // Depth not ready yet — log once
                Log.d("HANS", "Depth initializing... move phone around to help")
            }

            val rgbBytes = yuvImageToJpegBytes(rgbImage, 640)

            var depthBytes = ByteArray(0)
            if (depthImage != null) {
                depthBytes = depth16ToPngBytes(depthImage)
                
                // Only send if depth has actual data (not all zeros)
                val hasDepthData = depthBytes.size > 100 && !isDepthAllZeros(depthBytes)
                if (!hasDepthData) {
                    Log.d("HANS", "Depth detected but empty, waiting...")
                    depthBytes = ByteArray(0) // Fall back to empty
                }
            }

            // Protocol: [4 bytes: RGB length][RGB bytes][Depth bytes]
            val buffer = ByteBuffer.allocate(4 + rgbBytes.size + depthBytes.size)
            buffer.putInt(rgbBytes.size)
            buffer.put(rgbBytes)
            buffer.put(depthBytes)

            webSocket?.send(buffer.array().toByteString(0, buffer.capacity()))

        } catch (e: Exception) {
            Log.e("HANS", "Frame processing failed", e)
        } finally {
            rgbImage?.close()
            depthImage?.close()
        }
    }

    private fun isDepthAllZeros(depthBytes: ByteArray): Boolean {
        // Sample the middle of the depth frame
        val sampleSize = minOf(1000, depthBytes.size)
        val sample = depthBytes.takeLast(sampleSize)
        return sample.all { it == 0.toByte() }
    }

    override fun onResume() {
        super.onResume()
        
        // Try to get the pre-warmed session first (with type cast)
        val prewarmSession = ArCoreManager.resume()
        if (prewarmSession != null) {
            @Suppress("UNCHECKED_CAST")
            arSession = prewarmSession as? Session
        }
        
        // Fallback: if warmup didn't complete yet, initialize normally
        if (arSession == null && allPermissionsGranted()) {
            try {
                if (ArCoreApk.getInstance().requestInstall(this, true) == ArCoreApk.InstallStatus.INSTALLED) {
                    arSession = Session(this)
                    val config = Config(arSession)
                    
                    if (arSession!!.isDepthModeSupported(Config.DepthMode.AUTOMATIC)) {
                        config.depthMode = Config.DepthMode.AUTOMATIC
                        Log.i("HANS", "ARCore Depth Mode Enabled (fallback init)")
                    }
                    config.updateMode = Config.UpdateMode.LATEST_CAMERA_IMAGE
                    config.planeFindingMode = Config.PlaneFindingMode.HORIZONTAL_AND_VERTICAL
                    arSession!!.configure(config)
                }
            } catch (e: Exception) {
                Log.e("HANS", "ARCore fallback init failed: $e")
            }
        }
        
        try {
            arSession?.resume()
            surfaceView.onResume()
        } catch (e: Exception) {
            Log.e("HANS", "Camera not available")
        }
    }

    override fun onPause() {
        super.onPause()
        ArCoreManager.pause()
        surfaceView.onPause()
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

        try {
            session.setCameraTextureName(cameraRenderer.textureId)
            val frame = session.update()
            cameraRenderer.draw(frame)

            val currentTime = System.currentTimeMillis()
            if (currentTime - lastFrameTime > currentFrameThrottle) {
                lastFrameTime = currentTime
                processArFrame(frame)
            }
        } catch (e: Exception) {
            Log.e("HANS", "Error updating ARCore frame", e)
        }
    }

    // =================================================================
    // MULTIPLEXED DATA SENDER (RGB + DEPTH)
    // =================================================================
    private fun yuvImageToJpegBytes(image: Image, targetWidth: Int): ByteArray {
        val yBuffer = image.planes[0].buffer
        val uBuffer = image.planes[1].buffer
        val vBuffer = image.planes[2].buffer

        val ySize = yBuffer.remaining()
        val uSize = uBuffer.remaining()
        val vSize = vBuffer.remaining()

        val nv21 = ByteArray(ySize + uSize + vSize)
        yBuffer.get(nv21, 0, ySize)
        vBuffer.get(nv21, ySize, vSize)
        uBuffer.get(nv21, ySize + vSize, uSize)

        val yuvImage = YuvImage(nv21, ImageFormat.NV21, image.width, image.height, null)
        val out = ByteArrayOutputStream()
        yuvImage.compressToJpeg(Rect(0, 0, image.width, image.height), 80, out)

        val bmp = BitmapFactory.decodeByteArray(out.toByteArray(), 0, out.size())
        val targetHeight = (targetWidth.toFloat() / bmp.width * bmp.height).toInt()
        val scaledBmp = Bitmap.createScaledBitmap(bmp, targetWidth, targetHeight, true)

        val finalOut = ByteArrayOutputStream()
        scaledBmp.compress(Bitmap.CompressFormat.JPEG, 60, finalOut)
        return finalOut.toByteArray()
    }

    private fun depth16ToPngBytes(depthImage: Image): ByteArray {
        val plane = depthImage.planes[0]
        val buffer = plane.buffer
        buffer.order(java.nio.ByteOrder.LITTLE_ENDIAN)

        val width  = depthImage.width
        val height = depthImage.height
        val rowStride   = plane.rowStride
        val pixelStride = plane.pixelStride

        val pixels = IntArray(width * height)

        for (y in 0 until height) {
            for (x in 0 until width) {
                val byteIndex  = (y * rowStride) + (x * pixelStride)
                val distanceMm = buffer.getShort(byteIndex).toInt() and 0xFFFF
                val pixelIndex = (y * width) + x

                pixels[pixelIndex] = if (distanceMm == 0) {
                    android.graphics.Color.rgb(0, 0, 0)
                } else {
                    // Pack 16-bit depth into R and G channels
                    val r = (distanceMm shr 8) and 0xFF
                    val g = distanceMm and 0xFF
                    android.graphics.Color.rgb(r, g, 0)
                }
            }
        }

        val bitmap = Bitmap.createBitmap(pixels, width, height, Bitmap.Config.ARGB_8888)
        val out = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.PNG, 100, out)
        return out.toByteArray()
    }

    // =================================================================
    // TTS
    // =================================================================
    override fun onInit(status: Int) {
        if (status == TextToSpeech.SUCCESS) {
            tts.language = Locale.US
            //tts.setPitch(0.6f)           // Lower pitch (0.5-2.0, default 1.0)
            //tts.setSpeechRate(0.9f)      // Slightly slower (0.5-2.0, default 1.0)
            tts.setOnUtteranceProgressListener(object : android.speech.tts.UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) {}

                override fun onDone(utteranceId: String?) {
                    Log.d("HANS", "TTS finished — resuming background listening.")
                    // Resume background wake-word listening after TTS finishes in both modes
                    restartListening()
                }

                @Deprecated("Deprecated in Java")
                override fun onError(utteranceId: String?) {
                    restartListening()
                }
            })
        }
    }

    // =================================================================
    // BLUETOOTH
    // =================================================================
    private fun connectBleDevices() {
        Thread {
            try {
                braceletManager.connect(MAC_BRACELET)
                Thread.sleep(2000)
                braceletManager.writeRawCommand(ByteArray(0))
            } catch (e: Exception) { Log.e("HANS", "Bracelet Connect Error", e) }
        }.start()

        Thread {
            try {
                beltManager.connect(MAC_BELT)
                Thread.sleep(2000)
                beltManager.writeRawCommand(ByteArray(0))
            } catch (e: Exception) { Log.e("HANS", "Belt Connect Error", e) }
        }.start()

        runOnUiThread { tvStatus.text = "Status: Connecting BLE..." }
    }

    // =================================================================
    // WEBSOCKET
    // =================================================================
    private fun startWebSocket() {
        val request = Request.Builder().url(WEBSOCKET_URL).build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {

            override fun onOpen(webSocket: WebSocket, response: Response) {
                runOnUiThread { tvStatus.text = "Status: Connected to PC" }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                runOnUiThread { tvStatus.text = "Status: Conn Failed" }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                runOnUiThread { tvStatus.text = "Status: Disconnected" }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    if (text.startsWith("[")) {
                        val jsonArray = JSONArray(text)
                        runOnUiThread {
                            overlayView.setDetections(jsonArray)
                            if (surfaceView.alpha < 1f) surfaceView.alpha = 1f
                        }
                    } else if (text.startsWith("{")) {
                        val jsonObj = JSONObject(text)

                        if (jsonObj.has("vibration_command")) {
                            val b64Command = jsonObj.getString("vibration_command")
                            val commandBytes = Base64.decode(b64Command, Base64.NO_WRAP)
                            braceletManager.writeRawCommand(commandBytes)
                            beltManager.writeRawCommand(commandBytes)
                        }

                        if (jsonObj.has("system_command")) {
                            when (jsonObj.getString("system_command")) {
                                "idle_mode" -> {
                                    currentFrameThrottle = 1000L
                                    runOnUiThread { tvStatus.text = "Status: Idle (Battery Saver)" }
                                }
                                "active_mode" -> {
                                    currentFrameThrottle = 100L
                                    runOnUiThread { tvStatus.text = "Status: Active Tracking" }
                                }
                            }
                        }

                        if (jsonObj.has("tts_command")) {
                            val msg = jsonObj.getString("tts_command")
                            runOnUiThread {
                                val params = Bundle()
                                params.putString(TextToSpeech.Engine.KEY_PARAM_UTTERANCE_ID, "TTS_CMD")
                                tts.speak(msg, TextToSpeech.QUEUE_ADD, params, "TTS_CMD")
                            }
                        }
                    }
                } catch (e: Exception) { /* ignore parse errors */ }
            }
        })
    }

    // =================================================================
    // PUSH-TO-TALK
    // Wake-word background listening always runs. Holding the button
    // cancels the current session, records a PTT utterance, and sends
    // it directly — no wake word required. After the response, background
    // listening resumes automatically.
    // =================================================================
    private fun setupPttButton() {
        resetPttButton()

        btnPtt.setOnTouchListener { _, event ->
            when (event.action) {
                MotionEvent.ACTION_DOWN -> {
                    if (!isPttRecording) startPttRecording()
                    true
                }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    if (isPttRecording) stopPttRecording()
                    true
                }
                else -> false
            }
        }
    }

    private fun startPttRecording() {
        isPttRecording = true

        if (::tts.isInitialized && tts.isSpeaking) tts.stop()

        runOnUiThread {
            tvStatus.text = "🔴 Recording... release to send"
            btnPtt.text = "Release to Send"
            btnPtt.setBackgroundColor(PTT_COLOR_ACTIVE)
        }

        // Destroy the background recognizer
        try {
            speechRecognizer.cancel()
            speechRecognizer.destroy()
            Log.d("HANS", "Background recognizer destroyed")
        } catch (e: Exception) {
            Log.e("HANS", "Recognizer destroy failed: $e")
        }

        // Wait for audio system to fully reset
        android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
            if (!isPttRecording) return@postDelayed

            try {
                // Create a NEW recognizer for PTT
                speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this@MainActivity)
                speechRecognizer.setRecognitionListener(object : RecognitionListener {
                    override fun onReadyForSpeech(params: Bundle?) {
                        runOnUiThread { tvStatus.text = "🔴 Listening..." }
                    }
                    override fun onBeginningOfSpeech() {}
                    override fun onRmsChanged(rmsdB: Float) {}
                    override fun onBufferReceived(buffer: ByteArray?) {}
                    override fun onEndOfSpeech() {}
                    override fun onError(error: Int) {
                        Log.e("HANS", "PTT error: $error")
                        val msg = when (error) {
                            SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "No speech detected — try again"
                            SpeechRecognizer.ERROR_NO_MATCH       -> "Couldn't understand — try again"
                            SpeechRecognizer.ERROR_AUDIO          -> "Mic error — try again"
                            else                                  -> "Error ($error) — try again"
                        }
                        runOnUiThread { tvStatus.text = msg }
                        resetPttButton()
                        restartListening()
                    }
                    override fun onResults(results: Bundle?) {
                        val matches = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                        if (!matches.isNullOrEmpty()) {
                            val spokenText = matches[0].lowercase().trim()
                            resetPttButton()
                            if (spokenText.isNotBlank()) {
                                runOnUiThread { tvAiResponse.text = "Processing: \"$spokenText\"" }
                                sendToBackend(spokenText)
                            } else {
                                runOnUiThread { tvStatus.text = "Nothing heard — try again" }
                                restartListening()
                            }
                        }
                    }
                    override fun onPartialResults(partialResults: Bundle?) {
                        val partial = partialResults?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                        if (!partial.isNullOrEmpty() && partial[0].isNotBlank()) {
                            runOnUiThread { tvAiResponse.text = "Hearing: \"${partial[0]}\"" }
                        }
                    }
                    override fun onEvent(eventType: Int, params: Bundle?) {}
                })

                audioManager.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_MUTE, 0)
                speechRecognizer.startListening(speechIntent)
                Log.d("HANS", "PTT listening started (new recognizer)")

                android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
                    audioManager.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_UNMUTE, 0)
                }, 300)
            } catch (e: Exception) {
                Log.e("HANS", "PTT start failed: $e")
                isPttRecording = false
                resetPttButton()
                restartListening()
            }
        }, 800) // Increased delay to allow audio shutdown
    }

    private fun stopPttRecording() {
        // Keep isPttRecording = true until onResults/onError fires so that
        // the recognition callback knows which path to take.
        runOnUiThread {
            tvStatus.text = "Status: Processing..."
            btnPtt.text   = "Processing..."
            btnPtt.isEnabled = false
        }
        try {
            // stopListening() finalises the utterance and triggers onResults
            // — unlike cancel() which discards it.
            speechRecognizer.stopListening()
        } catch (e: Exception) {
            Log.e("HANS", "PTT stop failed: $e")
            isPttRecording = false
            runOnUiThread { resetPttButton() }
            restartListening()
        }
    }

    /** Restores button to idle state. Safe to call from any thread. */
    private fun resetPttButton() {
        runOnUiThread {
            isPttRecording   = false
            btnPtt.text      = "Hold to Speak"
            btnPtt.isEnabled = true
            btnPtt.setBackgroundColor(PTT_COLOR_IDLE)
        }
    }

    // =================================================================
    // SPEECH RECOGNITION
    // =================================================================
    private fun setupSpeech() {
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            tvStatus.text = "Speech Recog Not Available"
            return
        }

        pttRecognitionListener = object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) {
                runOnUiThread {
                    tvStatus.text = if (isPttRecording)
                        "🔴 Listening..."
                    else
                        "Status: Listening for '$WAKE_WORD'..."
                }
            }

            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {}

            override fun onError(error: Int) {
                if (isPttRecording) {
                    val msg = when (error) {
                        SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "No speech detected — try again"
                        SpeechRecognizer.ERROR_NO_MATCH       -> "Couldn't understand — try again"
                        SpeechRecognizer.ERROR_AUDIO          -> "Mic error — try again"
                        else                                  -> "Mic error ($error) — try again"
                    }
                    runOnUiThread { tvStatus.text = msg }
                    resetPttButton()
                }
                restartListening()
            }

            override fun onResults(results: Bundle?) {
                val matches = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                if (!matches.isNullOrEmpty()) {
                    val spokenText = matches[0].lowercase().trim()

                    if (isPttRecording) {
                        resetPttButton()
                        if (spokenText.isNotBlank()) {
                            runOnUiThread { tvAiResponse.text = "Processing: \"$spokenText\"" }
                            sendToBackend(spokenText)
                        } else {
                            runOnUiThread { tvStatus.text = "Nothing heard — try again" }
                            restartListening()
                        }
                    } else {
                        if (spokenText.contains(WAKE_WORD)) {
                            runOnUiThread { tvAiResponse.text = "Processing: $spokenText" }
                            sendToBackend(spokenText)
                        } else {
                            restartListening()
                        }
                    }
                }
            }

            override fun onPartialResults(partialResults: Bundle?) {
                if (isPttRecording) {
                    val partial = partialResults?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    if (!partial.isNullOrEmpty() && partial[0].isNotBlank()) {
                        runOnUiThread { tvAiResponse.text = "Hearing: \"${partial[0]}\"" }
                    }
                }
            }

            override fun onEvent(eventType: Int, params: Bundle?) {}
        }

        speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this)
        speechRecognizer.setRecognitionListener(pttRecognitionListener)

        speechIntent = android.content.Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-US")
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, "en-US")
            putExtra(RecognizerIntent.EXTRA_ONLY_RETURN_LANGUAGE_PREFERENCE, true)
        }

        startListeningMuted()
    }

    private fun restartListening() {
        runOnUiThread {
            try {
                if (::speechRecognizer.isInitialized) speechRecognizer.destroy()
            } catch (e: Exception) {}

            android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
                speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this)
                speechRecognizer.setRecognitionListener(pttRecognitionListener)
                startListeningMuted()
            }, 200)
        }
    }

    private fun startListeningMuted() {
        runOnUiThread {
            if (!::speechRecognizer.isInitialized) return@runOnUiThread
            try {
                audioManager.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_MUTE, 0)
                speechRecognizer.startListening(speechIntent)
                android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
                    audioManager.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_UNMUTE, 0)
                }, 300)
            } catch (e: Exception) {}
        }
    }

    private fun toggleListening() {
        if (isListening) {
            speechRecognizer.stopListening()
            isListening = false
        } else {
            speechRecognizer.startListening(speechIntent)
            isListening = true
        }
    }

    // =================================================================
    // BACKEND COMMUNICATION
    // =================================================================
    private fun sendToBackend(text: String) {
        val json = JSONObject().apply {
            put("text", text)
            put("bracelet_connected", braceletManager.isConnected())
            put("belt_connected",     beltManager.isConnected())
        }

        val body    = json.toString().toRequestBody("application/json; charset=utf-8".toMediaType())
        val request = Request.Builder().url(COMMAND_URL).post(body).build()

        client.newCall(request).enqueue(object : Callback {

            override fun onFailure(call: Call, e: IOException) {
                runOnUiThread { tvAiResponse.text = "AI Error: Network Fail" }
                resetPttButton()   // safe even if PTT was not active
                restartListening()
            }

            override fun onResponse(call: Call, response: Response) {
                val responseData = response.body?.string()
                if (responseData != null) {
                    try {
                        val jsonRes = JSONObject(responseData)
                        var answer  = jsonRes.optString("answer", "Done")

                        // Speech rate tags
                        when {
                            answer.contains("[SPEED:SLOW]")   -> { tts.setSpeechRate(0.5f); answer = answer.replace("[SPEED:SLOW]", "") }
                            answer.contains("[SPEED:NORMAL]") -> { tts.setSpeechRate(1.0f); answer = answer.replace("[SPEED:NORMAL]", "") }
                            answer.contains("[SPEED:FAST]")   -> { tts.setSpeechRate(1.5f); answer = answer.replace("[SPEED:FAST]", "") }
                        }

                        val isShutdown   = answer.contains("[SHUTDOWN]")
                        val isDisconnect = answer.contains("[DISCONNECT]")

                        if (isShutdown || isDisconnect) {
                            var finalAnswer = answer
                            if (isShutdown)   finalAnswer = finalAnswer.replace("[SHUTDOWN]", "Shutting down the server. Goodbye.")
                            if (isDisconnect) finalAnswer = finalAnswer.replace("[DISCONNECT]", "Disconnecting. Goodbye.")

                            runOnUiThread {
                                tvAiResponse.text = "AI: $finalAnswer"
                                tts.speak(finalAnswer, TextToSpeech.QUEUE_FLUSH, null, null)
                                android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
                                    finishAndRemoveTask()
                                    kotlin.system.exitProcess(0)
                                }, 2500)
                            }
                            return
                        }

                        runOnUiThread {
                            tvAiResponse.text = "AI: $answer"
                            val params = Bundle()
                            params.putString(TextToSpeech.Engine.KEY_PARAM_UTTERANCE_ID, "TTS_REPLY")
                            tts.speak(answer, TextToSpeech.QUEUE_FLUSH, params, "TTS_REPLY")
                            // TTS onDone → restartListening() — no manual restart needed here
                        }

                    } catch (e: Exception) {
                        resetPttButton()
                        restartListening()
                    }
                } else {
                    resetPttButton()
                    restartListening()
                }
            }
        })
    }

    // =================================================================
    // PERMISSIONS
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

    override fun onDestroy() {
        super.onDestroy()
        ArCoreManager.destroy()
        webSocket?.close(1000, "App closed")
        braceletManager.disconnect()
        beltManager.disconnect()
        try { speechRecognizer.destroy() } catch (e: Exception) {}
        if (::tts.isInitialized) { tts.stop(); tts.shutdown() }
    }
}