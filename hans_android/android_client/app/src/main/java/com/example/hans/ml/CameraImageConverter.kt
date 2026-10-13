package com.example.hans.ml

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageFormat
import android.graphics.Matrix
import android.graphics.Rect
import android.graphics.YuvImage
import android.media.Image
import java.io.ByteArrayOutputStream

object CameraImageConverter {

    /**
     * Converts an ARCore YUV_420_888 Image into a Portrait-rotated 640x640 Bitmap.
     */
    fun yuvToPortraitBitmap(image: Image, targetSize: Int = 640): Bitmap {
        val width = image.width
        val height = image.height

        val nv21 = yuv420ToNv21(image)

        val yuvImage = YuvImage(nv21, ImageFormat.NV21, width, height, null)
        val out = ByteArrayOutputStream()
        // 75% JPEG quality is fast to encode/decode and preserves full edge fidelity for YOLO
        yuvImage.compressToJpeg(Rect(0, 0, width, height), 75, out)

        val rawBmp = BitmapFactory.decodeByteArray(out.toByteArray(), 0, out.size())
            ?: throw IllegalStateException("Failed to decode YUV byte stream")

        // Rotate 90 degrees to match portrait screen orientation and scale to 640x640
        val matrix = Matrix().apply {
            postRotate(90f)
            val scaleX = targetSize.toFloat() / height // Note swapped dims after 90 deg rotation
            val scaleY = targetSize.toFloat() / width
            postScale(scaleX, scaleY)
        }

        val portraitBmp = Bitmap.createBitmap(rawBmp, 0, 0, rawBmp.width, rawBmp.height, matrix, true)
        rawBmp.recycle()
        return portraitBmp
    }

    private fun yuv420ToNv21(image: Image): ByteArray {
        val width = image.width
        val height = image.height
        val ySize = width * height
        val uvSize = width * height / 2
        val nv21 = ByteArray(ySize + uvSize)

        val yPlane = image.planes[0]
        val uPlane = image.planes[1]
        val vPlane = image.planes[2]

        val yBuffer = yPlane.buffer
        val uBuffer = uPlane.buffer
        val vBuffer = vPlane.buffer

        val yRowStride = yPlane.rowStride
        val yPixelStride = yPlane.pixelStride

        if (yPixelStride == 1 && yRowStride == width) {
            yBuffer.get(nv21, 0, ySize)
        } else {
            val rowData = ByteArray(width)
            for (row in 0 until height) {
                yBuffer.position(row * yRowStride)
                yBuffer.get(rowData, 0, width)
                System.arraycopy(rowData, 0, nv21, row * width, width)
            }
        }

        val vRowStride = vPlane.rowStride
        val vPixelStride = vPlane.pixelStride
        val uRowStride = uPlane.rowStride
        val uPixelStride = uPlane.pixelStride

        val uvWidth = width / 2
        val uvHeight = height / 2
        var nv21Idx = ySize

        val vBytes = ByteArray(vBuffer.remaining())
        val uBytes = ByteArray(uBuffer.remaining())
        vBuffer.get(vBytes)
        uBuffer.get(uBytes)

        for (row in 0 until uvHeight) {
            val vRowStart = row * vRowStride
            val uRowStart = row * uRowStride
            for (col in 0 until uvWidth) {
                nv21[nv21Idx++] = vBytes[vRowStart + (col * vPixelStride)]
                nv21[nv21Idx++] = uBytes[uRowStart + (col * uPixelStride)]
            }
        }

        return nv21
    }
}
