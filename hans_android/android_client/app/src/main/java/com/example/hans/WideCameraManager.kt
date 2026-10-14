package com.example.hans

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageFormat
import android.graphics.Matrix
import android.hardware.camera2.*
import android.hardware.camera2.params.OutputConfiguration
import android.hardware.camera2.params.SessionConfiguration
import android.media.ImageReader
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import kotlinx.coroutines.suspendCancellableCoroutine
import java.util.concurrent.Executors
import java.util.concurrent.Semaphore
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import kotlin.coroutines.resume

class WideCameraManager(private val context: Context) {

    companion object {
        private const val TAG = "WideCameraManager"
        private const val CAPTURE_TIMEOUT_MS = 3500L
        // Skip first 6 cold frames (~200ms) to allow Auto-Exposure and AWB to converge
        private const val WARMUP_FRAMES_TO_SKIP = 6
    }

    private val cameraManager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
    private val cameraLock = Semaphore(1)

    data class UltraWideTarget(
        val logicalCameraId: String,
        val physicalCameraId: String?,
        val minZoomRatio: Float
    )

    fun findUltraWideTarget(): UltraWideTarget? {
        try {
            for (logicalId in cameraManager.cameraIdList) {
                val chars = cameraManager.getCameraCharacteristics(logicalId)
                val facing = chars.get(CameraCharacteristics.LENS_FACING)

                if (facing == CameraCharacteristics.LENS_FACING_BACK) {
                    var targetPhysicalId: String? = null
                    var lowestFocal = Float.MAX_VALUE

                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                        for (physId in chars.physicalCameraIds) {
                            try {
                                val physChars = cameraManager.getCameraCharacteristics(physId)
                                val focals = physChars.get(CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS)
                                val focal = focals?.firstOrNull() ?: Float.MAX_VALUE

                                if (focal <= 2.8f && focal < lowestFocal) {
                                    lowestFocal = focal
                                    targetPhysicalId = physId
                                }
                            } catch (e: Exception) {
                                Log.w(TAG, "Failed reading physical ID $physId", e)
                            }
                        }
                    }

                    val zoomRange = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                        chars.get(CameraCharacteristics.CONTROL_ZOOM_RATIO_RANGE)
                    } else null
                    val minZoom = zoomRange?.lower ?: 1.0f

                    Log.i(TAG, "Resolved Target: Logical '$logicalId', Physical '${targetPhysicalId ?: "None"}', MinZoom: ${minZoom}x")
                    return UltraWideTarget(logicalId, targetPhysicalId, minZoom)
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error resolving ultra-wide target", e)
        }
        return null
    }

