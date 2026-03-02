package com.example.hans

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageFormat
import android.graphics.Rect
import android.graphics.YuvImage
import android.media.AudioManager
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
import androidx.camera.core.*
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import okio.ByteString.Companion.toByteString
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.Locale

class MainActivity : AppCompatActivity(), TextToSpeech.OnInitListener {

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
    private lateinit var viewFinder: PreviewView
    private lateinit var overlayView: OverlayView
    private lateinit var tvStatus: TextView
    private lateinit var tvAiResponse: TextView
    private lateinit var btnToggleListen: Button

    private lateinit var cameraExecutor: ExecutorService

    // Networking
    private val client = OkHttpClient()
    private var webSocket: WebSocket? = null

    // Speech
    private lateinit var speechRecognizer: SpeechRecognizer
    private var isListening = false
    private var speechIntent: android.content.Intent? = null

    // Bluetooth Managers (One for each device)
    private lateinit var braceletManager: BleManager
    private lateinit var beltManager: BleManager

    private lateinit var tts: TextToSpeech

    private lateinit var audioManager: AudioManager

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        audioManager = getSystemService(AUDIO_SERVICE) as AudioManager

        // 1. Link UI Variables
        viewFinder = findViewById(R.id.viewFinder)
        overlayView = findViewById(R.id.overlayView)
        tvStatus = findViewById(R.id.tvStatus)
        tvAiResponse = findViewById(R.id.tvAiResponse)
        btnToggleListen = findViewById(R.id.btnToggleListen)

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

        btnToggleListen.setOnClickListener {
            toggleListening()
        }

        tts = TextToSpeech(this, this)
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

                braceletManager.writeIntensity(vib)
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

                beltManager.writeIntensity(vib)
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

            // Resize to 640 width
            val scaledBitmap = Bitmap.createScaledBitmap(
                bitmap,
                640,
                (640.toFloat() / bitmap.width * bitmap.height).toInt(),
                true
            )

            val out = ByteArrayOutputStream()
            scaledBitmap.compress(Bitmap.CompressFormat.JPEG, 60, out)
            val imageBytes = out.toByteArray()

            // Binary Optimization (Okio)
            val byteString = imageBytes.toByteString(0, imageBytes.size)

            webSocket?.send(byteString)

        } catch (e: Exception) {
            Log.e("HANS", "Error processing frame", e)
        } finally {
            image.close()
        }
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

            // === NEW: HANDLE JSON TEXT MESSAGES ===
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

        speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this)
        speechIntent = android.content.Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
            // Optional: Tell the recognizer to prefer offline mode if available
            // putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
        }

        speechRecognizer.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) {
                runOnUiThread { tvStatus.text = "Status: Listening for 'Hello'..." }
            }
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}

            override fun onEndOfSpeech() {
                // User stopped talking.
                // We don't restart here; we wait for onResults or onError.
            }

            override fun onError(error: Int) {
                // Error 7 (ERROR_NO_MATCH) or Error 6 (ERROR_SPEECH_TIMEOUT)
                // happen constantly when it's quiet. Just silently restart.
                Log.d("HANS", "Speech Error: $error. Restarting listener.")
                restartListening()
            }

            override fun onResults(results: Bundle?) {
                val matches = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                if (!matches.isNullOrEmpty()) {
                    val spokenText = matches[0].lowercase()
                    Log.d("HANS", "Heard: $spokenText")

                    if (spokenText.contains(WAKE_WORD)) {
                        runOnUiThread { tvAiResponse.text = "Processing: $spokenText" }
                        sendToBackend(spokenText)

                        // Pause listening briefly while the LLM talks back
                        // (So it doesn't transcribe its own voice!)
                        // It will restart automatically after TTS finishes (see Step 4).
                    } else {
                        // Heard something, but no wake word. Restart immediately.
                        restartListening()
                    }
                } else {
                    restartListening()
                }
            }

            override fun onPartialResults(partialResults: Bundle?) {}
            override fun onEvent(eventType: Int, params: Bundle?) {}
        })

        // Start the continuous loop immediately
        startListeningMuted()
    }

    private fun restartListening() {
        // Prevent overlapping calls
        speechRecognizer.cancel()

        // Add a tiny delay to prevent the UI thread from locking up
        android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
            startListeningMuted()
        }, 100)
    }

    private fun startListeningMuted() {
        if (!::speechRecognizer.isInitialized) return

        // 1. Mute the system "Bloop" sound
        val currentVolume = audioManager.getStreamVolume(AudioManager.STREAM_MUSIC)
        audioManager.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_MUTE, 0)

        // 2. Start listening
        speechRecognizer.startListening(speechIntent)

        // 3. Unmute immediately after it starts
        android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
            audioManager.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_UNMUTE, 0)
        }, 300)
    }

    private fun toggleListening() {
        if (isListening) {
            speechRecognizer.stopListening()
            isListening = false
            btnToggleListen.text = "Start Listening"
        } else {
            speechRecognizer.startListening(speechIntent)
            isListening = true
            btnToggleListen.text = "Stop Listening"
        }
    }

    private fun sendToBackend(text: String) {
        val json = JSONObject()
        json.put("text", text)

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
                        val answer = jsonRes.optString("answer", "Done")

                        runOnUiThread {
                            tvAiResponse.text = "AI: $answer"

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