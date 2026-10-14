package com.example.hans.ml

import android.media.Image
import com.example.hans.ml.tracker.TrackedDetection
import java.nio.ByteOrder

object DepthUtils {

    /**
     * Maps tracked detection bounding boxes (normalized portrait [0..1])
     * into ARCore 16-bit depth coordinates and assigns depth in meters.
     */
    fun assignDepthToDetections(
        depthImage: Image,
        detections: List<TrackedDetection>,
        sensorRotationDegrees: Int = 90
    ) {
        val plane = depthImage.planes[0]
        val buffer = plane.buffer.order(ByteOrder.LITTLE_ENDIAN)

        val depthWidth = depthImage.width
        val depthHeight = depthImage.height
        val rowStride = plane.rowStride
        val pixelStride = plane.pixelStride

        for (det in detections) {
            val portraitNormX = det.boundingBox.centerX().coerceIn(0f, 1f)
            val portraitNormY = det.boundingBox.centerY().coerceIn(0f, 1f)

            // Convert normalized portrait (X, Y) to raw Depth sensor (depthX, depthY)
            val (depthX, depthY) = when (sensorRotationDegrees) {
                90 -> {
                    val rawX = (portraitNormY * depthWidth).toInt().coerceIn(0, depthWidth - 1)
                    val rawY = ((1.0f - portraitNormX) * depthHeight).toInt().coerceIn(0, depthHeight - 1)
                    Pair(rawX, rawY)
                }
                else -> {
                    Pair(
                        (portraitNormX * depthWidth).toInt().coerceIn(0, depthWidth - 1),
                        (portraitNormY * depthHeight).toInt().coerceIn(0, depthHeight - 1)
                    )
                }
            }

            // Sample a 3x3 median patch around (depthX, depthY) to filter out sensor dropouts (0 values)
            val validDepths = mutableListOf<Float>()

            for (dy in -1..1) {
                for (dx in -1..1) {
                    val sampleX = (depthX + dx).coerceIn(0, depthWidth - 1)
                    val sampleY = (depthY + dy).coerceIn(0, depthHeight - 1)

                    val byteOffset = sampleY * rowStride + sampleX * pixelStride
                    val depthMm = buffer.getShort(byteOffset).toInt() and 0xFFFF

                    if (depthMm in 100..4500) { // Valid desk/room depth range: 10 cm to 4.5 m
                        validDepths.add(depthMm / 1000.0f)
                    }
                }
            }

            if (validDepths.isNotEmpty()) {
                validDepths.sort()
                val medianDepth = validDepths[validDepths.size / 2]
                det.depth = medianDepth
            }
        }
    }
}
