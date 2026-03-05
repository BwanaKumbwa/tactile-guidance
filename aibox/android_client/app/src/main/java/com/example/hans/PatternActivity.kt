package com.example.hans

import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.NumberPicker
import android.widget.EditText
import android.graphics.Paint
import android.widget.ImageView
import androidx.appcompat.app.AppCompatActivity

class PatternActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_pattern)

        val patterns = arrayOf(
            "Continuous",
            "Fast Long Pulse",
            "Slow Long Pulse",
            "Fast Short Pulse",
            "Slow Short Pulse"
        )

        val picker = findViewById<NumberPicker>(R.id.patternPicker)
        val selectButton = findViewById<Button>(R.id.button_select_pattern)

        picker.minValue = 0
        picker.maxValue = patterns.size - 1
        picker.displayedValues = patterns
        picker.wrapSelectorWheel = false
        picker.scaleX = 2.5f
        picker.scaleY = 2.5f

        // Load saved pattern index
        val prefs = getSharedPreferences("guidehand_prefs", MODE_PRIVATE)
        val savedIndex = prefs.getInt("PATTERN_INDEX", 0).coerceIn(0, patterns.size - 1)
        picker.value = savedIndex

        // Save selected pattern
        selectButton.setOnClickListener {
            val selectedIndex = picker.value
            prefs.edit()
                .putInt("PATTERN_INDEX", selectedIndex)
                .apply()
            finish()
        }

        // Home icon: go to BluetoothActivity
        val homeIcon = findViewById<ImageView>(R.id.Home)
        homeIcon.setOnClickListener {
            startActivity(
                Intent(this, BluetoothActivity::class.java)
            )
            finish() // optional: prevents stacking activities
        }

        // Setting icon: go to SettingsActivity
        val settingIcon = findViewById<ImageView>(R.id.Setting)
        settingIcon.setOnClickListener {
            startActivity(
                Intent(this, SettingsActivity::class.java)
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
    }
}
