package com.example.hans

import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.RadioGroup
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity

class ExperimentSetupActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_experiment_setup)

        val participantIdInput = findViewById<EditText>(R.id.inputParticipantId)
        val conditionGroup = findViewById<RadioGroup>(R.id.conditionGroup)

        findViewById<Button>(R.id.buttonStartExperiment).setOnClickListener {
            val participantId = participantIdInput.text.toString().trim()
            if (participantId.isEmpty()) {
                participantIdInput.error = getString(R.string.participant_id_required)
                participantIdInput.requestFocus()
                return@setOnClickListener
            }

            val condition = when (conditionGroup.checkedRadioButtonId) {
                R.id.condition0 -> 0
                R.id.condition1 -> 1
                R.id.condition2 -> 2
                else -> {
                    Toast.makeText(this, R.string.condition_required, Toast.LENGTH_SHORT).show()
                    return@setOnClickListener
                }
            }

            ExperimentSession.startExperiment(applicationContext, participantId, condition)
            startActivity(Intent(this, MainActivity::class.java))
            finish()
        }
    }
}
