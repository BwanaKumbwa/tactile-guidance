package com.example.hans

import android.Manifest
import android.content.Context
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
import com.google.ar.core.exceptions.CameraNotAvailableException
import com.google.ar.core.exceptions.UnavailableException
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
    private val SERVER_IP = "192.168.1.16" // UPDATE
    private val WEBSOCKET_URL = "ws://$SERVER_IP:8000/ws/video"
    private val COMMAND_URL = "http://$SERVER_IP:8000/api/command"
    private val WAKE_WORD = "hans"

    // BLUETOOTH MAC ADDRESSES
    private val MAC_BRACELET = "00:A0:50:93:8A:AA" // UPDATE
    private val MAC_BELT     = "00:A0:50:DA:2B:54" // UPDATE
    // =================================================================

    // UI Components
    private lateinit var surfaceView: GLSurfaceView // <--- ARCore uses GLSurfaceView
    private lateinit var overlayView: OverlayView
    private lateinit var tvStatus: TextView
    private lateinit var tvAiResponse: TextView

    // ARCore Session
    private var arSession: Session? = null
    private var hasSetTextureNames = false
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

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        audioManager = getSystemService(AUDIO_SERVICE) as AudioManager

        // 1. Link UI Variables
        surfaceView = findViewById(R.id.surfaceView)
        overlayView = findViewById(R.id.overlayView)
        tvStatus = findViewById(R.id.tvStatus)
        tvAiResponse = findViewById(R.id.tvAiResponse)

        // Setup OpenGL Surface for ARCore
        surfaceView.preserveEGLContextOnPause = true
        surfaceView.setEGLContextClientVersion(2)
        surfaceView.setEGLConfigChooser(8, 8, 8, 8, 16, 0)
        surfaceView.setRenderer(this)
        surfaceView.renderMode = GLSurfaceView.RENDERMODE_CONTINUOUSLY

        // 2. Initialize BLE Managers
        braceletManager = BleManager(this)
        beltManager = BleManager(this)

        // 3. Check Permissions & Start
        if (allPermissionsGranted()) {
            initSystem()
        } else {
            requestPermissionsLauncher.launch(REQUIRED_PERMISSIONS)
        }

        tts = TextToSpeech(this, this)

        val rootLayout = findViewById<androidx.constraintlayout.widget.ConstraintLayout>(R.id.rootLayout)
        rootLayout.setOnClickListener {
            if (::tts.isInitialized && tts.isSpeaking) {
                Log.d("HANS", "User interrupted AI. Stopping TTS.")
                tts.stop()
                tvAiResponse.text = "AI: (Interrupted)"
                restartListening()
            }
        }

        // Set the background to black so the "stripes" blend in natively
        rootLayout.setBackgroundColor(android.graphics.Color.BLACK)

        surfaceView.post {
            val screenWidth = surfaceView.width

            // The raw ARCore CPU image sent to the PC is usually a 3:4 portrait ratio (e.g., 480x640).
            // If your PC stream looks slightly squished with 3:4, change the 4 and 3 to 16 and 9 (for 9:16).
            val targetHeight = (screenWidth * 16) / 9

            // 1. Resize the Camera View to force letterboxing (black bars)
            val surfaceParams = surfaceView.layoutParams
            surfaceParams.height = targetHeight
            surfaceView.layoutParams = surfaceParams

            // 2. Resize the Overlay View so the AI boxes perfectly match the new camera dimensions
            val overlayParams = overlayView.layoutParams
            overlayParams.height = targetHeight
            overlayView.layoutParams = overlayParams
        }
    }

    private fun initSystem() {
        startWebSocket()
        setupSpeech()
        connectBleDevices()
    }

    // =================================================================
    // ARCORE LIFECYCLE
    // =================================================================
    override fun onResume() {
        super.onResume()
        if (arSession == null && allPermissionsGranted()) {
            try {
                if (ArCoreApk.getInstance().requestInstall(this, true) == ArCoreApk.InstallStatus.INSTALLED) {
                    arSession = Session(this)

                    // ENABLE HARDWARE DEPTH MAPS
                    val config = Config(arSession)
                    if (arSession!!.isDepthModeSupported(Config.DepthMode.AUTOMATIC)) {
                        config.depthMode = Config.DepthMode.AUTOMATIC
                        Log.i("HANS", "ARCore Depth Mode Enabled")
                    }
                    config.updateMode = Config.UpdateMode.LATEST_CAMERA_IMAGE
                    arSession!!.configure(config)
                }
            } catch (e: Exception) {
                Log.e("HANS", "ARCore Unavailable: $e")
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
        if (arSession != null) {
            surfaceView.onPause()
            arSession!!.pause()
        }
    }

    // =================================================================
    // OPENGL RENDERER (Extracts frames from ARCore)
    // =================================================================
    override fun onSurfaceCreated(gl: GL10?, config: EGLConfig?) {
        GLES20.glClearColor(0.1f, 0.1f, 0.1f, 1.0f)

        // 1. Initialize shaders and generate the Texture ID
        cameraRenderer.createOnGlThread()

        // 2. We MUST set the texture name here so ARCore knows where to write the pixels
        arSession?.setCameraTextureName(cameraRenderer.textureId)
    }

    override fun onSurfaceChanged(gl: GL10?, width: Int, height: Int) {
        GLES20.glViewport(0, 0, width, height)
        // 3. Update ARCore with the screen dimensions so UVs map correctly
        arSession?.setDisplayGeometry(android.view.Surface.ROTATION_0, width, height)
    }

    override fun onDrawFrame(gl: GL10?) {
        // Clear screen
        GLES20.glClear(GLES20.GL_COLOR_BUFFER_BIT or GLES20.GL_DEPTH_BUFFER_BIT)
        val session = arSession ?: return

        try {
            // 4. Update session to get latest camera frame into the texture
            session.setCameraTextureName(cameraRenderer.textureId) // Enforce binding
            val frame = session.update()

            // 5. Tell the renderer to draw that texture to the screen
            cameraRenderer.draw(frame)

            // 6. Network AI Logic
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
    private fun processArFrame(frame: Frame) {
        if (webSocket == null) return

        var rgbImage: Image? = null
        var depthImage: Image? = null

        try {
            rgbImage = frame.acquireCameraImage()

            try {
                depthImage = frame.acquireDepthImage16Bits()
            } catch (e: Exception) {
                // Depth is not ready on the first few frames, ignore safely
            }

            // 1. Process RGB
            val rgbBytes = yuvImageToJpegBytes(rgbImage, 640)

            // 2. Process Depth (If available)
            var depthBytes = ByteArray(0)
            if (depthImage != null) {
                depthBytes = depth16ToPngBytes(depthImage)
            }

            // 3. Pack into Multiplexed Protocol: [4 Bytes: Length of RGB] + [RGB Bytes] + [Depth Bytes]
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

        val width = depthImage.width
        val height = depthImage.height
        val rowStride = plane.rowStride
        val pixelStride = plane.pixelStride

        val pixels = IntArray(width * height)

        for (y in 0 until height) {
            for (x in 0 until width) {
                val byteIndex = (y * rowStride) + (x * pixelStride)
                val distanceMm = buffer.getShort(byteIndex).toInt() and 0xFFFF
                val pixelIndex = (y * width) + x

                if (distanceMm == 0) {
                    pixels[pixelIndex] = android.graphics.Color.rgb(0, 0, 0)
                } else {
                    // Pack the 16-bit distance into the Red and Green channels
                    val r = (distanceMm shr 8) and 0xFF
                    val g = distanceMm and 0xFF
                    // B is kept at 0
                    pixels[pixelIndex] = android.graphics.Color.rgb(r, g, 0)
                }
            }
        }

        val bitmap = Bitmap.createBitmap(pixels, width, height, Bitmap.Config.ARGB_8888)
        val out = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.PNG, 100, out)
        return out.toByteArray()
    }

    // =================================================================
    // TTS LOGIC
    // =================================================================
    override fun onInit(status: Int) {
        if (status == TextToSpeech.SUCCESS) {
            tts.language = Locale.US
            tts.setOnUtteranceProgressListener(object : android.speech.tts.UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) {}
                override fun onDone(utteranceId: String?) {
                    Log.d("HANS", "TTS Finished. Resuming listening.")
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
    // BLUETOOTH LOGIC
    // =================================================================
    private fun connectBleDevices() {
        Thread {
            try {
                braceletManager.connect(MAC_BRACELET)
                Thread.sleep(2000)
                val dummy = JSONObject()
                val vib = JSONObject()
                vib.put("top", 0)
                dummy.put("vibration", vib)
                braceletManager.writeRawCommand(ByteArray(0)) // Prevent crash on dummy init
            } catch (e: Exception) { Log.e("HANS", "Bracelet Connect Error", e) }
        }.start()

        Thread {
            try {
                beltManager.connect(MAC_BELT)
                Thread.sleep(2000)
                val dummy = JSONObject()
                val vib = JSONObject()
                vib.put("top", 0)
                dummy.put("vibration", vib)
                beltManager.writeRawCommand(ByteArray(0))
            } catch (e: Exception) { Log.e("HANS", "Belt Connect Error", e) }
        }.start()

        runOnUiThread { tvStatus.text = "Status: Connecting BLE..." }
    }

    // =================================================================
    // WEBSOCKET LOGIC
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
                    }
                    else if (text.startsWith("{")) {
                        val jsonObj = JSONObject(text)
                        if (jsonObj.has("vibration_command")) {
                            val b64Command = jsonObj.getString("vibration_command")
                            val commandBytes = Base64.decode(b64Command, Base64.NO_WRAP)
                            braceletManager.writeRawCommand(commandBytes)
                            beltManager.writeRawCommand(commandBytes)
                        }
                        if (jsonObj.has("system_command")) {
                            val cmd = jsonObj.getString("system_command")
                            if (cmd == "idle_mode") {
                                currentFrameThrottle = 1000L // Drop to 1 Frame Per Second
                                runOnUiThread { tvStatus.text = "Status: Idle (Battery Saver)" }
                            } else if (cmd == "active_mode") {
                                currentFrameThrottle = 100L  // Resume 10 Frames Per Second
                                runOnUiThread { tvStatus.text = "Status: Active Tracking" }
                            }
                        }
                        if (jsonObj.has("tts_command")) {
                            val msg = jsonObj.getString("tts_command")
                            runOnUiThread {
                                val params = Bundle()
                                params.putString(TextToSpeech.Engine.KEY_PARAM_UTTERANCE_ID, "TTS_CMD")
                                // Use QUEUE_ADD so it doesn't cut off anything else currently speaking
                                tts.speak(msg, TextToSpeech.QUEUE_ADD, params, "TTS_CMD")
                            }
                        }
                    }

                } catch (e: Exception) {
                    // Ignore parse errors
                }
            }
        })
    }

    // =================================================================
    // CONTINUOUS SPEECH LOGIC
    // =================================================================
    private fun setupSpeech() {
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            tvStatus.text = "Speech Recog Not Available"
            return
        }

        speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this)
        speechIntent = android.content.Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-US")
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, "en-US")
            putExtra(RecognizerIntent.EXTRA_ONLY_RETURN_LANGUAGE_PREFERENCE, true)
        }

        speechRecognizer.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) {
                runOnUiThread { tvStatus.text = "Status: Listening for 'Hello'..." }
            }
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {}

            override fun onError(error: Int) {
                restartListening()
            }

            override fun onResults(results: Bundle?) {
                val matches = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                if (!matches.isNullOrEmpty()) {
                    val spokenText = matches[0].lowercase()
                    if (spokenText.contains(WAKE_WORD)) {
                        runOnUiThread { tvAiResponse.text = "Processing: $spokenText" }
                        sendToBackend(spokenText)
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

        startListeningMuted()
    }

    private fun restartListening() {
        runOnUiThread {
            try {
                if (::speechRecognizer.isInitialized) {
                    speechRecognizer.cancel()
                }
            } catch (e: Exception) {}

            android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
                startListeningMuted()
            }, 100)
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

    private fun sendToBackend(text: String) {
        val json = JSONObject()
        json.put("text", text)
        json.put("bracelet_connected", braceletManager.isConnected())
        json.put("belt_connected", beltManager.isConnected())

        val body = json.toString().toRequestBody("application/json; charset=utf-8".toMediaType())
        val request = Request.Builder().url(COMMAND_URL).post(body).build()

        client.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                runOnUiThread { tvAiResponse.text = "AI Error: Network Fail" }
            }

            override fun onResponse(call: Call, response: Response) {
                val responseData = response.body?.string()
                if (responseData != null) {
                    try {
                        val jsonRes = JSONObject(responseData)
                        var answer = jsonRes.optString("answer", "Done")

                        if (answer.contains("[SPEED:SLOW]")) {
                            tts.setSpeechRate(0.5f)
                            answer = answer.replace("[SPEED:SLOW]", "")
                        } else if (answer.contains("[SPEED:NORMAL]")) {
                            tts.setSpeechRate(1.0f)
                            answer = answer.replace("[SPEED:NORMAL]", "")
                        } else if (answer.contains("[SPEED:FAST]")) {
                            tts.setSpeechRate(1.5f)
                            answer = answer.replace("[SPEED:FAST]", "")
                        }

                        val isShutdown = answer.contains("[SHUTDOWN]")
                        val isDisconnect = answer.contains("[DISCONNECT]")

                        if (isShutdown || isDisconnect) {
                            var finalAnswer = answer
                            if (isShutdown) finalAnswer = finalAnswer.replace("[SHUTDOWN]", "Shutting down the server. Goodbye.")
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
                        }
                    } catch (e: Exception) {
                        restartListening()
                    }
                } else {
                    restartListening()
                }
            }
        })
    }

    // =================================================================
    // PERMISSIONS
    // =================================================================
    private val REQUIRED_PERMISSIONS = if (Build.VERSION.SDK_INT >= 31) {
        arrayOf(Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO, Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
    } else {
        arrayOf(Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO, Manifest.permission.BLUETOOTH, Manifest.permission.BLUETOOTH_ADMIN, Manifest.permission.ACCESS_FINE_LOCATION)
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
        webSocket?.close(1000, "App closed")
        braceletManager.disconnect()
        beltManager.disconnect()
        try { speechRecognizer.destroy() } catch (e: Exception) {}
        if (::tts.isInitialized) {
            tts.stop()
            tts.shutdown()
        }
    }
}