    @SuppressLint("MissingPermission")
    suspend fun captureWideSnapshot(): Bitmap? = suspendCancellableCoroutine { continuation ->
        val target = findUltraWideTarget()
        if (target == null) {
            Log.w(TAG, "No back camera available.")
            continuation.resume(null)
            return@suspendCancellableCoroutine
        }

        val thread = HandlerThread("WideCaptureThread").apply { start() }
        val handler = Handler(thread.looper)

        val isFinished = AtomicBoolean(false)
        val frameCounter = AtomicInteger(0)

        var cameraDevice: CameraDevice? = null
        var captureSession: CameraCaptureSession? = null
        var imageReader: ImageReader? = null

        fun cleanup(bitmap: Bitmap?) {
            if (isFinished.compareAndSet(false, true)) {
                try {
                    captureSession?.stopRepeating()
                    captureSession?.close()
                    cameraDevice?.close()
                    imageReader?.close()
                } catch (e: Exception) {
                    Log.w(TAG, "Error during cleanup", e)
                }

                handler.postDelayed({
                    thread.quitSafely()
                    cameraLock.release()
                }, 200)

                if (continuation.isActive) {
                    continuation.resume(bitmap)
                }
            }
        }

        if (!cameraLock.tryAcquire()) {
            Log.w(TAG, "Camera lock busy, skipping capture")
            thread.quitSafely()
            continuation.resume(null)
            return@suspendCancellableCoroutine
        }

        handler.postDelayed({
            if (!isFinished.get()) {
                Log.w(TAG, "Wide capture timed out")
                cleanup(null)
            }
        }, CAPTURE_TIMEOUT_MS)

        try {
            // Allocate larger buffer so warmup frames can be processed without stalling
            imageReader = ImageReader.newInstance(1280, 720, ImageFormat.JPEG, 4)
            imageReader.setOnImageAvailableListener({ reader ->
                val image = reader.acquireLatestImage() ?: return@setOnImageAvailableListener
                val currentCount = frameCounter.incrementAndGet()

                // Skip the first N frames to let Auto-Exposure and Auto-White-Balance settle
                if (currentCount <= WARMUP_FRAMES_TO_SKIP) {
                    image.close()
                    Log.d(TAG, "Warming up AE/AWB: frame $currentCount / $WARMUP_FRAMES_TO_SKIP")
                    return@setOnImageAvailableListener
                }

                // Properly exposed frame reached
                try {
                    val buffer = image.planes[0].buffer
                    val bytes = ByteArray(buffer.remaining())
                    buffer.get(bytes)

                    val rawBitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                    val matrix = Matrix().apply { postRotate(90f) }
                    val orientedBitmap = Bitmap.createBitmap(
                        rawBitmap, 0, 0, rawBitmap.width, rawBitmap.height, matrix, true
                    )
                    Log.i(TAG, "✓ Frame $currentCount captured with converged exposure!")
                    cleanup(orientedBitmap)
                } catch (e: Exception) {
                    Log.e(TAG, "Error decoding image buffer", e)
                    cleanup(null)
                } finally {
                    image.close()
                }
            }, handler)

            cameraManager.openCamera(target.logicalCameraId, object : CameraDevice.StateCallback() {
                override fun onOpened(camera: CameraDevice) {
                    cameraDevice = camera
                    try {
                        val surface = imageReader.surface

                        val sessionCallback = object : CameraCaptureSession.StateCallback() {
                            override fun onConfigured(session: CameraCaptureSession) {
                                captureSession = session
                                try {
                                    val requestBuilder = camera.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW).apply {
                                        addTarget(surface)
                                        set(CaptureRequest.CONTROL_MODE, CameraMetadata.CONTROL_MODE_AUTO)
                                        set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE)
                                        set(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_ON)
                                        set(CaptureRequest.CONTROL_AWB_MODE, CaptureRequest.CONTROL_AWB_MODE_AUTO)

                                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && target.minZoomRatio < 1.0f) {
                                            set(CaptureRequest.CONTROL_ZOOM_RATIO, target.minZoomRatio)
                                        }
                                    }

                                    // Run repeating request so exposure & white balance adapt naturally
                                    session.setRepeatingRequest(requestBuilder.build(), null, handler)

                                } catch (e: Exception) {
                                    Log.e(TAG, "Error starting repeating warmup request", e)
                                    cleanup(null)
                                }
                            }

                            override fun onConfigureFailed(session: CameraCaptureSession) {
                                Log.e(TAG, "Capture session configuration failed")
                                cleanup(null)
                            }
                        }

                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P && target.physicalCameraId != null) {
                            val outputConfig = OutputConfiguration(surface).apply {
                                setPhysicalCameraId(target.physicalCameraId)
                            }
                            val sessionConfig = SessionConfiguration(
                                SessionConfiguration.SESSION_REGULAR,
                                listOf(outputConfig),
                                Executors.newSingleThreadExecutor(),
                                sessionCallback
                            )
                            camera.createCaptureSession(sessionConfig)
                        } else {
                            @Suppress("DEPRECATION")
                            camera.createCaptureSession(listOf(surface), sessionCallback, handler)
                        }

                    } catch (e: Exception) {
                        Log.e(TAG, "Session initialization error", e)
                        cleanup(null)
                    }
                }

                override fun onDisconnected(camera: CameraDevice) {
                    cleanup(null)
                }

                override fun onError(camera: CameraDevice, error: Int) {
                    Log.e(TAG, "Camera open error: $error")
                    cleanup(null)
                }
            }, handler)

        } catch (e: Exception) {
            Log.e(TAG, "Exception opening camera", e)
            cleanup(null)
        }
    }
}
