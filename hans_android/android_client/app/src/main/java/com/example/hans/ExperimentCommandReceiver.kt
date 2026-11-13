package com.example.hans

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import java.io.IOException

class ExperimentCommandReceiver(
    private val logger: ExperimentTrialLogger,
    private val targetObject: () -> String,
    private val onClose: () -> Unit
) : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val command = intent.getStringExtra(EXTRA_COMMAND)
        if (command.isNullOrBlank()) {
            Log.w(TAG, "Experiment command ignored: missing command extra")
            return
        }

        try {
            if (!logger.handleCommand(command, targetObject())) {
                Log.w(TAG, "Unknown experiment command: $command")
            } else if (command.trim().equals("C", ignoreCase = true)) {
                onClose()
            }
        } catch (error: IOException) {
            Log.e(TAG, "Could not save experiment log", error)
        }
    }

    companion object {
        const val EXTRA_COMMAND = "command"
        private const val TAG = "ExperimentLogger"
    }
}
