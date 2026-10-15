package com.example.hans.haptics

import android.content.Context
import android.os.Build
import android.os.SystemClock
import android.os.VibrationAttributes
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.util.Log

class PhoneHapticFeedback(private val context: Context) {

    companion object {
        private const val TAG = "PhoneHapticFeedback"

        // Cadence boundaries in milliseconds
        private const val MIN_PULSE_INTERVAL_MS = 70L   // Closest / "Boiling hot" (< 5 cm)
        private const val MAX_PULSE_INTERVAL_MS = 620L  // Farthest / "Cold" (~70 cm)

        // Distance range in meters
        private const val MIN_DIST_METERS = 0.04f
        private const val MAX_DIST_METERS = 0.70f

        // Increased from 35ms to 55ms to overcome Pixel 8a coin-LRA motor inertia
        private const val PULSE_DURATION_MS = 55L
    }

    private val vibrator: Vibrator = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        val vibratorManager = context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager
        vibratorManager?.defaultVibrator ?: (context.getSystemService(Context.VIBRATOR_SERVICE) as Vibrator)
    } else {
        @Suppress("DEPRECATION")
        context.getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
    }

    // Android 13+ Accessibility attribute: bypasses system touch-feedback muting
    private val accessibilityVibeAttrs: VibrationAttributes? = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
        VibrationAttributes.Builder()
            .setUsage(VibrationAttributes.USAGE_ACCESSIBILITY)
            .build()
    } else null

    private var lastPulseTimestamp = 0L
    @Volatile var isEnabled: Boolean = false

    /**
     * Updates the "hot/cold" cadence based on distance.
     */
    fun updateProximityFeedback(distanceMeters: Float) {
        if (!isEnabled || !vibrator.hasVibrator()) return

        val clampedDist = distanceMeters.coerceIn(MIN_DIST_METERS, MAX_DIST_METERS)
        val normalized = (clampedDist - MIN_DIST_METERS) / (MAX_DIST_METERS - MIN_DIST_METERS)

        val targetInterval = (MIN_PULSE_INTERVAL_MS + (normalized * (MAX_PULSE_INTERVAL_MS - MIN_PULSE_INTERVAL_MS))).toLong()

        val now = SystemClock.uptimeMillis()
        if (now - lastPulseTimestamp >= targetInterval) {
            lastPulseTimestamp = now
            fireShortPulse(clampedDist)
        }
    }

    /**
     * Modulated pulse supporting alignment gating and distance.
     */
    fun updateTargetedFeedback(distanceMeters: Float, alignmentError2D: Float) {
        if (!isEnabled || !vibrator.hasVibrator()) return

        val clampedDist = distanceMeters.coerceIn(MIN_DIST_METERS, MAX_DIST_METERS)
        val normalizedDist = (clampedDist - MIN_DIST_METERS) / (MAX_DIST_METERS - MIN_DIST_METERS)
        val targetInterval = (MIN_PULSE_INTERVAL_MS + (normalizedDist * (MAX_PULSE_INTERVAL_MS - MIN_PULSE_INTERVAL_MS))).toLong()

        val now = SystemClock.uptimeMillis()
        if (now - lastPulseTimestamp >= targetInterval) {
            lastPulseTimestamp = now

            val alignmentFactor = (1.0f - (alignmentError2D.coerceIn(0.04f, 0.35f) - 0.04f) / (0.35f - 0.04f)).coerceIn(0f, 1f)
            // Ensure minimum amplitude is high enough for Pixel 8a actuator (110 .. 255)
            val amplitude = (110 + (alignmentFactor * 145)).toInt()

            fireModulatedPulse(amplitude)
        }
    }

    private fun fireShortPulse(distanceMeters: Float) {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                val normalizedProximity = 1.0f - ((distanceMeters - MIN_DIST_METERS) / (MAX_DIST_METERS - MIN_DIST_METERS))
                // Floor at 120 so the Pixel 8a motor definitely resonates
                val amplitude = (120 + (normalizedProximity * 135)).toInt().coerceIn(1, 255)

                val effect = VibrationEffect.createOneShot(PULSE_DURATION_MS, amplitude)

                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && accessibilityVibeAttrs != null) {
                    vibrator.vibrate(effect, accessibilityVibeAttrs)
                } else {
                    vibrator.vibrate(effect)
                }
            } else {
                @Suppress("DEPRECATION")
                vibrator.vibrate(PULSE_DURATION_MS)
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error vibrating phone", e)
        }
    }

    private fun fireModulatedPulse(amplitude: Int) {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                val effect = VibrationEffect.createOneShot(PULSE_DURATION_MS, amplitude.coerceIn(1, 255))
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && accessibilityVibeAttrs != null) {
                    vibrator.vibrate(effect, accessibilityVibeAttrs)
                } else {
                    vibrator.vibrate(effect)
                }
            } else {
                @Suppress("DEPRECATION")
                vibrator.vibrate(PULSE_DURATION_MS)
            }
        } catch (e: Exception) {
            Log.e(TAG, "Modulated pulse error", e)
        }
    }

    fun fireTargetReachedPulse() {
        if (!isEnabled || !vibrator.hasVibrator()) return
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                val pattern = longArrayOf(0, 80, 60, 160)
                val amplitudes = intArrayOf(0, 255, 0, 255)
                val effect = VibrationEffect.createWaveform(pattern, amplitudes, -1)

                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && accessibilityVibeAttrs != null) {
                    vibrator.vibrate(effect, accessibilityVibeAttrs)
                } else {
                    vibrator.vibrate(effect)
                }
            } else {
                @Suppress("DEPRECATION")
                vibrator.vibrate(longArrayOf(0, 80, 60, 160), -1)
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
