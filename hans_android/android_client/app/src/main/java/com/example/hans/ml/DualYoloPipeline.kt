package com.example.hans.ml

import android.content.Context
import android.graphics.Bitmap
import android.graphics.RectF
import android.os.SystemClock
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.runBlocking
import org.tensorflow.lite.Interpreter
import org.tensorflow.lite.gpu.GpuDelegate
import java.io.FileInputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.MappedByteBuffer
import java.nio.channels.FileChannel
import kotlin.math.max
import kotlin.math.min

data class DualInferenceResult(
    val objectDetections: List<Detection>,
    val handDetections: List<Detection>,
    val mergedDetections: List<Detection>,
    val preprocessTimeMs: Long,
    val inferenceTimeMs: Long,
    val postprocessTimeMs: Long,
    val hardwareDevice: String
) {
    val totalTimeMs: Long get() = preprocessTimeMs + inferenceTimeMs + postprocessTimeMs
}

class DualYoloPipeline(
    private val context: Context,
    private val objModelFileName: String = "yolov5s-fp16.tflite",
    private val handModelFileName: String = "hand-fp16.tflite",
    private val objLabelsFileName: String? = "coco_labels.txt",
    private val handLabelsFileName: String? = "hand_labels.txt",
    var confThreshold: Float = 0.35f,
    var iouThreshold: Float = 0.45f
) : AutoCloseable {

    companion object {
        private const val TAG = "DualYoloPipeline"
        const val INPUT_SIZE = 640
        private const val BATCH_SIZE = 1
        private const val PIXEL_CHANNELS = 3
    }

    private var objInterpreter: Interpreter? = null
    private var handInterpreter: Interpreter? = null
    private var objGpuDelegate: GpuDelegate? = null
    private var handGpuDelegate: GpuDelegate? = null

    private var objLabels: List<String> = emptyList()
    private var handLabels: List<String> = emptyList()
    val masterLabels: MutableMap<Int, String> = mutableMapOf()

    var activeHardware: String = "Uninitialized"
        private set

    // Pre-allocated direct ByteBuffers for concurrent thread safety
    val objInputBuffer: ByteBuffer = ByteBuffer.allocateDirect(
        BATCH_SIZE * INPUT_SIZE * INPUT_SIZE * PIXEL_CHANNELS * 4
    ).apply { order(ByteOrder.nativeOrder()) }

    val handInputBuffer: ByteBuffer = ByteBuffer.allocateDirect(
        BATCH_SIZE * INPUT_SIZE * INPUT_SIZE * PIXEL_CHANNELS * 4
    ).apply { order(ByteOrder.nativeOrder()) }

    private val intValues = IntArray(INPUT_SIZE * INPUT_SIZE)

    init {
        loadLabels()
        setupInterpreters()
    }

    private fun loadLabels() {
        objLabels = loadLabelFile(objLabelsFileName) ?: defaultCocoLabels()
        handLabels = loadLabelFile(handLabelsFileName) ?: defaultHandLabels()

        objLabels.forEachIndexed { idx, label -> masterLabels[idx] = label }
        val indexAdd = objLabels.size
        handLabels.forEachIndexed { idx, label -> masterLabels[idx + indexAdd] = label }

        Log.i(TAG, "Master labels mapped: ${masterLabels.size} classes (Object: ${objLabels.size}, Hand: ${handLabels.size})")
    }

    private fun loadLabelFile(fileName: String?): List<String>? {
        if (fileName.isNullOrEmpty()) return null
        return try {
            context.assets.open(fileName).bufferedReader().useLines { it.toList() }
        } catch (e: Exception) {
            Log.w(TAG, "Could not open '$fileName', using defaults.", e)
            null
        }
    }

    private fun setupInterpreters() {
        val resolvedObjName = resolveAssetPath(objModelFileName, "yolov5s.tflite")
        val resolvedHandName = resolveAssetPath(handModelFileName, "hand.tflite")

        val objBuffer = loadModelBuffer(resolvedObjName)
        val handBuffer = loadModelBuffer(resolvedHandName)

        var gpuSuccess = false

        try {
            val objDelegate = GpuDelegate()
            val handDelegate = GpuDelegate()

            val objOpts = Interpreter.Options().apply { addDelegate(objDelegate) }
            val handOpts = Interpreter.Options().apply { addDelegate(handDelegate) }

            objInterpreter = Interpreter(objBuffer, objOpts)
            handInterpreter = Interpreter(handBuffer, handOpts)

            objGpuDelegate = objDelegate
            handGpuDelegate = handDelegate
            gpuSuccess = true
            activeHardware = "GPU (Accelerated Parallel)"
            Log.i(TAG, "✓ Both models loaded on GPU.")
        } catch (e: Throwable) {
            Log.w(TAG, "GPU initialization failed, falling back to multi-threaded CPU: ${e.message}")
            objGpuDelegate?.close()
            handGpuDelegate?.close()
            objGpuDelegate = null
            handGpuDelegate = null
        }

        if (!gpuSuccess) {
            val cpuOptions = Interpreter.Options().apply {
                setNumThreads(4)
                setUseXNNPACK(true)
            }
            objInterpreter = Interpreter(objBuffer, cpuOptions)
            handInterpreter = Interpreter(handBuffer, cpuOptions)
            activeHardware = "CPU (XNNPACK Parallel)"
            Log.i(TAG, "✓ Both models loaded on CPU with XNNPACK.")
        }
    }

    private fun resolveAssetPath(preferred: String, fallback: String): String {
        return try {
            context.assets.openFd(preferred).close()
            preferred
        } catch (e: Exception) {
            fallback
        }
    }

    /**
     * COMPATIBILITY METHOD FOR TEST ACTIVITIES:
     * Accepts a standard Bitmap, normalizes it into the direct buffers, and runs inference.
     */
    fun detect(bitmap: Bitmap): DualInferenceResult {
        val startPre = SystemClock.uptimeMillis()
        val scaledBitmap = if (bitmap.width == INPUT_SIZE && bitmap.height == INPUT_SIZE) {
            bitmap
        } else {
            Bitmap.createScaledBitmap(bitmap, INPUT_SIZE, INPUT_SIZE, true)
        }

        bitmapToNormalizedByteBuffer(scaledBitmap, objInputBuffer)

        if (scaledBitmap != bitmap) {
            scaledBitmap.recycle()
        }

        return detectDirect(startPre)
    }

    /**
     * ZERO-COPY DIRECT INFERENCE:
     * Expects objInputBuffer to already be filled by CameraImageConverter.yuvToNormalizedRgbBuffer().
     */
    fun detectDirect(preStartTime: Long = SystemClock.uptimeMillis()): DualInferenceResult = runBlocking {
        val objInter = objInterpreter ?: throw IllegalStateException("Object model uninitialized")
        val handInter = handInterpreter ?: throw IllegalStateException("Hand model uninitialized")

        // Fast native memory copy from objInputBuffer to handInputBuffer
        objInputBuffer.rewind()
        handInputBuffer.rewind()
        handInputBuffer.put(objInputBuffer)
        objInputBuffer.rewind()
        handInputBuffer.rewind()

        val preTime = SystemClock.uptimeMillis() - preStartTime

        // Parallel Inference Execution
        val startInfer = SystemClock.uptimeMillis()

        val objTensorShape = objInter.getOutputTensor(0).shape()
        val handTensorShape = handInter.getOutputTensor(0).shape()

        val rawObjOutput = Array(1) { Array(objTensorShape[1]) { FloatArray(objTensorShape[2]) } }
        val rawHandOutput = Array(1) { Array(handTensorShape[1]) { FloatArray(handTensorShape[2]) } }

        val objJob = async(Dispatchers.Default) {
            objInter.run(objInputBuffer, rawObjOutput)
        }
        val handJob = async(Dispatchers.Default) {
            handInter.run(handInputBuffer, rawHandOutput)
        }

        objJob.await()
        handJob.await()
        val inferTime = SystemClock.uptimeMillis() - startInfer

        // Postprocessing & Output Normalization
        val startPost = SystemClock.uptimeMillis()
        val indexAdd = objLabels.size

        val objDetections = processRawOutputs(rawObjOutput[0], objTensorShape[2], offsetClassId = 0, labels = objLabels)
        val handDetections = processRawOutputs(rawHandOutput[0], handTensorShape[2], offsetClassId = indexAdd, labels = handLabels)

        val merged = ArrayList<Detection>(objDetections.size + handDetections.size).apply {
            addAll(objDetections)
            addAll(handDetections)
        }

        val postTime = SystemClock.uptimeMillis() - startPost

        DualInferenceResult(
            objectDetections = objDetections,
            handDetections = handDetections,
            mergedDetections = merged,
            preprocessTimeMs = preTime,
            inferenceTimeMs = inferTime,
            postprocessTimeMs = postTime,
            hardwareDevice = activeHardware
        )
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

    private fun processRawOutputs(
        boxes: Array<FloatArray>,
        numFeatures: Int,
        offsetClassId: Int,
        labels: List<String>
    ): List<Detection> {
        val candidates = mutableListOf<Detection>()
        val numClasses = numFeatures - 5

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

            if (offsetClassId == 0 && maxClassId == 0) {
                continue // Filter 'person'
            }

            val confidence = objectness * maxClassScore
            if (confidence >= confThreshold && maxClassId != -1) {
                val cx = row[0] / scaleFactor
                val cy = row[1] / scaleFactor
                val w = row[2] / scaleFactor
                val h = row[3] / scaleFactor

                val left = max(0f, cx - w / 2f)
                val top = max(0f, cy - h / 2f)
                val right = min(1f, cx + w / 2f)
                val bottom = min(1f, cy + h / 2f)

                val finalClassId = maxClassId + offsetClassId
                val labelName = masterLabels[finalClassId] ?: labels.getOrElse(maxClassId) { "Class_$finalClassId" }

                candidates.add(
                    Detection(
                        classId = finalClassId,
                        label = labelName,
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

    private fun loadModelBuffer(modelName: String): MappedByteBuffer {
        val assetFileDescriptor = context.assets.openFd(modelName)
        val inputStream = FileInputStream(assetFileDescriptor.fileDescriptor)
        val fileChannel = inputStream.channel
        val startOffset = assetFileDescriptor.startOffset
        val declaredLength = assetFileDescriptor.declaredLength
        return fileChannel.map(FileChannel.MapMode.READ_ONLY, startOffset, declaredLength)
    }

    override fun close() {
        objInterpreter?.close()
        handInterpreter?.close()
        objGpuDelegate?.close()
        handGpuDelegate?.close()
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

    private fun defaultHandLabels() = listOf(
        "my_left", "my_right", "your_left", "your_right"
    )
}
