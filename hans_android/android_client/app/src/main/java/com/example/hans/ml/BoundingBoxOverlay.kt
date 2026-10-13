package com.example.hans.ml

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import android.util.AttributeSet
import android.view.View
import com.example.hans.ml.tracker.TrackedDetection

class BoundingBoxOverlay @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    private var trackedDetections: List<TrackedDetection> = emptyList()
    private var frameWidth: Int = 1
    private var frameHeight: Int = 1
    private var handClassIdThreshold: Int = 80 // Class IDs >= 80 are hands

    // Paint for standard objects (Green)
    private val objBoxPaint = Paint().apply {
        style = Paint.Style.STROKE
        strokeWidth = 8f
        color = Color.rgb(0, 255, 127) // Spring Green
        isAntiAlias = true
    }

    // Paint for detected hands (Cyan)
    private val handBoxPaint = Paint().apply {
        style = Paint.Style.STROKE
        strokeWidth = 8f
        color = Color.rgb(0, 229, 255) // Cyan / Blue Accent
        isAntiAlias = true
    }

    private val textBgPaint = Paint().apply {
        style = Paint.Style.FILL
        color = Color.parseColor("#E6000000") // 90% opacity black
        isAntiAlias = true
    }

    private val textPaint = Paint().apply {
        color = Color.WHITE
        textSize = 34f
        typeface = Typeface.DEFAULT_BOLD
        isAntiAlias = true
    }

    init {
        setWillNotDraw(false)
    }

    fun setTrackedResults(
        results: List<TrackedDetection>,
        srcWidth: Int,
        srcHeight: Int,
        handIdThreshold: Int = 80
    ) {
        this.trackedDetections = results
        this.frameWidth = if (srcWidth > 0) srcWidth else 1
        this.frameHeight = if (srcHeight > 0) srcHeight else 1
        this.handClassIdThreshold = handIdThreshold
        postInvalidate()
    }

    fun clear() {
        this.trackedDetections = emptyList()
        postInvalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        if (trackedDetections.isEmpty()) return

        val viewW = width.toFloat()
        val viewH = height.toFloat()
        if (viewW <= 0f || viewH <= 0f) return

        val scale = maxOf(viewW / frameWidth, viewH / frameHeight)
        val offsetX = (viewW - frameWidth * scale) / 2f
        val offsetY = (viewH - frameHeight * scale) / 2f

        for (det in trackedDetections) {
            val box = det.boundingBox
            val isHand = det.classId >= handClassIdThreshold
            val paint = if (isHand) handBoxPaint else objBoxPaint

            val left = (box.left * frameWidth * scale) + offsetX
            val top = (box.top * frameHeight * scale) + offsetY
            val right = (box.right * frameWidth * scale) + offsetX
            val bottom = (box.bottom * frameHeight * scale) + offsetY

            val rect = RectF(
                left.coerceIn(0f, viewW),
                top.coerceIn(0f, viewH),
                right.coerceIn(0f, viewW),
                bottom.coerceIn(0f, viewH)
            )

            canvas.drawRoundRect(rect, 16f, 16f, paint)

            // Label text: shows ID:# just like Python server!
            // e.g. "✋ my_right ID:1 85%" or "cup ID:3 92%"
            val labelText = buildString {
                if (isHand) append("✋ ")
                append(det.label)
                append(" ID:${det.trackId} ")
                append("${(det.confidence * 100).toInt()}%")
                if (det.depth > 0) append(" ${String.format("%.2f", det.depth)}m")
            }

            val textWidth = textPaint.measureText(labelText)
            val textHeight = 36f

            val labelTop = (rect.top - textHeight - 16f).coerceAtLeast(10f)
            canvas.drawRect(
                rect.left,
                labelTop,
                rect.left + textWidth + 24f,
                labelTop + textHeight + 16f,
                textBgPaint
            )
            canvas.drawText(labelText, rect.left + 12f, labelTop + textHeight + 2f, textPaint)
        }
    }
}
