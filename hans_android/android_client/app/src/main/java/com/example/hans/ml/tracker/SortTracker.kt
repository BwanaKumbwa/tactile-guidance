package com.example.hans.ml.tracker

import android.graphics.RectF
import com.example.hans.ml.Detection
import kotlin.math.max
import kotlin.math.min

data class TrackedDetection(
    val trackId: Int,
    val classId: Int,
    val label: String,
    val confidence: Float,
    val boundingBox: RectF,
    var depth: Float = -1.0f // Mirrors Python: [xc, yc, w, h, track_id, cls, conf, depth=-1]
)

class SortTracker(
    private val maxAge: Int = 30, // Number of frames to retain lost tracks
    private val minHits: Int = 2, // Hits required before reporting a track (prevents flicker)
    private val iouThreshold: Float = 0.30f
) {
    private val trackers = mutableListOf<KalmanBoxTracker>()
    private var frameCount = 0

    /**
     * Updates tracks with new detections from the detector.
     * Returns the list of currently confirmed, active tracked detections.
     */
    fun update(detections: List<Detection>): List<TrackedDetection> {
        frameCount++

        // 1. Predict new locations of existing trackers
        val predictedBoxes = trackers.map { it.predict() }

        // 2. Associate detections to trackers using greedy bipartite matching
        val matchedIndices = mutableSetOf<Int>()
        val matchedTrackers = mutableSetOf<Int>()

        val matches = mutableListOf<Pair<Int, Int>>() // Pair(detectionIdx, trackerIdx)

        if (trackers.isNotEmpty() && detections.isNotEmpty()) {
            val iouMatrix = Array(detections.size) { FloatArray(trackers.size) }

            for (d in detections.indices) {
                for (t in trackers.indices) {
                    // Strict rule: Trackers only associate with detections of the SAME class
                    if (detections[d].classId == trackers[t].classId) {
                        iouMatrix[d][t] = calculateIoU(detections[d].boundingBox, predictedBoxes[t])
                    } else {
                        iouMatrix[d][t] = 0f
                    }
                }
            }

            // Greedy matching by highest IoU
            while (true) {
                var bestIou = 0f
                var bestD = -1
                var bestT = -1

                for (d in detections.indices) {
                    if (d in matchedIndices) continue
                    for (t in trackers.indices) {
                        if (t in matchedTrackers) continue
                        if (iouMatrix[d][t] > bestIou) {
                            bestIou = iouMatrix[d][t]
                            bestD = d
                            bestT = t
                        }
                    }
                }

                if (bestIou >= iouThreshold && bestD != -1 && bestT != -1) {
                    matches.add(Pair(bestD, bestT))
                    matchedIndices.add(bestD)
                    matchedTrackers.add(bestT)
                } else {
                    break
                }
            }
        }

        // 3. Update matched trackers
        for ((dIdx, tIdx) in matches) {
            trackers[tIdx].update(detections[dIdx])
        }

        // 4. Create new trackers for unmatched detections
        for (d in detections.indices) {
            if (d !in matchedIndices) {
                trackers.add(KalmanBoxTracker(detections[d]))
            }
        }

        // 5. Remove dead trackers (lost longer than maxAge)
        val iterator = trackers.iterator()
        while (iterator.hasNext()) {
            val trk = iterator.next()
            if (trk.timeSinceUpdate > maxAge) {
                iterator.remove()
            }
        }

        // 6. Return active, confirmed tracks
        val activeTracks = mutableListOf<TrackedDetection>()
        for (trk in trackers) {
            // Confirm track if it has enough consecutive hits or has been active recently
            if (trk.timeSinceUpdate < 1 && (trk.hitStreak >= minHits || frameCount <= minHits)) {
                val box = trk.getBoundingBox()
                activeTracks.add(
                    TrackedDetection(
                        trackId = trk.id,
                        classId = trk.classId,
                        label = trk.label,
                        confidence = trk.confidence,
                        boundingBox = RectF(
                            box.left.coerceIn(0f, 1f),
                            box.top.coerceIn(0f, 1f),
                            box.right.coerceIn(0f, 1f),
                            box.bottom.coerceIn(0f, 1f)
                        )
                    )
                )
            }
        }

        return activeTracks
    }

    fun clear() {
        trackers.clear()
        KalmanBoxTracker.resetCounter()
        frameCount = 0
    }

    private fun calculateIoU(a: RectF, b: RectF): Float {
        val interLeft = max(a.left, b.left)
        val interTop = max(a.top, b.top)
        val interRight = min(a.right, b.right)
        val interBottom = min(a.bottom, b.bottom)

        val interArea = max(0f, interRight - interLeft) * max(0f, interBottom - interTop)
        val unionArea = (a.width() * a.height()) + (b.width() * b.height()) - interArea

        return if (unionArea <= 0f) 0f else interArea / unionArea
    }
}
