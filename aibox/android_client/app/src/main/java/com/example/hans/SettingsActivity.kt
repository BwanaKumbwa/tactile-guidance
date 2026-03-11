package com.example.hans

import android.content.Intent
import android.os.Bundle
import android.widget.Button
import androidx.appcompat.app.AppCompatActivity
import android.widget.ImageView

class SettingsActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)

        // Home icon: go to BluetoothActivity
        val homeIcon = findViewById<ImageView>(R.id.Home)
        homeIcon.setOnClickListener {
            startActivity(
                Intent(this, BluetoothActivity::class.java)
            )
            finish() // optional: prevents stacking activities
        }

        // Camera icon: go to MainActivity
        val cameraIcon = findViewById<ImageView>(R.id.Camera_command)
        cameraIcon.setOnClickListener {
            startActivity(
                Intent(this, MainActivity::class.java)
            )
            finish() // optional: prevents stacking activities
        }

        // Find the button
        val intensityButton = findViewById<Button>(R.id.button_intensity)

        // When clicked → go to IntensityActivity
        intensityButton.setOnClickListener {
            startActivity(
                Intent(this, FullIntensityActivity::class.java)
            )
        }

        // Find the button
        val patternButton = findViewById<Button>(R.id.button_pattern)

        // When clicked → go to PatternActivity
        patternButton.setOnClickListener {
            startActivity(
                Intent(this, PatternActivity::class.java)
            )
        }
    }
}
