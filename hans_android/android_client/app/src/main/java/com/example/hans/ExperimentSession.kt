package com.example.hans

import android.content.Context

object ExperimentSession {
    private const val PREFERENCES_NAME = "experiment_session"
    private const val KEY_MODE = "mode"
    private const val KEY_PARTICIPANT_ID = "participant_id"
    private const val KEY_CONDITION = "condition"

    enum class Mode {
        USAGE,
        EXPERIMENT
    }

    data class State(
        val mode: Mode,
        val participantId: String?,
        val condition: Int?
    )

    fun selectUsage(context: Context) {
        preferences(context).edit()
            .putString(KEY_MODE, Mode.USAGE.name)
            .apply()
    }

    fun startExperiment(context: Context, participantId: String, condition: Int) {
        require(condition in 0..2)
        preferences(context).edit()
            .putString(KEY_MODE, Mode.EXPERIMENT.name)
            .putString(KEY_PARTICIPANT_ID, participantId)
            .putInt(KEY_CONDITION, condition)
            .apply()
    }

    fun current(context: Context): State {
        val prefs = preferences(context)
        val mode = if (prefs.getString(KEY_MODE, Mode.USAGE.name) == Mode.EXPERIMENT.name) {
            Mode.EXPERIMENT
        } else {
            Mode.USAGE
        }
        val condition = if (prefs.contains(KEY_CONDITION)) {
            prefs.getInt(KEY_CONDITION, -1).takeIf { it in 0..2 }
        } else {
            null
        }

        return State(
            mode = mode,
            participantId = prefs.getString(KEY_PARTICIPANT_ID, null),
            condition = condition
        )
    }

    private fun preferences(context: Context) =
        context.getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)
}
