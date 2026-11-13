package com.example.hans

import android.app.Activity
import android.content.Context
import android.content.IntentFilter
import android.os.Build
import android.util.Log

class ExperimentCommandControls(
    private val activity: Activity,
    private val targetObject: () -> String
) {
    private var receiver: ExperimentCommandReceiver? = null

    fun start() {
        if (receiver != null) return
        val logger = ExperimentTrialLogger.forCurrentSession(activity) ?: return
        val commandReceiver = ExperimentCommandReceiver(
            logger = logger,
            targetObject = targetObject,
            onClose = activity::finishAndRemoveTask
        )
        val filter = IntentFilter(ExperimentTrialLogger.COMMAND_ACTION)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            activity.registerReceiver(commandReceiver, filter, Context.RECEIVER_EXPORTED)
        } else {
            @Suppress("DEPRECATION")
            activity.registerReceiver(commandReceiver, filter)
        }
        receiver = commandReceiver
        Log.i(
            TAG,
            "Terminal control: adb shell am broadcast -a ${ExperimentTrialLogger.COMMAND_ACTION} " +
                "-p ${activity.packageName} --es ${ExperimentCommandReceiver.EXTRA_COMMAND} <S/R/T/O/Y/N/F/C>"
        )
    }

    fun stop() {
        receiver?.let {
            activity.unregisterReceiver(it)
            receiver = null
        }
    }

    companion object {
        private const val TAG = "ExperimentLogger"
    }
}
