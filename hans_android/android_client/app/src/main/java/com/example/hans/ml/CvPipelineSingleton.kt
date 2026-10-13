package com.example.hans.ml

import android.content.Context
import com.example.hans.ml.tracker.SortTracker

object CvPipelineSingleton {
    @Volatile
    private var pipeline: DualYoloPipeline? = null

    val tracker = SortTracker(maxAge = 30, minHits = 2, iouThreshold = 0.30f)

    @Synchronized
    fun getPipeline(context: Context): DualYoloPipeline {
        if (pipeline == null) {
            pipeline = DualYoloPipeline(
                context = context.applicationContext,
                objModelFileName = "yolov5s-fp16.tflite",
                handModelFileName = "hand-fp16.tflite",
                objLabelsFileName = "coco_labels.txt",
                handLabelsFileName = "hand_labels.txt",
                confThreshold = 0.35f,
                iouThreshold = 0.45f
            )
        }
        return pipeline!!
    }

    fun isInitialized(): Boolean = pipeline != null

    fun destroy() {
        pipeline?.close()
        pipeline = null
        tracker.clear()
    }
}
