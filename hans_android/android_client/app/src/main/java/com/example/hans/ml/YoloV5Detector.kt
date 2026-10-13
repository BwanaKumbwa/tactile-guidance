package com.example.hans.ml

import android.content.Context
import android.graphics.Bitmap
import android.graphics.RectF
import android.os.SystemClock
import android.util.Log
import org.tensorflow.lite.Interpreter
import org.tensorflow.lite.gpu.GpuDelegate
import org.tensorflow.lite.nnapi.NnApiDelegate
import java.io.FileInputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.MappedByteBuffer
import java.nio.channels.FileChannel
import kotlin.math.max
import kotlin.math.min

data class Detection(
    val classId: Int,
    val label: String,
    val confidence: Float,
    val boundingBox: RectF
)

data class InferenceResult(
    val detections: List<Detection>,
    val preProcessTimeMs: Long,
    val inferenceTimeMs: Long,
    val postProcessTimeMs: Long,
    val hardwareDevice: String
) {
    val totalTimeMs: Long get() = preProcessTimeMs + inferenceTimeMs + postProcessTimeMs
}

class YoloV5Detector(
    private val context: Context,
    private val modelFileName: String = "yolov5s-fp16.tflite", // Use FP16 model
    private val labelFileName: String? = "coco_labels.txt",
    private val useGpu: Boolean = true,
    var confThreshold: Float = 0.35f,
    var iouThreshold: Float = 0.45f
) : AutoCloseable {

    companion object {
        private const val TAG = "YoloV5Detector"
        const val INPUT_SIZE = 640
        private const val BATCH_SIZE = 1
        private const val PIXEL_CHANNELS = 3
    }

    private var interpreter: Interpreter? = null
    private var gpuDelegate: GpuDelegate? = null
    private var nnApiDelegate: NnApiDelegate? = null
    private var labels: List<String> = emptyList()
    var activeHardware: String = "Uninitialized"
        private set

    private val inputByteBuffer: ByteBuffer = ByteBuffer.allocateDirect(
        BATCH_SIZE * INPUT_SIZE * INPUT_SIZE * PIXEL_CHANNELS * 4
    ).apply {
        order(ByteOrder.nativeOrder())
    }

    private val intValues = IntArray(INPUT_SIZE * INPUT_SIZE)

    init {
        loadLabels()
        setupInterpreter()
    }

    private fun loadLabels() {
        labels = if (!labelFileName.isNullOrEmpty()) {
            try {
                context.assets.open(labelFileName).bufferedReader().useLines { it.toList() }
            } catch (e: Exception) {
                Log.w(TAG, "Failed to load '$labelFileName', using fallback labels.", e)
                defaultCocoLabels()
            }
        } else {
            defaultCocoLabels()
        }
    }

    private fun setupInterpreter() {
        // Fallback to "yolov5s.tflite" if "yolov5s-fp16.tflite" was not found
        val actualModelName = try {
            context.assets.openFd(modelFileName).close()
            modelFileName
        } catch (e: Exception) {
            Log.w(TAG, "Model '$modelFileName' not found in assets, falling back to 'yolov5s.tflite'")
            "yolov5s.tflite"
        }

        val modelBuffer = loadModelFile(actualModelName)
        var lastErrorReason = ""

        // Strategy 1: Try GPU Delegate
        if (useGpu) {
            try {
                val delegate = GpuDelegate()
                val options = Interpreter.Options().apply {
                    addDelegate(delegate)
                }
                val inter = Interpreter(modelBuffer, options)
                interpreter = inter
                gpuDelegate = delegate
                activeHardware = "GPU (Accelerated)"
                Log.i(TAG, "✓ GPU Delegate attached successfully.")
            } catch (e: Throwable) {
                lastErrorReason = "GPU rejected: ${e.message?.take(30)}"
                Log.w(TAG, "GPU Delegate failed: ${e.message}", e)
                gpuDelegate?.close()
                gpuDelegate = null
                interpreter = null
            }
        }

        // Strategy 2: Try NNAPI Delegate (Pixel Tensor TPU)
        if (interpreter == null) {
            try {
                val nnapi = NnApiDelegate()
                val options = Interpreter.Options().apply {
                    addDelegate(nnapi)
                }
                val inter = Interpreter(modelBuffer, options)
                interpreter = inter
                nnApiDelegate = nnapi
                activeHardware = "Tensor TPU (NNAPI)"
                Log.i(TAG, "✓ Tensor TPU (NNAPI) attached successfully.")
            } catch (e: Throwable) {
                lastErrorReason = "TPU rejected: ${e.message?.take(30)}"
                Log.w(TAG, "NNAPI Delegate failed: ${e.message}", e)
                nnApiDelegate?.close()
                nnApiDelegate = null
                interpreter = null
            }
        }

        // Strategy 3: Multi-threaded CPU Fallback
        if (interpreter == null) {
            val cpuOptions = Interpreter.Options().apply {
                setNumThreads(4)
                setUseXNNPACK(true)
            }
            interpreter = Interpreter(modelBuffer, cpuOptions)
            activeHardware = "CPU ($lastErrorReason)"
            Log.i(TAG, "Running on CPU with XNNPACK.")
        }
    }

    fun detect(bitmap: Bitmap): InferenceResult {
        val inter = interpreter ?: throw IllegalStateException("Detector not initialized.")

        val startPre = SystemClock.uptimeMillis()
        val scaledBitmap = if (bitmap.width == INPUT_SIZE && bitmap.height == INPUT_SIZE) {
            bitmap
        } else {
            Bitmap.createScaledBitmap(bitmap, INPUT_SIZE, INPUT_SIZE, true)
        }

        bitmapToNormalizedByteBuffer(scaledBitmap, inputByteBuffer)
        if (scaledBitmap != bitmap) {
            scaledBitmap.recycle()
        }
        val preTime = SystemClock.uptimeMillis() - startPre

        val startInfer = SystemClock.uptimeMillis()
        val outputTensorShape = inter.getOutputTensor(0).shape()
        val numBoxes = outputTensorShape[1]
        val numFeatures = outputTensorShape[2]
        val rawOutput = Array(1) { Array(numBoxes) { FloatArray(numFeatures) } }

        inter.run(inputByteBuffer, rawOutput)
        val inferTime = SystemClock.uptimeMillis() - startInfer

        val startPost = SystemClock.uptimeMillis()
        val detections = processRawYoloOutputs(rawOutput[0], numFeatures)
        val postTime = SystemClock.uptimeMillis() - startPost

        return InferenceResult(detections, preTime, inferTime, postTime, activeHardware)
    }

    private fun bitmapToNormalizedByteBuffer(bitmap: Bitmap, byteBuffer: ByteBuffer) {
        byteBuffer.rewind()
        bitmap.getPixels(intValues, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)

        var pixelIndex = 0
        for (i in 0 until INPUT_SIZE) {
            for (j in 0 until INPUT_SIZE) {
                val pixel = intValues[pixelIndex++]
                val r = ((pixel shr 16) and 0xFF) / 255.0f
                val g = ((pixel shr 8) and 0xFF) / 255.0f
                val b = (pixel and 0xFF) / 255.0f

                byteBuffer.putFloat(r)
                byteBuffer.putFloat(g)
                byteBuffer.putFloat(b)
            }
        }
    }

    private fun processRawYoloOutputs(boxes: Array<FloatArray>, numFeatures: Int): List<Detection> {
        val candidates = mutableListOf<Detection>()
        val numClasses = numFeatures - 5

        // CRITICAL FIX: Auto-detect if coordinates are already normalized or in 0..640 pixel space
        val firstSample = boxes.firstOrNull { it[4] > 0.1f } ?: boxes[0]
        val isAlreadyNormalized = firstSample[0] <= 1.05f && firstSample[2] <= 1.05f
        val scaleFactor = if (isAlreadyNormalized) 1.0f else INPUT_SIZE.toFloat()

        for (row in boxes) {
            val objectness = row[4]
            if (objectness < confThreshold) continue

            var maxClassScore = 0f
            var maxClassId = -1
            for (c in 0 until numClasses) {
                val score = row[5 + c]
                if (score > maxClassScore) {
                    maxClassScore = score
                    maxClassId = c
                }
            }

            val confidence = objectness * maxClassScore
            if (confidence >= confThreshold) {
                val cx = row[0] / scaleFactor
                val cy = row[1] / scaleFactor
                val w = row[2] / scaleFactor
                val h = row[3] / scaleFactor

                val left = max(0f, cx - w / 2f)
                val top = max(0f, cy - h / 2f)
                val right = min(1f, cx + w / 2f)
                val bottom = min(1f, cy + h / 2f)

                candidates.add(
                    Detection(
                        classId = maxClassId,
                        label = labels.getOrElse(maxClassId) { "Class_$maxClassId" },
                        confidence = confidence,
                        boundingBox = RectF(left, top, right, bottom)
                    )
                )
            }
        }

        return nonMaxSuppression(candidates, iouThreshold)
    }

    private fun nonMaxSuppression(boxes: List<Detection>, iouLimit: Float): List<Detection> {
        val sorted = boxes.sortedByDescending { it.confidence }.toMutableList()
        val selected = mutableListOf<Detection>()

        while (sorted.isNotEmpty()) {
            val first = sorted.removeAt(0)
            selected.add(first)

            val iterator = sorted.iterator()
            while (iterator.hasNext()) {
                val next = iterator.next()
                if (next.classId == first.classId && calculateIoU(first.boundingBox, next.boundingBox) > iouLimit) {
                    iterator.remove()
                }
            }
        }
        return selected
    }

    private fun calculateIoU(a: RectF, b: RectF): Float {
        val interLeft = max(a.left, b.left)
        val interTop = max(a.top, b.top)
        val interRight = min(a.right, b.right)
        val interBottom = min(a.bottom, b.bottom)

        val interArea = max(0f, interRight - interLeft) * max(0f, interBottom - interTop)
        val unionArea = (a.width() * a.height()) + (b.width() * b.height()) - interArea

        return if (unionArea <= 0f) 0f else interArea / unionArea
    }

    private fun loadModelFile(modelName: String): MappedByteBuffer {
        val assetFileDescriptor = context.assets.openFd(modelName)
        val inputStream = FileInputStream(assetFileDescriptor.fileDescriptor)
        val fileChannel = inputStream.channel
        val startOffset = assetFileDescriptor.startOffset
        val declaredLength = assetFileDescriptor.declaredLength
        return fileChannel.map(FileChannel.MapMode.READ_ONLY, startOffset, declaredLength)
    }

    override fun close() {
        interpreter?.close()
        interpreter = null
        gpuDelegate?.close()
        gpuDelegate = null
        nnApiDelegate?.close()
        nnApiDelegate = null
    }

    private fun defaultCocoLabels() = listOf(
        "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
        "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
        "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
        "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
        "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
        "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
        "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
        "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote",
        "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book",
        "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush"
    )
}
