package com.example.hans.ml

import android.media.Image
import java.nio.ByteBuffer
import kotlin.math.max
import kotlin.math.min

object CameraImageConverter {

    /**
     * Converts an ARCore YUV_420_888 Image directly into a normalized (0..1) RGB Float ByteBuffer
     * with 90-degree clockwise rotation (matching Android portrait display).
     *
     * Pre-allocated direct buffer size: 1 * targetSize * targetSize * 3 * 4 bytes.
     * ZERO Bitmap allocations per frame.
     */
    fun yuvToNormalizedRgbBuffer(
        image: Image,
        targetSize: Int,
        outputBuffer: ByteBuffer
    ) {
        outputBuffer.rewind()

        val yPlane = image.planes[0]
        val uPlane = image.planes[1]
        val vPlane = image.planes[2]

        val yBuffer = yPlane.buffer
        val uBuffer = uPlane.buffer
        val vBuffer = vPlane.buffer

        val srcWidth = image.width
        val srcHeight = image.height

        val yRowStride = yPlane.rowStride
        val yPixelStride = yPlane.pixelStride

        val uvRowStride = uPlane.rowStride
        val uvPixelStride = uPlane.pixelStride

        // In 90-deg clockwise rotation:
        // Portrait X [0..targetSize-1] maps to Camera Y: (targetSize - 1 - dstY) / targetSize * srcHeight
        // Portrait Y [0..targetSize-1] maps to Camera X: dstX / targetSize * srcWidth
        //
        // Specifically for target (dstX, dstY):
        // srcX = (dstY.toFloat() / targetSize * srcWidth).toInt()
        // srcY = ((targetSize - 1 - dstX).toFloat() / targetSize * srcHeight).toInt()

        for (dstY in 0 until targetSize) {
            val srcX = (dstY.toFloat() / targetSize * srcWidth).toInt().coerceIn(0, srcWidth - 1)

            for (dstX in 0 until targetSize) {
                val srcY = ((targetSize - 1 - dstX).toFloat() / targetSize * srcHeight).toInt().coerceIn(0, srcHeight - 1)

                // 1. Fetch Y
                val yIndex = srcY * yRowStride + srcX * yPixelStride
                val yVal = (yBuffer.get(yIndex).toInt() and 0xFF)

                // 2. Fetch U and V (subsampled 2x2)
                val uvSrcX = srcX / 2
                val uvSrcY = srcY / 2
                val uIndex = uvSrcY * uvRowStride + uvSrcX * uvPixelStride
                val vIndex = uvSrcY * vPlane.rowStride + uvSrcX * vPlane.pixelStride

                val uVal = (uBuffer.get(uIndex).toInt() and 0xFF) - 128
                val vVal = (vBuffer.get(vIndex).toInt() and 0xFF) - 128

                // 3. Fast Integer YUV -> RGB conversion
                // R = Y + 1.402 * V
                // G = Y - 0.344136 * U - 0.714136 * V
                // B = Y + 1.772 * U
                val r = (yVal + (91881 * vVal shr 16)).coerceIn(0, 255)
                val g = (yVal - ((22554 * uVal + 46802 * vVal) shr 16)).coerceIn(0, 255)
                val b = (yVal + (116130 * uVal shr 16)).coerceIn(0, 255)

                // 4. Normalized float (0.0 .. 1.0) into direct TFLite buffer
                outputBuffer.putFloat(r / 255.0f)
                outputBuffer.putFloat(g / 255.0f)
                outputBuffer.putFloat(b / 255.0f)
            }
        }

        outputBuffer.rewind()
    }
}
