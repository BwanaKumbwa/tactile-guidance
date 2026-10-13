package com.example.hans

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import android.provider.MediaStore
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.SeekBar
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.example.hans.ml.Detection
import com.example.hans.ml.YoloV5Detector
import java.util.concurrent.Executors

class ModelTestActivity : AppCompatActivity() {

    private lateinit var ivPreview: ImageView
    private lateinit var tvMetrics: TextView
    private lateinit var btnSynthetic: Button
    private lateinit var btnCamera: Button
    private lateinit var btnGallery: Button
    private lateinit var btnBenchmark: Button
    private lateinit var seekConf: SeekBar
    private lateinit var tvConfLabel: TextView
    private lateinit var progressBar: ProgressBar

    private var detector: YoloV5Detector? = null
    private var currentBitmap: Bitmap? = null
    private val backgroundExecutor = Executors.newSingleThreadExecutor()

    // 1. Live Camera Snapshot Contract
    private val takePictureLauncher = registerForActivityResult(
        ActivityResultContracts.TakePicturePreview()
    ) { bitmap: Bitmap? ->
        if (bitmap != null) {
            currentBitmap = bitmap
            runInference(bitmap)
        } else {
            Toast.makeText(this, "Camera capture cancelled", Toast.LENGTH_SHORT).show()
        }
    }

    // Camera Permission Request Contract
    private val requestCameraPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { isGranted: Boolean ->
        if (isGranted) {
            takePictureLauncher.launch(null)
        } else {
            Toast.makeText(this, "Camera permission is required to snap photos", Toast.LENGTH_SHORT).show()
        }
    }

    // 2. Photo Gallery Picker Contract
    private val galleryLauncher = registerForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri: Uri? ->
        uri?.let { loadBitmapFromUri(it) }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(buildLayout())

        initDetector()
        setupListeners()

