package com.example.hans

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.media.AudioManager
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
import android.widget.ImageButton
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.core.*
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import com.google.android.material.bottomnavigation.BottomNavigationView
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity(), TextToSpeech.OnInitListener {

    // =================================================================
    // CONFIGURATION
    // =================================================================
    private val SERVER_IP = "192.168.178.33" // UPDATE
    private val WEBSOCKET_URL = "ws://$SERVER_IP:8000/ws/video"
    private val COMMAND_URL = "http://$SERVER_IP:8000/api/command"
    private val WAKE_WORD = "hans"

    // BLUETOOTH MAC ADDRESSES
    private val MAC_BRACELET = "00:A0:50:65:73:20" // UPDATE
    private val MAC_BELT     = "00:A0:50:39:96:11" // UPDATE
    // =================================================================

    // UI Components
    private lateinit var viewFinder: PreviewView
    private lateinit var overlayView: OverlayView
    private lateinit var tvStatus: TextView
    private lateinit var tvAiResponse: TextView
    private lateinit var btnPtt: ImageButton
    private val PTT_COLOR_IDLE = R.drawable.circle_green
    private val PTT_COLOR_ACTIVE = R.drawable.circle_red
    private lateinit var pttRecognitionListener: RecognitionListener

    private lateinit var cameraExecutor: ExecutorService

    // Networking
    private val client = OkHttpClient()
    private var webSocket: WebSocket? = null

    // Speech
    private lateinit var speechRecognizer: SpeechRecognizer
    private var isListening = false
    private var speechIntent: android.content.Intent? = null

    @Volatile private var isPttRecording = false

    // Bluetooth Managers (One for each device)
    private lateinit var braceletManager: BleManager
    private lateinit var beltManager: BleManager

    private lateinit var tts: TextToSpeech

    private lateinit var audioManager: AudioManager

    private val Intensity_Prefs = "FullIntensityPrefs"

    private val Pattern_Prefs = "PatternPrefs"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        audioManager = getSystemService(AUDIO_SERVICE) as AudioManager

        // 1. Link UI Variables
        viewFinder = findViewById(R.id.viewFinder)
        overlayView = findViewById(R.id.overlayView)
        tvStatus = findViewById(R.id.tvStatus)
        tvAiResponse = findViewById(R.id.tvAiResponse)
        btnPtt = findViewById(R.id.btnPtt)

        val bottomNav = findViewById<BottomNavigationView>(R.id.bottomNavigation)

        bottomNav.selectedItemId = R.id.menu_camera
        bottomNav.setOnItemSelectedListener { item ->

            when (item.itemId) {

                R.id.menu_home -> {
                    startActivity(Intent(this, BluetoothActivity::class.java))
                    finish()
                    true
                }

                R.id.menu_camera -> true

                R.id.menu_setting -> {
                    startActivity(Intent(this, SettingsActivity::class.java))
                    finish()
                    true
                }

                else -> false
            }
        }

        // 2. Initialize BLE Managers
        braceletManager = BleManager(this)
        beltManager = BleManager(this)

        // 3. Check Permissions & Start
        if (allPermissionsGranted()) {
            initSystem()
        } else {
            requestPermissionsLauncher.launch(REQUIRED_PERMISSIONS)
        }

        cameraExecutor = Executors.newSingleThreadExecutor()

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

        tts = TextToSpeech(this, this)

        rootLayout.setOnClickListener {
            // If the AI is currently talking, shut it up and start listening
            if (::tts.isInitialized && tts.isSpeaking) {
                Log.d("HANS", "User interrupted AI. Stopping TTS.")

                // 1. Instantly stop the speech
                tts.stop()

                // 2. Update the UI so the user knows it registered
                tvAiResponse.text = "AI: (Interrupted)"

                // 3. Force the microphone to restart immediately
                restartListening()
            }
        }
    }

    private fun initSystem() {
        startCamera()
        startWebSocket()
        setupSpeech()
        connectBleDevices()
    }

    // In onInit, set up a listener to know when the TTS finishes speaking
    override fun onInit(status: Int) {
        if (status == TextToSpeech.SUCCESS) {
            tts.language = java.util.Locale.US

            // Listen for when TTS finishes
            tts.setOnUtteranceProgressListener(object : android.speech.tts.UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) {}

                override fun onDone(utteranceId: String?) {
                    // TTS finished speaking! Safe to start listening again.
                    showStatus("🎤 Ready. Hold microphone to speak.")
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
    // 1. BLUETOOTH LOGIC
    // =================================================================
    private fun connectBleDevices() {
        // We connect in background threads to avoid UI jank
        Thread {
            try {
                Log.d("HANS", "Connecting to Bracelet: $MAC_BRACELET")
                braceletManager.connect(MAC_BRACELET)

                // WAIT 2 seconds for connection to settle
                Thread.sleep(2000)

                // Send a dummy command to keep it alive
                val dummy = JSONObject()
                val vib = JSONObject()
                vib.put("top", 0) // Zero intensity
                dummy.put("vibration", vib)

                //braceletManager.writeIntensity(vib)
            } catch (e: Exception) { Log.e("HANS", "Bracelet Connect Error", e) }
        }.start()

        Thread {
            try {
                Log.d("HANS", "Connecting to Belt: $MAC_BELT")
                beltManager.connect(MAC_BELT)

                // WAIT 2 seconds for connection to settle
                Thread.sleep(2000)

                // Send a dummy command to keep it alive
                val dummy = JSONObject()
                val vib = JSONObject()
                vib.put("top", 0) // Zero intensity
                dummy.put("vibration", vib)

                //beltManager.writeIntensity(vib)
            } catch (e: Exception) { Log.e("HANS", "Belt Connect Error", e) }
        }.start()

        runOnUiThread { tvStatus.text = "Status: Connecting BLE..." }
    }

    // =================================================================
    // 2. CAMERA LOGIC
    // =================================================================
    private fun startCamera() {
        val cameraProviderFuture = ProcessCameraProvider.getInstance(this)

        cameraProviderFuture.addListener({
            val cameraProvider: ProcessCameraProvider = cameraProviderFuture.get()

            // Preview
            val preview = Preview.Builder().build().also {
                it.setSurfaceProvider(viewFinder.surfaceProvider)
            }

            // Image Analysis
            val imageAnalyzer = ImageAnalysis.Builder()
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_YUV_420_888)
                .setTargetResolution(android.util.Size(640, 480))
                .build()
                .also {
                    it.setAnalyzer(cameraExecutor) { image ->
                        processImage(image)
                    }
                }

            try {
                cameraProvider.unbindAll()
                cameraProvider.bindToLifecycle(
                    this, CameraSelector.DEFAULT_BACK_CAMERA, preview, imageAnalyzer
                )
            } catch (exc: Exception) {
                Log.e("HANS", "Use case binding failed", exc)
            }

        }, ContextCompat.getMainExecutor(this))
    }

    private fun processImage(image: ImageProxy) {
        try {
            val bitmap = image.toBitmap()
            val scaledBitmap = Bitmap.createScaledBitmap(
                bitmap, 640, (640.toFloat() / bitmap.width * bitmap.height).toInt(), true
            )

            val outRgb = ByteArrayOutputStream()
            scaledBitmap.compress(Bitmap.CompressFormat.JPEG, 60, outRgb)
            val rgbBytes = outRgb.toByteArray()

            // =========================================================
            // HARDWARE DEPTH INJECTION
            // =========================================================
            // Call your future ARCore/Camera2 depth extractor here.
            // It should return a Grayscale JPEG or PNG byte array.
            val depthBytes = getHardwareDepthBytes()

            // Pack both into a single Binary Payload
            // [4 Bytes: Length of RGB] + [RGB Bytes] + [Depth Bytes]
            val buffer = java.nio.ByteBuffer.allocate(4 + rgbBytes.size + depthBytes.size)
            buffer.putInt(rgbBytes.size)
            buffer.put(rgbBytes)
            buffer.put(depthBytes)

            val byteString = okio.ByteString.of(*buffer.array())
            webSocket?.send(byteString)
            // =========================================================

        } catch (e: Exception) {
            Log.e("HANS", "Error processing frame", e)
        } finally {
            image.close()
        }
    }

    private fun getHardwareDepthBytes(): ByteArray {
        // Create a dummy depth frame matching RGB dimensions
        // Server will detect this and fallback to ML depth estimation

        // Match the RGB frame dimensions (640 x ~480)
        val width = 640
        val height = 480

        // Create a grayscale image (all zeros = no depth info)
        val depthBitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        depthBitmap.eraseColor(android.graphics.Color.BLACK)

        // Compress to PNG
        val out = ByteArrayOutputStream()
        depthBitmap.compress(Bitmap.CompressFormat.PNG, 100, out)

        Log.d("HANS", "Dummy depth frame created: ${out.size()} bytes")
        return out.toByteArray()
    }
    // =================================================================
    // 3. WEBSOCKET LOGIC
    // =================================================================
    private fun startWebSocket() {
        val request = Request.Builder().url(WEBSOCKET_URL).build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                runOnUiThread { tvStatus.text = "Status: Connected to PC" }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                runOnUiThread { tvStatus.text = "Status: Conn Failed" }
                Log.e("HANS", "WebSocket Error", t)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                runOnUiThread { tvStatus.text = "Status: Disconnected" }
            }

            // === HANDLE JSON TEXT MESSAGES ===
            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    // CASE A: Bounding Boxes (JSON Array)
                    if (text.startsWith("[")) {
                        val jsonArray = JSONArray(text)
                        runOnUiThread {
                            overlayView.setDetections(jsonArray)
                            if (viewFinder.alpha < 1f) viewFinder.alpha = 1f
                        }
                    }

                    // CASE B: Vibration Command (JSON Object)
                    else if (text.startsWith("{")) {
                        val jsonObj = JSONObject(text)

                        // Look for the Base64 Encoded Command
                        if (jsonObj.has("vibration_command")) {
                            val b64Command = jsonObj.getString("vibration_command")

                            // Decode to raw bytes
                            val commandBytes = Base64.decode(b64Command, Base64.NO_WRAP)

                            // Send directly to hardware
                            braceletManager.writeRawCommand(commandBytes)
                            beltManager.writeRawCommand(commandBytes)

                            Log.d("HANS", "Forwarded ${commandBytes.size} bytes to BLE")
                        }
                    }
                } catch (e: Exception) {
                    // Ignore parse errors
                }
            }


        })
    }

    // =================================================================
    // 4. CONTINUOUS SPEECH LOGIC (ALWAYS ON)
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

        setupPttButton()
        startListeningMuted()
    }

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

        if (::tts.isInitialized && tts.isSpeaking) {
            tts.stop()
        }

        runOnUiThread {
            btnPtt.setBackgroundResource(PTT_COLOR_ACTIVE)
        }

        showStatus("Recording... Release to Send")

        try {
            speechRecognizer.cancel()
            speechRecognizer.destroy()
        } catch (_: Exception) {
        }

        android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({

            if (!isPttRecording) return@postDelayed

            speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this)
            speechRecognizer.setRecognitionListener(pttRecognitionListener)

            try {
                speechRecognizer.startListening(speechIntent)
            } catch (e: Exception) {
                e.printStackTrace()
                showStatus("❌ Failed to start recording")
                resetPttButton()
                restartListening()
            }

        }, 500)
    }

    private fun stopPttRecording() {

        runOnUiThread {
            btnPtt.isEnabled = false
        }

        showStatus("Processing command...")

        try {
            speechRecognizer.stopListening()
        } catch (e: Exception) {
            e.printStackTrace()
            showStatus("❌ Failed to process")
            resetPttButton()
            restartListening()
        }
    }

    private fun resetPttButton() {

        isPttRecording = false

        runOnUiThread {

            btnPtt.isEnabled = true
            btnPtt.setBackgroundResource(PTT_COLOR_IDLE)
        }

        showStatus("Hold microphone to speak")
    }

    private fun showStatus(message: String) {
        runOnUiThread {
            tvStatus.text = message
            Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
        }
    }

    private fun restartListening() {
        // Force all SpeechRecognizer actions onto the Main UI Thread
        runOnUiThread {
            try {
                if (::speechRecognizer.isInitialized) {
                    // Prevent overlapping calls
                    speechRecognizer.cancel()
                }
            } catch (e: Exception) {
                Log.e("HANS", "Error canceling recognizer", e)
            }

            // Add a tiny delay to prevent the UI thread from locking up
            android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
                startListeningMuted()
            }, 100)
        }
    }

    private fun startListeningMuted() {
        runOnUiThread {
            if (!::speechRecognizer.isInitialized) return@runOnUiThread

            try {
                // 1. Mute the system "Bloop" sound
                audioManager.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_MUTE, 0)

                // 2. Start listening
                speechRecognizer.startListening(speechIntent)

                // 3. Unmute immediately after it starts
                android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
                    audioManager.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_UNMUTE, 0)
                }, 300)
            } catch (e: Exception) {
                Log.e("HANS", "Error starting muted listener", e)
            }
        }
    }

    private fun sendToBackend(text: String) {
        val json = JSONObject()
        json.put("text", text)

        json.put("bracelet_connected", braceletManager.isConnected())
        json.put("belt_connected", beltManager.isConnected())
        json.put("vibration", loadIntensity())
        json.put("pattern", loadPattern())

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
                            tts.setSpeechRate(0.5f) // Half speed
                            answer = answer.replace("[SPEED:SLOW]", "")
                        } else if (answer.contains("[SPEED:NORMAL]")) {
                            tts.setSpeechRate(1.0f) // Normal speed
                            answer = answer.replace("[SPEED:NORMAL]", "")
                        } else if (answer.contains("[SPEED:FAST]")) {
                            tts.setSpeechRate(1.5f) // 1.5x speed
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
                                }, 3000)
                            }
                            return // Stop normal processing
                        }

                        runOnUiThread {
                            tvAiResponse.text = "AI: $answer"
                            showStatus("🤖 AI is speaking...")

                            // Speak the text, and pass an ID ("TTS_REPLY") so
                            // onDone() knows when to restart the microphone.
                            val params = Bundle()
                            params.putString(TextToSpeech.Engine.KEY_PARAM_UTTERANCE_ID, "TTS_REPLY")
                            tts.speak(answer, TextToSpeech.QUEUE_FLUSH, params, "TTS_REPLY")
                        }
                    } catch (e: Exception) {
                        Log.e("HANS", "Json Parse Error", e)
                        restartListening() // Restart if json fails
                    }
                } else {
                    restartListening() // Restart if network fails
                }
            }
        })
    }

    // =================================================================
    // PERMISSIONS (Updated for Android 12+)
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
                Toast.makeText(this, "Permissions not granted.", Toast.LENGTH_SHORT).show()
                finish()
            }
        }

    private fun loadIntensity(): JSONObject{
        val prefs = getSharedPreferences(Intensity_Prefs, Context.MODE_PRIVATE)

        val vibration = JSONObject()
        vibration.put("left", prefs.getInt("leftIntensity", 0))
        vibration.put("down", prefs.getInt("bottomIntensity", 0))
        vibration.put("right", prefs.getInt("rightIntensity", 0))
        vibration.put("top", prefs.getInt("topIntensity", 0))
        vibration.put("top_front", prefs.getInt("topFrontIntensity", 0))
        vibration.put("top_back", prefs.getInt("topBackIntensity", 0))

        return vibration
    }

    private fun loadPattern(): String{
        val prefs = getSharedPreferences(Pattern_Prefs, MODE_PRIVATE)

        return prefs.getString("PATTERN_CODE","VIB_PATTERN_SINGLE") ?: "VIB_PATTERN_SINGLE"
    }

    override fun onDestroy() {
        super.onDestroy()
        cameraExecutor.shutdown()
        webSocket?.close(1000, "App closed")

        // Disconnect BLE
        braceletManager.disconnect()
        beltManager.disconnect()

        try { speechRecognizer.destroy() } catch (e: Exception) {}

        // Add TTS cleanup
        if (::tts.isInitialized) {
            tts.stop()
            tts.shutdown()
        }
    }
}