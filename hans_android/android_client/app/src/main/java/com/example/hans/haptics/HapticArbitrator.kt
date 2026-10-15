package com.example.hans.haptics

import android.os.SystemClock
import android.util.Log
import com.example.hans.BleManager
import com.example.hans.ml.tracker.TrackedDetection
import kotlin.math.atan2
import kotlin.math.hypot

data class GuidanceState(
    val isGuiding: Boolean,
    val targetName: String,
    val distanceMeters: Float,
    val directionAngleDeg: Float,
    val activeDevice: String,
    val isGrasped: Boolean
)

class HapticArbitrator(
    private val braceletManager: BleManager,
    private val beltManager: BleManager,
    private val phoneHaptics: PhoneHapticFeedback? = null
) {
    companion object {
        private const val TAG = "HapticArbitrator"
        private const val BRACELET_THROTTLE_MS = 140L
        private const val BELT_REFRESH_INTERVAL_MS = 1000L
        private const val GRASP_THRESHOLD_METERS = 0.09f
        private const val BELT_DISTANCE_THRESHOLD = 0.70f // 70 cm boundary
        private const val ON_TARGET_DISTANCE_THRESHOLD = 0.045f // Hand directly centered over object
    }

    // Toggle between BLE bracelet vectors and Phone "Hot/Cold" distance vibration
    var usePhoneHaptics: Boolean = false

    // Manual Grasp Mode: false by default so guidance stays active until confirmed
    var enableAutoGrasp: Boolean = false

    private var lastBraceletCmdTimestamp = 0L
    private var lastBeltCmdTimestamp = 0L
    private var isGrasped = false
    private var graspCooldownUntil = 0L

    @Volatile private var isBeltVibrating = false
    @Volatile private var isBraceletVibrating = false

    fun updateNavigation(
        activeTarget: String,
        detections: List<TrackedDetection>,
        vibrationIntensities: Map<String, Int>,
        patternIndex: Int = 0
    ): GuidanceState {
        val now = SystemClock.uptimeMillis()

        if (activeTarget == "none" || activeTarget.isBlank()) {
            stopAllFeedback()
            return GuidanceState(false, "none", -1f, 0f, "none", false)
        }

        // Grasp cooldown
        if (isGrasped) {
            if (now < graspCooldownUntil) {
                return GuidanceState(false, activeTarget, 0f, 0f, "none", true)
            } else {
                isGrasped = false
            }
        }

        // Match target either by exact label or open-vocabulary alias
        val targetDet = detections.firstOrNull {
            it.label.equals(activeTarget, ignoreCase = true) ||
            (activeTarget.contains(it.label, ignoreCase = true) && it.classId < 80)
        }
        val handDet = detections.firstOrNull { it.classId >= 80 }

        if (targetDet == null) {
            stopAllFeedback()
            return GuidanceState(false, activeTarget, -1f, 0f, "searching", false)
        }

        val targetDepth = if (targetDet.depth > 0) targetDet.depth else 0.5f

        // =====================================================================
        // Strategy A: Target is far (> 70 cm) -> Belt guidance
        // =====================================================================
        if (targetDepth > BELT_DISTANCE_THRESHOLD) {
            if (isBraceletVibrating) {
                stopBracelet()
            }
            phoneHaptics?.stop()

            val targetXc = targetDet.boundingBox.centerX()
            val shouldRefreshBelt = !isBeltVibrating || (now - lastBeltCmdTimestamp) >= BELT_REFRESH_INTERVAL_MS

            if (shouldRefreshBelt) {
                lastBeltCmdTimestamp = now
                isBeltVibrating = true
                val intensity = vibrationIntensities["belt"] ?: 60
                sendBeltDirectionCommand(targetXc, intensity)
            }
            return GuidanceState(true, activeTarget, targetDepth, 0f, "belt", false)
        }

        // =====================================================================
        // Strategy B: Target is close (<= 70 cm) -> Bracelet or Phone guidance
        // =====================================================================
        if (isBeltVibrating) {
            stopBelt()
        }

        if (handDet == null) {
            if (isBraceletVibrating) {
                stopBracelet()
            }
            phoneHaptics?.stop()
            return GuidanceState(true, activeTarget, targetDepth, 0f, "waiting_for_hand", false)
        }

        val hx = handDet.boundingBox.centerX()
        val hy = handDet.boundingBox.centerY()
        val tx = targetDet.boundingBox.centerX()
        val ty = targetDet.boundingBox.centerY()

        val dx = tx - hx
        val dy = ty - hy
        val distance2D = hypot(dx, dy)

        var angleDeg = Math.toDegrees(atan2(-dy.toDouble(), dx.toDouble())).toFloat()
        if (angleDeg < 0) angleDeg += 360f

        val handDepth = if (handDet.depth > 0) handDet.depth else targetDepth
        val depthDelta = Math.abs(targetDepth - handDepth)

        // Compute 3D Euclidean metric separation
        val distance3D = Math.sqrt((distance2D * targetDepth * distance2D * targetDepth + depthDelta * depthDelta).toDouble()).toFloat()

        // Automatic grasp trigger (only runs if explicitly enabled)
        if (enableAutoGrasp && distance2D < 0.12f && depthDelta < GRASP_THRESHOLD_METERS) {
            isGrasped = true
            graspCooldownUntil = now + 1500L
            sendGraspConfirmationPulse()
            return GuidanceState(false, activeTarget, targetDepth, angleDeg, "grasped", true)
        }

        // -----------------------------------------------------------------
        // BRANCH 1: DEV MODE -> Use Phone "Hot/Cold" Cadence Vibration
        // -----------------------------------------------------------------
        if (usePhoneHaptics && phoneHaptics != null) {
            stopBracelet()
            phoneHaptics.updateProximityFeedback(distance3D)
            return GuidanceState(true, activeTarget, targetDepth, 0f, "phone_haptics", false)
        }

        // -----------------------------------------------------------------
        // BRANCH 2: PRODUCTION -> 4-Quadrant Tactile BLE Bracelet Guidance
        // -----------------------------------------------------------------
        phoneHaptics?.stop()

        if (distance2D < ON_TARGET_DISTANCE_THRESHOLD) {
            if ((now - lastBraceletCmdTimestamp) >= BRACELET_THROTTLE_MS) {
                lastBraceletCmdTimestamp = now
                isBraceletVibrating = true
                sendOnTargetCenteredPulse(vibrationIntensities)
            }
            return GuidanceState(true, activeTarget, targetDepth, 0f, "on_target", false)
        }

        if ((now - lastBraceletCmdTimestamp) >= BRACELET_THROTTLE_MS) {
            lastBraceletCmdTimestamp = now
            isBraceletVibrating = true
            sendBraceletVectorCommand(angleDeg, distance2D, vibrationIntensities, patternIndex)
        }

        return GuidanceState(true, activeTarget, targetDepth, angleDeg, "bracelet", false)
    }

    private fun sendOnTargetCenteredPulse(intensities: Map<String, Int>) {
        if (!braceletManager.isConnected()) return
        val intensity = (intensities["top"] ?: 50).coerceIn(10, 60)
        val pulseDuration = 90

        val command = byteArrayOf(
            0x0E.toByte(),
            0x04.toByte(),
            0x03.toByte(), // Top motor
            intensity.toByte(),
            ((pulseDuration shr 8) and 0xFF).toByte(),
            (pulseDuration and 0xFF).toByte()
        )
        braceletManager.writeRawCommand(command)
    }

    private fun sendBeltDirectionCommand(targetXc: Float, intensity: Int) {
        if (!beltManager.isConnected()) return

        val motorMask = when {
            targetXc < 0.33f -> 0b100000
            targetXc > 0.66f -> 0b001000
            else -> 0b010000
        }

        val clampedIntensity = intensity.coerceIn(10, 100)
        val onDurationMs = 2000

        val command = byteArrayOf(
            0x40.toByte(), 0x01.toByte(), 0x01.toByte(),
            (motorMask and 0xFF).toByte(), ((motorMask shr 8) and 0xFF).toByte(),
            clampedIntensity.toByte(),
            (onDurationMs and 0xFF).toByte(), ((onDurationMs shr 8) and 0xFF).toByte(),
            0x01.toByte(), 0x01.toByte(),
            (onDurationMs and 0xFF).toByte(), ((onDurationMs shr 8) and 0xFF).toByte(),
            (onDurationMs and 0xFF).toByte(), ((onDurationMs shr 8) and 0xFF).toByte(),
            0x00.toByte(), 0x00.toByte(), 0x00.toByte()
        )

        beltManager.writeRawCommand(command)
    }

    private fun sendBraceletVectorCommand(
        angleDeg: Float,
        distance2D: Float,
        intensities: Map<String, Int>,
        patternIndex: Int
    ) {
        if (!braceletManager.isConnected()) return

        val (motorId, motorKey) = when (angleDeg) {
            in 45.0..135.0 -> Pair(0x03, "top")
            in 135.0..225.0 -> Pair(0x00, "left")
            in 225.0..315.0 -> Pair(0x01, "bottom")
            else -> Pair(0x02, "right")
        }

        val intensity = (intensities[motorKey] ?: 50).coerceIn(10, 100)
        val pulseDuration = 180

        val command = byteArrayOf(
            0x0E.toByte(),
            0x04.toByte(),
            motorId.toByte(),
            intensity.toByte(),
            ((pulseDuration shr 8) and 0xFF).toByte(),
            (pulseDuration and 0xFF).toByte()
        )
        braceletManager.writeRawCommand(command)
    }

    /**
     * Confirmation double-pulse fired on manual user confirmation.
     */
    fun sendGraspConfirmationPulse() {
        stopAllFeedback()

        if (usePhoneHaptics && phoneHaptics != null) {
            phoneHaptics.fireTargetReachedPulse()
        } else if (braceletManager.isConnected()) {
            val graspCmd = byteArrayOf(
                0x0E.toByte(),
                0x04.toByte(),
                0xFF.toByte(),
                0x64.toByte(),
                0x02.toByte(),
                0x58.toByte()
            )
            braceletManager.writeRawCommand(graspCmd)
        }

        if (beltManager.isConnected()) {
            val onDurationMs = 600
            val beltGraspCmd = byteArrayOf(
                0x40.toByte(), 0x01.toByte(), 0x01.toByte(),
                63.toByte(), 0x00.toByte(),
                80.toByte(),
                (onDurationMs and 0xFF).toByte(), ((onDurationMs shr 8) and 0xFF).toByte(),
                0x01.toByte(), 0x01.toByte(),
                (onDurationMs and 0xFF).toByte(), ((onDurationMs shr 8) and 0xFF).toByte(),
                (onDurationMs and 0xFF).toByte(), ((onDurationMs shr 8) and 0xFF).toByte(),
                0x00.toByte(), 0x00.toByte(), 0x00.toByte()
            )
            beltManager.writeRawCommand(beltGraspCmd)
        }
    }

    private fun stopBracelet() {
        if (isBraceletVibrating && braceletManager.isConnected()) {
            isBraceletVibrating = false
            val braceletStopCmd = byteArrayOf(0x03.toByte(), 0x01.toByte(), 0xFF.toByte())
            braceletManager.writeRawCommand(braceletStopCmd)
        }
    }

    private fun stopBelt() {
        if (isBeltVibrating && beltManager.isConnected()) {
            isBeltVibrating = false
            val beltStopCmd = byteArrayOf(0x30.toByte(), 0xFF.toByte())
            beltManager.writeRawCommand(beltStopCmd)
        }
    }

    fun stopAllFeedback() {
        stopBracelet()
        stopBelt()
        phoneHaptics?.stop()
    }
}
