package com.example.hans

import android.content.ContentValues
import android.content.Context
import android.graphics.*
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import android.util.Log
import com.example.hans.ml.Detection
import java.io.OutputStream
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

object DebugImageSaver {
    private const val TAG = "DebugImageSaver"

    /**
     * Saves a pristine, untouched bitmap to Pictures/HansDebug.
     */
    fun saveSnapshotToGallery(context: Context, bitmap: Bitmap, prefix: String = "wide_raw"): String? {
        val timeStamp = SimpleDateFormat("yyyyMMdd_HHmmss_SSS", Locale.US).format(Date())
        val filename = "${prefix}_$timeStamp.jpg"
        return writeBitmapToMediaStore(context, bitmap, filename)
    }

    /**
     * Draws bounding boxes, labels, and confidence percentages onto a cloned Bitmap,
     * saves it to Pictures/HansDebug, and returns both the saved filename and the annotated Bitmap.
     */
    fun saveAnnotatedSnapshotToGallery(
        context: Context,
        rawBitmap: Bitmap,
        detections: List<Detection>,
        prefix: String = "wide_annotated"
    ): Pair<String?, Bitmap> {
        val timeStamp = SimpleDateFormat("yyyyMMdd_HHmmss_SSS", Locale.US).format(Date())
        val filename = "${prefix}_$timeStamp.jpg"

        // Create mutable clone for drawing
        val annotatedBitmap = rawBitmap.copy(Bitmap.Config.ARGB_8888, true)
        val canvas = Canvas(annotatedBitmap)
        val imgWidth = annotatedBitmap.width.toFloat()
        val imgHeight = annotatedBitmap.height.toFloat()

        // 1. Box Paint (Vibrant Green)
        val boxPaint = Paint().apply {
            color = Color.parseColor("#00E676")
            style = Paint.Style.STROKE
            strokeWidth = (imgWidth * 0.005f).coerceIn(4f, 10f)
            isAntiAlias = true
        }

        // 2. Text Paint
        val fontSize = (imgWidth * 0.028f).coerceIn(24f, 44f)
        val textPaint = Paint().apply {
            color = Color.WHITE
            textSize = fontSize
            typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
            isAntiAlias = true
        }

        // 3. Label Background Paint (Semi-transparent black)
        val bgPaint = Paint().apply {
            color = Color.parseColor("#CC000000")
            style = Paint.Style.FILL
            isAntiAlias = true
        }

        for (det in detections) {
            // De-normalize bounding box coordinates (0..1 -> pixel coordinates)
            val left = (det.boundingBox.left * imgWidth).coerceIn(0f, imgWidth)
            val top = (det.boundingBox.top * imgHeight).coerceIn(0f, imgHeight)
            val right = (det.boundingBox.right * imgWidth).coerceIn(0f, imgWidth)
            val bottom = (det.boundingBox.bottom * imgHeight).coerceIn(0f, imgHeight)

            // Draw bounding rectangle
            canvas.drawRect(left, top, right, bottom, boxPaint)

            // Construct label text (e.g. "cup 84%")
            val confPct = (det.confidence * 100).toInt()
            val labelText = "${det.label} $confPct%"

            // Measure text for background pill
            val textBounds = Rect()
            textPaint.getTextBounds(labelText, 0, labelText.length, textBounds)
            val textWidth = textPaint.measureText(labelText)
            val textHeight = textBounds.height()

            val padX = 8f
            val padY = 6f
            val bgTop = (top - textHeight - padY * 2).coerceAtLeast(0f)
            val bgBottom = bgTop + textHeight + padY * 2
            val bgRight = (left + textWidth + padX * 2).coerceAtMost(imgWidth)

            // Draw label background pill
            canvas.drawRect(left, bgTop, bgRight, bgBottom, bgPaint)

            // Draw text
            canvas.drawText(labelText, left + padX, bgBottom - padY, textPaint)
        }

        val savedName = writeBitmapToMediaStore(context, annotatedBitmap, filename)
        return Pair(savedName, annotatedBitmap)
    }

    private fun writeBitmapToMediaStore(context: Context, bitmap: Bitmap, filename: String): String? {
        val values = ContentValues().apply {
            put(MediaStore.Images.Media.DISPLAY_NAME, filename)
            put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg")
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                put(MediaStore.Images.Media.RELATIVE_PATH, Environment.DIRECTORY_PICTURES + "/HansDebug")
                put(MediaStore.Images.Media.IS_PENDING, 1)
            }
        }

        val resolver = context.contentResolver
        val uri = resolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values) ?: return null

        return try {
            val outputStream: OutputStream? = resolver.openOutputStream(uri)
            outputStream?.use {
                bitmap.compress(Bitmap.CompressFormat.JPEG, 92, it)
            }

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                values.clear()
                values.put(MediaStore.Images.Media.IS_PENDING, 0)
                resolver.update(uri, values, null, null)
            }

            Log.i(TAG, "✓ Saved image: Pictures/HansDebug/$filename")
            filename
        } catch (e: Exception) {
            Log.e(TAG, "Failed writing bitmap to MediaStore", e)
            null
        }
    }
}
