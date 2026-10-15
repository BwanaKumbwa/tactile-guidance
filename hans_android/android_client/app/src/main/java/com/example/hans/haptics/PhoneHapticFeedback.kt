package com.example.hans.haptics

import android.content.Context
import android.os.Build
import android.os.SystemClock
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.util.Log

class PhoneHapticFeedback(private val context: Context) {

    companion object {
        private const val TAG = "PhoneHapticFeedback"

        // Cadence boundaries in milliseconds
        private const val MIN_PULSE_INTERVAL_MS = 60L   // Closest / "Boiling hot" (< 5 cm)
        private const val MAX_PULSE_INTERVAL_MS = 620L  // Farthest / "Cold" (~70 cm)

        // Distance range in meters
        private const val MIN_DIST_METERS = 0.04f
        private const val MAX_DIST_METERS = 0.70f

        // Pulse duration
        private const val PULSE_DURATION_MS = 35L
    }

    private val vibrator: Vibrator = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        val vibratorManager = context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as VibratorManager
        vibratorManager.defaultVibrator
    } else {
        @Suppress("DEPRECATION")
        context.getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
    }

    private var lastPulseTimestamp = 0L
    @Volatile var isEnabled: Boolean = false

    /**
     * Updates the "hot/cold" cadence based on hand-to-target 3D/2D distance in meters.
     * Call this every frame from the arbitrator or MainActivity.
     */
    fun updateProximityFeedback(distanceMeters: Float) {
        if (!isEnabled || !vibrator.hasVibrator()) return

        val clampedDist = distanceMeters.coerceIn(MIN_DIST_METERS, MAX_DIST_METERS)
        val normalized = (clampedDist - MIN_DIST_METERS) / (MAX_DIST_METERS - MIN_DIST_METERS)

        // Linear interpolation between fast (60ms) and slow (620ms) interval
        val targetInterval = (MIN_PULSE_INTERVAL_MS + (normalized * (MAX_PULSE_INTERVAL_MS - MIN_PULSE_INTERVAL_MS))).toLong()

        val now = SystemClock.uptimeMillis()
        if (now - lastPulseTimestamp >= targetInterval) {
            lastPulseTimestamp = now
            fireShortPulse(clampedDist)
        }
    }

    private fun fireShortPulse(distanceMeters: Float) {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                // Higher amplitude (crisper click) as the hand gets closer
                val normalizedProximity = 1.0f - ((distanceMeters - MIN_DIST_METERS) / (MAX_DIST_METERS - MIN_DIST_METERS))
                val amplitude = (140 + (normalizedProximity * 115)).toInt().coerceIn(1, 255)

                val effect = VibrationEffect.createOneShot(PULSE_DURATION_MS, amplitude)
                vibrator.vibrate(effect)
            } else {
                @Suppress("DEPRECATION")
                vibrator.vibrate(PULSE_DURATION_MS)
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error vibrating phone", e)
        }
    }

    fun fireTargetReachedPulse() {
        if (!isEnabled || !vibrator.hasVibrator()) return
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                // Triple celebratory buzz pattern: buzz 60ms, pause 50ms, buzz 120ms
                val pattern = longArrayOf(0, 60, 50, 140)
                val amplitudes = intArrayOf(0, 255, 0, 255)
                val effect = VibrationEffect.createWaveform(pattern, amplitudes, -1)
                vibrator.vibrate(effect)
            } else {
                @Suppress("DEPRECATION")
                vibrator.vibrate(longArrayOf(0, 60, 50, 140), -1)
            }
        } catch (e: Exception) {
            Log.e(TAG, "Target reached vibration error", e)
        }
    }

    fun stop() {
        try {
            vibrator.cancel()
        } catch (e: Exception) {
            // Ignore
        }
    }
}
