package com.example.hans

import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.NumberPicker
import android.widget.EditText
import android.graphics.Paint
import android.widget.ImageView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.size

class IntensityActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_intensity)

        val picker = findViewById<NumberPicker>(R.id.intensityPicker)
        val selectButton = findViewById<Button>(R.id.button_select_intensity)

        val step = 5
        val values = (0..100 step step).map { it.toString() }.toTypedArray()

        picker.minValue = 0
        picker.maxValue = values.size - 1
        picker.wrapSelectorWheel = false
        picker.scaleX = 2.5f
        picker.scaleY = 2.5f

        // Load saved intensity (default 50)
        val prefs = getSharedPreferences("guidehand_prefs", MODE_PRIVATE)
        val savedIntensity = prefs.getInt("INTENSITY", 50)

        // Map saved intensity to picker index safely
        picker.displayedValues = values
        val index = (savedIntensity / step).coerceIn(0, values.size - 1)
        picker.value = index

        selectButton.setOnClickListener {
            val selectedIntensity = values[picker.value].toInt()
            prefs.edit()
                .putInt("INTENSITY", selectedIntensity)
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
