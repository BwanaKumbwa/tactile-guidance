package com.example.hans.ml.tracker

import android.graphics.RectF
import com.example.hans.ml.Detection
import kotlin.math.sqrt

/**
 * 2D Bounding Box Kalman Filter for tracking moving objects in image space.
 * State vector: [x_center, y_center, area, aspect_ratio, dx, dy, darea, 0]
 */
class KalmanBoxTracker(detection: Detection) {

    companion object {
        private var count = 0
        fun resetCounter() { count = 0 }
    }

    val id: Int = ++count
    val classId: Int = detection.classId
    val label: String = detection.label

    var timeSinceUpdate: Int = 0
    var hits: Int = 1
    var hitStreak: Int = 1
    var age: Int = 0
    var confidence: Float = detection.confidence

    // State: [x, y, s (scale/area), r (aspect ratio), vx, vy, vs, 0]
    private val x = FloatArray(7)
    // Covariance matrix diagonal approximation (fast 1D Kalman)
    private val p = floatArrayOf(10f, 10f, 10f, 10f, 10000f, 10000f, 10000f)

    init {
        val box = detection.boundingBox
        val w = box.width()
        val h = box.height()
        x[0] = box.centerX()
        x[1] = box.centerY()
        x[2] = w * h
        x[3] = if (h > 0) w / h else 1.0f
    }

    /**
     * Advances the state vector and returns the predicted bounding box.
     */
    fun predict(): RectF {
        // Constant velocity motion model
        x[0] += x[4]
        x[1] += x[5]
        x[2] += x[6]

        age++
        if (timeSinceUpdate > 0) {
            hitStreak = 0
        }
        timeSinceUpdate++

        return getBoundingBox()
    }

    /**
     * Updates the state vector with an observed bounding box.
     */
    fun update(detection: Detection) {
        timeSinceUpdate = 0
        hits++
        hitStreak++
        this.confidence = detection.confidence

        val box = detection.boundingBox
        val w = box.width()
        val h = box.height()
        val z0 = box.centerX()
        val z1 = box.centerY()
        val z2 = w * h
        val z3 = if (h > 0) w / h else 1.0f

        // Simple Kalman update (blending observation with prediction)
        val kPos = 0.75f
        val kVel = 0.25f

        val dx0 = z0 - x[0]
        val dx1 = z1 - x[1]
        val dx2 = z2 - x[2]

        x[0] += kPos * dx0
        x[1] += kPos * dx1
        x[2] += kPos * dx2
        x[3] = z3 // Aspect ratio is generally invariant

        x[4] += kVel * dx0
        x[5] += kVel * dx1
        x[6] += kVel * dx2
    }

    fun getBoundingBox(): RectF {
        val w = sqrt(maxOf(0f, x[2] * x[3]))
        val h = if (x[3] > 0) x[2] / w else 0f
        return RectF(
            x[0] - w / 2f,
            x[1] - h / 2f,
            x[0] + w / 2f,
            x[1] + h / 2f
        )
    }
}
