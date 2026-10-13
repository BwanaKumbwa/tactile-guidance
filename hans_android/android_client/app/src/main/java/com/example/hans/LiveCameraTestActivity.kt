package com.example.hans

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Color
import android.os.Bundle
import android.os.SystemClock
import android.util.Log
import android.util.Size
import android.view.Gravity
import android.view.View
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.SeekBar
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import com.example.hans.ml.BoundingBoxOverlay
import com.example.hans.ml.DualYoloPipeline
import com.example.hans.ml.tracker.SortTracker
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

class LiveCameraTestActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "LiveCameraTest"
    }

    private lateinit var previewView: PreviewView
    private lateinit var overlayView: BoundingBoxOverlay
    private lateinit var tvStatus: TextView
    private lateinit var tvConfLabel: TextView
    private lateinit var seekConf: SeekBar

    private var dualPipeline: DualYoloPipeline? = null
    // Multi-object Kalman tracker matching server-side StrongSORT logic
    private val tracker = SortTracker(maxAge = 30, minHits = 2, iouThreshold = 0.30f)

    private lateinit var cameraExecutor: ExecutorService
    private val isProcessingFrame = AtomicBoolean(false)

    private var lastFpsTimestamp = 0L
    private var frameCounter = 0
    private var currentFps = 0

    private val cameraPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { isGranted ->
        if (isGranted) {
            startCamera()
        } else {
            Toast.makeText(this, "Camera permission required for live testing", Toast.LENGTH_LONG).show()
            finish()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(buildLayout())

        cameraExecutor = Executors.newSingleThreadExecutor()
        initDetector()

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            startCamera()
        } else {
            cameraPermissionLauncher.launch(Manifest.permission.CAMERA)
        }
    }

    private fun initDetector() {
        cameraExecutor.execute {
            try {
                dualPipeline = DualYoloPipeline(
                    context = this,
                    objModelFileName = "yolov5s-fp16.tflite",
                    handModelFileName = "hand-fp16.tflite",
                    objLabelsFileName = "coco_labels.txt",
                    handLabelsFileName = "hand_labels.txt",
                    confThreshold = 0.35f,
                    iouThreshold = 0.45f
                )
                runOnUiThread {
                    tvStatus.text =
                        "Dual Pipeline Ready [${dualPipeline?.activeHardware}]. Starting camera..."
                }
            } catch (e: Exception) {
                Log.e(TAG, "Failed to initialize dual pipeline", e)
                runOnUiThread {
                    tvStatus.text = "Pipeline Error: ${e.message}"
                }
            }
        }
    }

    private fun startCamera() {
        val cameraProviderFuture = ProcessCameraProvider.getInstance(this)

        cameraProviderFuture.addListener({
            val cameraProvider = cameraProviderFuture.get()

            val preview = Preview.Builder()
                .build()
                .also {
                    it.setSurfaceProvider(previewView.surfaceProvider)
                }

            val imageAnalyzer = ImageAnalysis.Builder()
                .setTargetResolution(Size(640, 640))
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
                .build()
                .also {
                    it.setAnalyzer(cameraExecutor) { imageProxy ->
                        processImageProxy(imageProxy)
                    }
                }

            val cameraSelector = CameraSelector.DEFAULT_BACK_CAMERA

            try {
                cameraProvider.unbindAll()
                cameraProvider.bindToLifecycle(this, cameraSelector, preview, imageAnalyzer)
                Log.d(TAG, "Camera bound successfully")
            } catch (e: Exception) {
                Log.e(TAG, "Camera binding failed", e)
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun processImageProxy(imageProxy: ImageProxy) {
        val pipeline = dualPipeline
        if (pipeline == null || !isProcessingFrame.compareAndSet(false, true)) {
            imageProxy.close()
            return
        }

        try {
            val rotationDegrees = imageProxy.imageInfo.rotationDegrees
            val bitmap = imageProxy.toBitmap()

            val orientedBitmap = if (rotationDegrees != 0) {
                val matrix = android.graphics.Matrix().apply {
                    postRotate(rotationDegrees.toFloat())
                }
                Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, matrix, true)
            } else {
                bitmap
            }

            // 1. Concurrent dual-model inference
            val result = pipeline.detect(orientedBitmap)

            // 2. Kalman multi-object tracking association (< 1ms execution)
            val trackedDetections = tracker.update(result.mergedDetections)

            frameCounter++
            val now = SystemClock.uptimeMillis()
            if (now - lastFpsTimestamp >= 1000) {
                currentFps = frameCounter
                frameCounter = 0
                lastFpsTimestamp = now
            }

            runOnUiThread {
                // Update overlay with tracked detections containing persistent track IDs
                // handIdThreshold = 80 matches the server offset: index_add = len(names_obj)
                overlayView.setTrackedResults(
                    trackedDetections,
                    orientedBitmap.width,
                    orientedBitmap.height,
                    handIdThreshold = 80
                )

                val summary = "[${result.hardwareDevice}]  FPS: $currentFps\n" +
                        "Infer: ${result.inferenceTimeMs}ms | Post: ${result.postprocessTimeMs}ms\n" +
                        "Active Tracks: ${trackedDetections.size} (Objects: ${result.objectDetections.size}, Hands: ${result.handDetections.size})"
                tvStatus.text = summary
            }

            if (orientedBitmap != bitmap) {
                orientedBitmap.recycle()
            }
        } catch (e: Exception) {
            Log.e(TAG, "Inference loop error", e)
        } finally {
            imageProxy.close()
            isProcessingFrame.set(false)
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        cameraExecutor.shutdown()
        dualPipeline?.close()
        tracker.clear()
    }

    private fun buildLayout(): View {
        val root = FrameLayout(this).apply {
            setBackgroundColor(Color.BLACK)
        }

        // 1. Camera Preview using COMPATIBLE mode (TextureView so overlay can render on top)
        previewView = PreviewView(this).apply {
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
            )
            scaleType = PreviewView.ScaleType.FILL_CENTER
            implementationMode = PreviewView.ImplementationMode.COMPATIBLE
        }
        root.addView(previewView)

        // 2. Bounding Box & Track ID Overlay
        overlayView = BoundingBoxOverlay(this).apply {
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
            )
            elevation = 50f
        }
        root.addView(overlayView)

        // 3. Top HUD (Performance & Track Stats)
        val topHud = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 64, 32, 24)
            setBackgroundColor(Color.parseColor("#99000000"))
            elevation = 60f
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT
            ).apply {
                gravity = Gravity.TOP
            }
        }

        tvStatus = TextView(this).apply {
            text = "Starting live inference & tracking..."
            setTextColor(Color.WHITE)
            textSize = 14f
        }
        topHud.addView(tvStatus)
        root.addView(topHud)

        // 4. Bottom Controls (Confidence Slider)
        val bottomControls = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 20, 32, 40)
            setBackgroundColor(Color.parseColor("#99000000"))
            elevation = 60f
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT
            ).apply {
                gravity = Gravity.BOTTOM
            }
        }

        tvConfLabel = TextView(this).apply {
            text = "Confidence Threshold: 0.35"
            setTextColor(Color.WHITE)
            textSize = 12f
        }
        bottomControls.addView(tvConfLabel)

        seekConf = SeekBar(this).apply {
            max = 100
            progress = 35
            setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
                override fun onProgressChanged(seekBar: SeekBar?, progress: Int, fromUser: Boolean) {
                    val threshold = progress / 100f
                    tvConfLabel.text = "Confidence Threshold: ${String.format("%.2f", threshold)}"
                    dualPipeline?.confThreshold = threshold
                }
                override fun onStartTrackingTouch(seekBar: SeekBar?) {}
                override fun onStopTrackingTouch(seekBar: SeekBar?) {}
            })
        }
        bottomControls.addView(seekConf)
        root.addView(bottomControls)

        overlayView.bringToFront()
        topHud.bringToFront()
        bottomControls.bringToFront()

        return root
    }
}
