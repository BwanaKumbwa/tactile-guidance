package com.example.hans

import android.content.Intent
import android.os.Bundle
import android.widget.Button
import androidx.appcompat.app.AppCompatActivity

class ModeSelectionActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_mode_selection)

        findViewById<Button>(R.id.buttonExperiment).setOnClickListener {
            startActivity(Intent(this, ExperimentSetupActivity::class.java))
            finish()
        }

        findViewById<Button>(R.id.buttonUsage).setOnClickListener {
            ExperimentSession.selectUsage(applicationContext)
            startActivity(Intent(this, MainActivity::class.java))
            finish()
        }
    }
}
