package com.example.hans

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View
import org.json.JSONArray

class OverlayView(context: Context, attrs: AttributeSet?) : View(context, attrs) {

    // Helper class to store box data
    private data class Box(val rect: RectF, val label: String)

    private val boxes = mutableListOf<Box>()

    // Paint for the Bounding Box
    private val boxPaint = Paint().apply {
        color = Color.GREEN
        style = Paint.Style.STROKE
        strokeWidth = 8f
        isAntiAlias = true
    }

    // Paint for the Text Background
    private val textBgPaint = Paint().apply {
        color = Color.GREEN
        style = Paint.Style.FILL
    }

    // Paint for the Text itself
    private val textPaint = Paint().apply {
        color = Color.BLACK
        textSize = 50f
        style = Paint.Style.FILL
        isFakeBoldText = true
        isAntiAlias = true
    }

    /**
     * Called by MainActivity when new JSON data arrives.
     * Expected JSON format: [{"x":0.5, "y":0.5, "w":0.2, "h":0.2, "label":"cup"}, ...]
     */
    fun setDetections(jsonArray: JSONArray) {
        boxes.clear()

        for (i in 0 until jsonArray.length()) {
            val obj = jsonArray.getJSONObject(i)

            // Normalized coordinates (0.0 to 1.0)
            val cx = obj.optDouble("x", 0.0).toFloat()
            val cy = obj.optDouble("y", 0.0).toFloat()
            val w = obj.optDouble("w", 0.0).toFloat()
            val h = obj.optDouble("h", 0.0).toFloat()
            val label = obj.optString("label", "?")

            // Convert to Screen Pixels
            // Note: Camera feed is often 640x480 (4:3), while screen might be taller (20:9).
            // A perfect implementation handles aspect ratio mapping, but for now we scale to view size.
            val screenW = width.toFloat()
            val screenH = height.toFloat()

            val left = (cx - w / 2) * screenW
            val top = (cy - h / 2) * screenH
            val right = (cx + w / 2) * screenW
            val bottom = (cy + h / 2) * screenH

            boxes.add(Box(RectF(left, top, right, bottom), label))
        }

        // Trigger a redraw
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        for (box in boxes) {
            // 1. Draw Rectangle
            canvas.drawRect(box.rect, boxPaint)

            // 2. Draw Label Background
            val textWidth = textPaint.measureText(box.label)
            val textHeight = textPaint.textSize
            canvas.drawRect(
                box.rect.left,
                box.rect.top - textHeight - 10,
                box.rect.left + textWidth + 20,
                box.rect.top,
                textBgPaint
            )

            // 3. Draw Text
            canvas.drawText(
                box.label,
                box.rect.left + 10,
                box.rect.top - 10,
                textPaint
            )
        }
    }
}