        // Generate an initial synthetic frame immediately so the UI isn't blank
        generateSyntheticTestImage()
    }

    private fun initDetector() {
        progressBar.visibility = View.VISIBLE
        backgroundExecutor.execute {
            try {
                // Instantiates detector with GPU delegate enabled
                detector = YoloV5Detector(
                    context = this,
                    modelFileName = "yolov5s.tflite",
                    labelFileName = "coco_labels.txt",
                    useGpu = true,
                    confThreshold = 0.35f,
                    iouThreshold = 0.45f
                )
                runOnUiThread {
                    progressBar.visibility = View.GONE
                    tvMetrics.text = "✓ Detector loaded successfully on GPU/NPU.\nSelect an input source below to test."
                }
            } catch (e: Exception) {
                runOnUiThread {
                    progressBar.visibility = View.GONE
                    tvMetrics.text = "Initialization Error: ${e.message}\nMake sure 'yolov5s.tflite' exists in src/main/assets/"
                }
            }
        }
    }

    private fun setupListeners() {
        btnSynthetic.setOnClickListener {
            generateSyntheticTestImage()
        }

        btnCamera.setOnClickListener {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
                takePictureLauncher.launch(null)
            } else {
                requestCameraPermissionLauncher.launch(Manifest.permission.CAMERA)
            }
        }

        btnGallery.setOnClickListener {
            galleryLauncher.launch("image/*")
        }

        btnBenchmark.setOnClickListener {
            runLatencyBenchmark(iterations = 20)
        }

        seekConf.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(seekBar: SeekBar?, progress: Int, fromUser: Boolean) {
                val threshold = progress / 100f
                tvConfLabel.text = "Confidence Threshold: ${String.format("%.2f", threshold)}"
                detector?.confThreshold = threshold
                currentBitmap?.let { runInference(it) }
            }

            override fun onStartTrackingTouch(seekBar: SeekBar?) {}
            override fun onStopTrackingTouch(seekBar: SeekBar?) {}
        })
    }

    private fun loadBitmapFromUri(uri: Uri) {
        try {
            @Suppress("DEPRECATION")
            val bitmap = MediaStore.Images.Media.getBitmap(contentResolver, uri)
            currentBitmap = bitmap
            runInference(bitmap)
        } catch (e: Exception) {
            Toast.makeText(this, "Failed to load image: ${e.message}", Toast.LENGTH_SHORT).show()
        }
    }

    private fun generateSyntheticTestImage() {
        val size = 640
        val bmp = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bmp)

        // Draw neutral backdrop
        canvas.drawColor(Color.rgb(230, 230, 235))

        val paint = Paint().apply {
            color = Color.DKGRAY
            style = Paint.Style.FILL
            isAntiAlias = true
        }

        // Draw synthetic test shapes
        canvas.drawRoundRect(220f, 160f, 420f, 520f, 24f, 24f, paint)

        paint.color = Color.rgb(60, 60, 60)
        paint.textSize = 28f
        canvas.drawText("Synthetic Frame (Waiting for image)", 40f, 80f, paint)

        currentBitmap = bmp
        runInference(bmp)
    }

    private fun runInference(bitmap: Bitmap) {
        val det = detector ?: return
        progressBar.visibility = View.VISIBLE

        backgroundExecutor.execute {
            try {
                val result = det.detect(bitmap)

                // Render bounding boxes onto visual copy
                val visualized = renderDetections(bitmap, result.detections)

                runOnUiThread {
                    progressBar.visibility = View.GONE
                    ivPreview.setImageBitmap(visualized)

                    val summary = buildString {
                        append("Detections: ${result.detections.size} | ")
                        append("Total: ${result.totalTimeMs}ms (${if (result.totalTimeMs > 0) 1000 / result.totalTimeMs else 0} FPS)\n")
                        append("Pre: ${result.preProcessTimeMs}ms | ")
                        append("Infer: ${result.inferenceTimeMs}ms | ")
                        append("Post: ${result.postProcessTimeMs}ms\n")

                        if (result.detections.isNotEmpty()) {
                            append("Found: ")
                            val topSummary = result.detections.take(4).joinToString(", ") {
                                "${it.label} (${(it.confidence * 100).toInt()}%)"
                            }
                            append(topSummary)
                        } else {
                            append("No objects detected above threshold.")
                        }
                    }
                    tvMetrics.text = summary
                }
            } catch (e: Exception) {
                runOnUiThread {
                    progressBar.visibility = View.GONE
                    tvMetrics.text = "Inference Error: ${e.message}"
                }
            }
        }
    }

    private fun runLatencyBenchmark(iterations: Int) {
        val det = detector ?: return
        val bmp = currentBitmap ?: return

        progressBar.visibility = View.VISIBLE
        tvMetrics.text = "Running $iterations-iteration benchmark on Tensor GPU/NPU..."

        backgroundExecutor.execute {
            // Warm-up runs to stabilize GPU frequency scaling
            repeat(3) { det.detect(bmp) }

            var totalPre = 0L
            var totalInfer = 0L
            var totalPost = 0L

            for (i in 1..iterations) {
                val res = det.detect(bmp)
                totalPre += res.preProcessTimeMs
                totalInfer += res.inferenceTimeMs
                totalPost += res.postProcessTimeMs
            }

            val avgPre = totalPre / iterations
            val avgInfer = totalInfer / iterations
            val avgPost = totalPost / iterations
            val avgTotal = avgPre + avgInfer + avgPost
            val fps = if (avgTotal > 0) 1000 / avgTotal else 0

            runOnUiThread {
                progressBar.visibility = View.GONE
                tvMetrics.text = """
                    ⚡ SUSTAINED BENCHMARK (N=$iterations iterations):
                    • Preprocessing (Scale/RGB): ${avgPre}ms
                    • Model Execution (TFLite GPU): ${avgInfer}ms
                    • Postprocessing (Boxes/NMS): ${avgPost}ms
                    • Average End-to-End Latency: ${avgTotal}ms
                    • Max Achievable Throughput: ~$fps FPS
                """.trimIndent()
            }
        }
    }

    private fun renderDetections(original: Bitmap, detections: List<Detection>): Bitmap {
        val output = original.copy(Bitmap.Config.ARGB_8888, true)
        val canvas = Canvas(output)

        val boxPaint = Paint().apply {
            style = Paint.Style.STROKE
            strokeWidth = 5f
            color = Color.rgb(0, 230, 118) // Bright green
            isAntiAlias = true
        }

        val textBgPaint = Paint().apply {
            style = Paint.Style.FILL
            color = Color.parseColor("#D9000000") // Translucent black
        }

        val textPaint = Paint().apply {
            color = Color.WHITE
            textSize = 28f
            typeface = Typeface.DEFAULT_BOLD
            isAntiAlias = true
        }

        val w = output.width.toFloat()
        val h = output.height.toFloat()

        for (det in detections) {
            val rect = RectF(
                det.boundingBox.left * w,
                det.boundingBox.top * h,
                det.boundingBox.right * w,
                det.boundingBox.bottom * h
            )
            canvas.drawRect(rect, boxPaint)

            val label = "${det.label} ${(det.confidence * 100).toInt()}%"
            val textWidth = textPaint.measureText(label)
            val textHeight = 32f

            val labelTop = (rect.top - textHeight - 10f).coerceAtLeast(0f)
            canvas.drawRect(
                rect.left,
                labelTop,
                rect.left + textWidth + 16f,
                labelTop + textHeight + 10f,
                textBgPaint
            )
            canvas.drawText(label, rect.left + 8f, labelTop + textHeight, textPaint)
        }

        return output
    }

    override fun onDestroy() {
        super.onDestroy()
        detector?.close()
        backgroundExecutor.shutdown()
    }

    // Programmatic Layout (Zero XML dependency)
    private fun buildLayout(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 48, 32, 24)
            setBackgroundColor(Color.parseColor("#121212"))
        }

        val title = TextView(this).apply {
            text = "YOLOv5s On-Device Test Harness"
            textSize = 20f
            setTextColor(Color.WHITE)
            setTypeface(null, Typeface.BOLD)
        }
        root.addView(title)

        tvMetrics = TextView(this).apply {
            text = "Initializing detector..."
            textSize = 13f
            setTextColor(Color.parseColor("#B0BEC5"))
            setPadding(0, 16, 0, 12)
        }
        root.addView(tvMetrics)

        progressBar = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            isIndeterminate = true
            visibility = View.GONE
        }
        root.addView(progressBar)

        ivPreview = ImageView(this).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                0,
                1.0f
            )
            setBackgroundColor(Color.BLACK)
            scaleType = ImageView.ScaleType.FIT_CENTER
        }
        root.addView(ivPreview)

        tvConfLabel = TextView(this).apply {
            text = "Confidence Threshold: 0.35"
            setTextColor(Color.WHITE)
            textSize = 12f
            setPadding(0, 12, 0, 4)
        }
        root.addView(tvConfLabel)

        seekConf = SeekBar(this).apply {
            max = 100
            progress = 35
        }
        root.addView(seekConf)

        val buttonBar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            setPadding(0, 12, 0, 0)
        }

        btnSynthetic = Button(this).apply { text = "Synth" }
        btnCamera = Button(this).apply { text = "Camera" }
        btnGallery = Button(this).apply { text = "Gallery" }
        btnBenchmark = Button(this).apply { text = "Benchmark" }

        val btnParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1.0f).apply {
            setMargins(4, 0, 4, 0)
        }

        buttonBar.addView(btnSynthetic, btnParams)
        buttonBar.addView(btnCamera, btnParams)
        buttonBar.addView(btnGallery, btnParams)
        buttonBar.addView(btnBenchmark, btnParams)

        root.addView(buttonBar)
        return root
    }
}
