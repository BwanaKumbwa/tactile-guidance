package com.example.hans.ml

import android.media.Image
import com.example.hans.ml.tracker.TrackedDetection
import java.nio.ByteOrder
import kotlin.math.max
import kotlin.math.min

object DepthUtils {

    /**
     * Extracts metric depth (in meters) for each tracked detection box.
     * Replicates Python server logic: 15th percentile of non-zero pixels
     * in the central 25% region-of-interest (ROI) of the bounding box.
     *
     * @param depthImage 16-bit depth Image from ARCore (format DEPTH16)
     * @param detections List of tracked bounding boxes with normalized [0..1] coordinates
     * @param sensorRotationDegrees Rotation between depth sensor and portrait display (typically 90)
     */
    fun assignDepthToDetections(
        depthImage: Image,
        detections: List<TrackedDetection>,
        sensorRotationDegrees: Int = 90
    ) {
        val plane = depthImage.planes[0]
        val buffer = plane.buffer
        buffer.order(ByteOrder.LITTLE_ENDIAN)

        val depthWidth = depthImage.width
        val depthHeight = depthImage.height
        val rowStride = plane.rowStride
        val pixelStride = plane.pixelStride

        for (det in detections) {
            val box = det.boundingBox

            // In portrait, if display is rotated 90 deg relative to sensor:
            // Display X maps to Sensor Y; Display Y maps to Sensor (width - X)
            val roiLeftNorm: Float
            val roiTopNorm: Float
            val roiRightNorm: Float
            val roiBottomNorm: Float

            if (sensorRotationDegrees == 90) {
                roiLeftNorm = box.top
                roiTopNorm = 1.0f - box.right
                roiRightNorm = box.bottom
                roiBottomNorm = 1.0f - box.left
            } else {
                roiLeftNorm = box.left
                roiTopNorm = box.top
                roiRightNorm = box.right
                roiBottomNorm = box.bottom
            }

            // Central 25% crop of bounding box
            val xc = (roiLeftNorm + roiRightNorm) / 2f
            val yc = (roiTopNorm + roiBottomNorm) / 2f
            val hw = max(0.01f, (roiRightNorm - roiLeftNorm) * 0.25f)
            val hh = max(0.01f, (roiBottomNorm - roiTopNorm) * 0.25f)

            val xMin = max(0, ((xc - hw) * depthWidth).toInt())
            val xMax = min(depthWidth - 1, ((xc + hw) * depthWidth).toInt())
            val yMin = max(0, ((yc - hh) * depthHeight).toInt())
            val yMax = min(depthHeight - 1, ((yc + hh) * depthHeight).toInt())

            if (xMax <= xMin || yMax <= yMin) {
                det.depth = -1.0f
                continue
            }

            val validDepthsMm = ArrayList<Int>((xMax - xMin + 1) * (yMax - yMin + 1))

            for (y in yMin..yMax) {
                for (x in xMin..xMax) {
                    val byteIndex = (y * rowStride) + (x * pixelStride)
                    val depthMm = buffer.getShort(byteIndex).toInt() and 0xFFFF
                    // Filter out zero (invalid/unreflective/shadow pixels)
                    if (depthMm in 80..6000) { // Valid range: 8cm to 6m
                        validDepthsMm.add(depthMm)
                    }
                }
            }

            if (validDepthsMm.isNotEmpty()) {
                validDepthsMm.sort()
                // 15th percentile: robust foreground surface depth (avoids background leakage)
                val p15Index = (validDepthsMm.size * 0.15f).toInt().coerceIn(0, validDepthsMm.size - 1)
                val depthMeters = validDepthsMm[p15Index] / 1000.0f
                det.depth = depthMeters
            } else {
                det.depth = -1.0f
            }
        }
    }
}
