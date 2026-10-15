package com.example.hans.ml.tracker

import android.graphics.RectF
import android.util.Log
import kotlin.math.hypot

class OpenVocabTracker {

    companion object {
        private const val TAG = "OpenVocabTracker"
        private const val OCCLUSION_TIMEOUT_MS = 6000L // Hold position for up to 6 seconds during grasp
        private const val HAND_PROXIMITY_THRESHOLD = 0.22f // Normalized distance between hand and target
    }

    private var activeTargetLabel: String? = null
    private var boundTrackId: Int? = null
    private var lastKnownBox: RectF? = null
    private var lastKnownDepth: Float = -1f
    private var lastSeenTimestamp: Long = 0L
    private var isUnderHandOcclusion: Boolean = false

    fun setTarget(label: String, initialBox: RectF, initialDepth: Float = -1f) {
        activeTargetLabel = label
        boundTrackId = null
        lastKnownBox = initialBox
        lastKnownDepth = initialDepth
        lastSeenTimestamp = System.currentTimeMillis()
        isUnderHandOcclusion = false
        Log.i(TAG, "🎯 OpenVocab target set: '$label' at [${initialBox.centerX()}, ${initialBox.centerY()}], depth=${initialDepth}m")
    }

    fun clearTarget() {
        activeTargetLabel = null
        boundTrackId = null
        lastKnownBox = null
        lastKnownDepth = -1f
        isUnderHandOcclusion = false
    }

    fun hasActiveTarget(): Boolean = activeTargetLabel != null

    /**
     * Processes live detections with hand-occlusion resilience.
     */
    fun processFrame(
        liveDetections: List<TrackedDetection>,
        currentDepthMapDepthAtCenter: Float = -1f
    ): List<TrackedDetection> {
        val target = activeTargetLabel ?: return liveDetections
        val now = System.currentTimeMillis()

        val handDet = liveDetections.firstOrNull { it.classId >= 80 }
        val targetBox = lastKnownBox

        // Check if hand is currently near the target's last known location
        val isHandNearby = if (handDet != null && targetBox != null) {
            val dist = hypot(
                handDet.boundingBox.centerX() - targetBox.centerX(),
                handDet.boundingBox.centerY() - targetBox.centerY()
            )
            dist < HAND_PROXIMITY_THRESHOLD
        } else false

        // -------------------------------------------------------------
        // Case 1: Currently bound track ID is visible in live frame
        // -------------------------------------------------------------
        if (boundTrackId != null) {
            val matchedIndex = liveDetections.indexOfFirst { it.trackId == boundTrackId }
            if (matchedIndex != -1) {
                val matched = liveDetections[matchedIndex]
                lastSeenTimestamp = now
                lastKnownBox = matched.boundingBox
                if (matched.depth > 0) lastKnownDepth = matched.depth
                isUnderHandOcclusion = false

                val aliased = TrackedDetection(
                    trackId = matched.trackId,
                    classId = matched.classId,
                    label = target,
                    confidence = matched.confidence,
                    boundingBox = matched.boundingBox,
                    depth = matched.depth
                )

                val resultList = liveDetections.toMutableList()
                resultList[matchedIndex] = aliased
                return resultList
            }
        }

        // -------------------------------------------------------------
        // Case 2: Track was lost — Check if a candidate re-appeared nearby
        // -------------------------------------------------------------
        if (targetBox != null) {
            val tCenterX = targetBox.centerX()
            val tCenterY = targetBox.centerY()

            val candidateIndex = liveDetections.indexOfFirst { det ->
                det.classId < 80 && hypot(det.boundingBox.centerX() - tCenterX, det.boundingBox.centerY() - tCenterY) < 0.28f
            }

            if (candidateIndex != -1) {
                val candidate = liveDetections[candidateIndex]
                boundTrackId = candidate.trackId
                lastSeenTimestamp = now
                lastKnownBox = candidate.boundingBox
                if (candidate.depth > 0) lastKnownDepth = candidate.depth
                isUnderHandOcclusion = false

                Log.i(TAG, "✓ Re-associated '$target' to new YOLO track ID ${candidate.trackId}")

                val aliased = TrackedDetection(
                    trackId = candidate.trackId,
                    classId = candidate.classId,
                    label = target,
                    confidence = candidate.confidence,
                    boundingBox = candidate.boundingBox,
                    depth = candidate.depth
                )

                val resultList = liveDetections.toMutableList()
                resultList[candidateIndex] = aliased
                return resultList
            }
        }

        // -------------------------------------------------------------
        // Case 3: Target is occluded (especially by the hand reaching for it)
        // -------------------------------------------------------------
        val timeSinceSeen = now - lastSeenTimestamp

        if (targetBox != null && (isHandNearby || timeSinceSeen < OCCLUSION_TIMEOUT_MS)) {
            if (isHandNearby && !isUnderHandOcclusion) {
                isUnderHandOcclusion = true
                Log.d(TAG, "✋ Target '$target' occluded by reaching hand -> holding spatial memory")
            }

            val syntheticDepth = when {
                lastKnownDepth > 0 -> lastKnownDepth
                currentDepthMapDepthAtCenter > 0 -> currentDepthMapDepthAtCenter
                else -> 0.5f
            }

            // Emit persistent synthetic detection so haptic arbitrator keeps guiding the hand to the target
            val persistentTarget = TrackedDetection(
                trackId = 9999,
                classId = 79,
                label = target,
                confidence = 0.85f,
                boundingBox = targetBox,
                depth = syntheticDepth
            )

            return liveDetections + persistentTarget
        }

        return liveDetections
    }
}
