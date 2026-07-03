package com.example.hans

import android.media.AudioManager
import android.os.Bundle
import android.content.Intent
import android.view.KeyEvent
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.bottomnavigation.BottomNavigationView
import com.google.android.material.progressindicator.CircularProgressIndicator

class CalibrationActivity : AppCompatActivity() {

    private val PREFS_NAME = "FullIntensityPrefs"

    private var value = 0
    private var direction = ""

    private lateinit var valueText: TextView
    private lateinit var directionText: TextView
    private lateinit var titleText: TextView
    private lateinit var selectButton: Button
    private lateinit var circularProgress: CircularProgressIndicator

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_calibration)

        // Gunakan tombol volume
        setVolumeControlStream(AudioManager.STREAM_MUSIC)

        val bottomNav = findViewById<BottomNavigationView>(R.id.bottomNavigation)
        circularProgress = findViewById(R.id.circularProgress)
        bottomNav.selectedItemId = R.id.menu_setting
        bottomNav.setOnItemSelectedListener { item ->

            when (item.itemId) {

                R.id.menu_home -> {
                    startActivity(Intent(this, BluetoothActivity::class.java))
                    finish()
                    true
                }

                R.id.menu_camera -> {
                    startActivity(Intent(this, MainActivity::class.java))
                    finish()
                    true
                }

                R.id.menu_setting -> {
                    startActivity(Intent(this, SettingsActivity::class.java))
                    finish()
                    true
                }

                else -> false
            }
        }

        // Ambil data dari FullIntensityActivity
        direction = intent.getStringExtra("direction") ?: ""
        value = intent.getIntExtra("value", 50)

        // Binding View
        valueText = findViewById(R.id.calibrationValue)
        directionText = findViewById(R.id.direction)
        titleText = findViewById(R.id.calibrationTitle)
        selectButton = findViewById(R.id.select_calibration)

        directionText.text = when (direction) {
            "left" -> "LEFT"
            "right" -> "RIGHT"
            "top" -> "TOP"
            "down" -> "DOWN"
            "topFront" -> "TOP FRONT"
            "topBack" -> "TOP BACK"
            else -> "-"
        }

        updateUI()

        // Select Calibration
        selectButton.setOnClickListener {

            saveValue()

            // Back to FullIntensityActivity
            finish()
        }
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent?): Boolean {

        when (keyCode) {

            KeyEvent.KEYCODE_VOLUME_UP -> {
                value = (value + 5).coerceAtMost(100)
                updateUI()
                return true
            }

            KeyEvent.KEYCODE_VOLUME_DOWN -> {
                value = (value - 5).coerceAtLeast(5)
                updateUI()
                return true
            }
        }

        return super.onKeyDown(keyCode, event)
    }

    private fun updateUI() {
        valueText.text = value.toString()
        circularProgress.progress = value
    }

    private fun saveValue() {

        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)

        prefs.edit()
            .putInt(direction, value)
            .apply()
    }
}