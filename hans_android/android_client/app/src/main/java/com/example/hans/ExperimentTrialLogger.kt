package com.example.hans

import android.content.Context
import android.util.AtomicFile
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

class ExperimentTrialLogger private constructor(
    context: Context,
    private val participantId: String,
    private val condition: Int
) {
    private val preferences = context.getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)
    private val storageKey = "trials_${participantId}_$condition"
    private val activeTrialKey = "active_trial_${participantId}_$condition"
    private val logDirectory = File(context.filesDir, "experiment_logs")
    private val csv = AtomicFile(
        File(
            logDirectory,
            "participant_${fileSafe(participantId)}_condition_$condition.csv"
        ).also {
            if (!logDirectory.exists() && !logDirectory.mkdirs()) {
                throw IllegalStateException("Could not create experiment log directory")
            }
        }
    )
    private val records = loadRecords()
    private var activeTrialNumber = preferences.getInt(activeTrialKey, NO_ACTIVE_TRIAL)

    init {
        persist()
        Log.i(TAG, "Experiment CSV: ${csv.baseFile.absolutePath}")
    }

    @Synchronized
    @Throws(IOException::class)
    fun handleCommand(command: String, targetObject: String = "NA"): Boolean {
        val normalized = command.trim().uppercase(Locale.ROOT)
        if (normalized.length != 1) return false

        when (normalized[0]) {
            'S' -> startTrial(targetObject)
            'R' -> recordRecoverySuccess()
            'T' -> incrementWrongTargetCount()
            'O' -> incrementOvershootCount()
            'Y' -> endTrial("success")
            'N' -> endTrial("participant_failure")
            'F' -> endTrial("system_failure")
            'C' -> closeSession()
            else -> return false
        }
        return true
    }

    private fun startTrial(targetObject: String) {
        if (activeRecord() != null) {
            Log.w(TAG, "A trial is already active")
            return
        }

        val record = TrialRecord(
            participantId = participantId,
            condition = condition,
            trialNumber = (records.maxOfOrNull { it.trialNumber } ?: 0) + 1,
            targetObject = targetObject.takeUnless { it.isBlank() || it.equals("none", true) } ?: "NA",
            trialStartTime = timestamp()
        )
        records.add(record)
        activeTrialNumber = record.trialNumber
        persist()
        Log.i(TAG, "Trial ${record.trialNumber} started")
    }

    private fun recordRecoverySuccess() {
        val record = activeRecord() ?: return commandWithoutTrial('R')
        record.recoverySuccess = true
        record.recoverySuccessTime = timestamp()
        persist()
        Log.i(TAG, "Recovery success recorded")
    }

    private fun incrementWrongTargetCount() {
        val record = activeRecord() ?: return commandWithoutTrial('T')
        record.wrongTargetCount++
        persist()
        Log.i(TAG, "Wrong target count: ${record.wrongTargetCount}")
    }

    private fun incrementOvershootCount() {
        val record = activeRecord() ?: return commandWithoutTrial('O')
        record.overshootCount++
        persist()
        Log.i(TAG, "Overshoot count: ${record.overshootCount}")
    }

    private fun endTrial(outcome: String) {
        val record = activeRecord() ?: return commandWithoutTrial(outcome.first())
        record.outcome = outcome
        record.trialEndTime = timestamp()
        activeTrialNumber = NO_ACTIVE_TRIAL
        persist()
        Log.i(TAG, "Trial ${record.trialNumber} ended: $outcome")
    }

    private fun closeSession() {
        activeTrialNumber = NO_ACTIVE_TRIAL
        persist()
        Log.i(TAG, "Experiment session saved and closed")
    }

    private fun commandWithoutTrial(command: Char) {
        Log.w(TAG, "Command $command ignored: no active trial")
    }

    private fun activeRecord(): TrialRecord? =
        records.lastOrNull { it.trialNumber == activeTrialNumber }

    private fun loadRecords(): MutableList<TrialRecord> {
        val encoded = preferences.getString(storageKey, null) ?: return mutableListOf()
        val rows = JSONArray(encoded)
        return MutableList(rows.length()) { index ->
            TrialRecord.fromJson(rows.getJSONObject(index))
        }
    }

    @Throws(IOException::class)
    private fun persist() {
        val editor = preferences.edit()
            .putString(storageKey, JSONArray().apply {
                records.forEach { put(it.toJson()) }
            }.toString())
            .putInt(activeTrialKey, activeTrialNumber)
        if (!editor.commit()) {
            throw IOException("Could not save experiment state")
        }

        val content = buildString {
            append(CSV_HEADER)
            append('\n')
            records.forEach { record ->
                append(record.csvRow())
                append('\n')
            }
        }.toByteArray(Charsets.UTF_8)

        val output = csv.startWrite()
        try {
            output.write(content)
            csv.finishWrite(output)
        } catch (error: IOException) {
            csv.failWrite(output)
            throw error
        }
    }

    private data class TrialRecord(
        val participantId: String,
        val condition: Int,
        val trialNumber: Int,
        val targetObject: String,
        val trialStartTime: String,
        var recoverySuccess: Boolean = false,
        var recoverySuccessTime: String = "",
        var wrongTargetCount: Int = 0,
        var overshootCount: Int = 0,
        var outcome: String = "",
        var trialEndTime: String = ""
    ) {
        fun toJson() = JSONObject().apply {
            put("participant_id", participantId)
            put("condition", condition)
            put("trial_number", trialNumber)
            put("target_object", targetObject)
            put("trial_start_time", trialStartTime)
            put("recovery_success", recoverySuccess)
            put("recovery_success_time", recoverySuccessTime)
            put("wrong_target_count", wrongTargetCount)
            put("overshoot_count", overshootCount)
            put("outcome", outcome)
            put("trial_end_time", trialEndTime)
        }

        fun csvRow() = listOf(
            participantId,
            condition.toString(),
            trialNumber.toString(),
            targetObject,
            trialStartTime,
            recoverySuccess.toString(),
            recoverySuccessTime,
            wrongTargetCount.toString(),
            overshootCount.toString(),
            outcome,
            trialEndTime
        ).joinToString(",") { csvEscape(it) }

        companion object {
            fun fromJson(json: JSONObject) = TrialRecord(
                participantId = json.getString("participant_id"),
                condition = json.getInt("condition"),
                trialNumber = json.getInt("trial_number"),
                targetObject = json.getString("target_object"),
                trialStartTime = json.getString("trial_start_time"),
                recoverySuccess = json.getBoolean("recovery_success"),
                recoverySuccessTime = json.getString("recovery_success_time"),
                wrongTargetCount = json.getInt("wrong_target_count"),
                overshootCount = json.getInt("overshoot_count"),
                outcome = json.getString("outcome"),
                trialEndTime = json.getString("trial_end_time")
            )
        }
    }

    companion object {
        const val COMMAND_ACTION = "com.example.hans.EXPERIMENT_COMMAND"
        private const val TAG = "ExperimentLogger"
        private const val PREFERENCES_NAME = "experiment_trial_logs"
        private const val NO_ACTIVE_TRIAL = 0
        private const val CSV_HEADER =
            "participant_id,condition,trial_number,target_object,trial_start_time," +
                "recovery_success,recovery_success_time,wrong_target_count,overshoot_count," +
                "outcome,trial_end_time"

        fun forCurrentSession(context: Context): ExperimentTrialLogger? {
            val session = ExperimentSession.current(context)
            if (session.mode != ExperimentSession.Mode.EXPERIMENT) return null
            val participantId = session.participantId ?: return null
            val condition = session.condition ?: return null
            return ExperimentTrialLogger(context.applicationContext, participantId, condition)
        }

        private fun timestamp(): String {
            val format = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US)
            format.timeZone = TimeZone.getTimeZone("UTC")
            return format.format(Date())
        }

        private fun csvEscape(value: String): String =
            "\"${value.replace("\"", "\"\"")}\""

        private fun fileSafe(value: String): String =
            value.replace(Regex("[^A-Za-z0-9_-]"), "_")
    }
}
