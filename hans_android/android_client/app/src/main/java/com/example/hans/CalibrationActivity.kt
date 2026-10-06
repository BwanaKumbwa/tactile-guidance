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
import android.util.Log
import android.view.ViewGroup
import androidx.core.content.ContextCompat

class CalibrationActivity : AppCompatActivity() {

    private val PREFS_NAME = "FullIntensityPrefs"

    private var value = 0
    private var direction = ""
    private lateinit var bottomNav: BottomNavigationView
    private lateinit var valueText: TextView
    private lateinit var directionText: TextView
    private lateinit var titleText: TextView
    private lateinit var selectButton: Button
    private lateinit var circularProgress: CircularProgressIndicator
    private lateinit var braceletManager: BleManager

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_calibration)

        braceletManager = BleManagerSingleton.getBraceletManager(this)

        // Use the volume button
        setVolumeControlStream(AudioManager.STREAM_MUSIC)

        circularProgress = findViewById(R.id.circularProgress)
        bottomNav = findViewById(R.id.bottomNavigation)
        bottomNav.selectedItemId = R.id.menu_setting
        bottomNav.post {
            updateBottomNavBackground(R.id.menu_setting)
        }
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

        // Take the data from FullIntensityActivity
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
            "belt" -> "BELT"
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
                previewVibration()
                return true
            }

            KeyEvent.KEYCODE_VOLUME_DOWN -> {
                value = (value - 5).coerceAtLeast(5)
                updateUI()
                previewVibration()
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

    private fun previewVibration() {
        val motor = when (direction) {
            "left" -> 0x00
            "down" -> 0x01
            "right" -> 0x02
            "top" -> 0x03
            "topFront" -> 0x04
            "topBack" -> 0x05
            else -> return
        }

        val duration = 500

        val command = byteArrayOf(
            0x0E.toByte(),
            0x04.toByte(),
            motor.toByte(),
            value.toByte(),
            ((duration shr 8) and 0xFF).toByte(),
            (duration and 0xFF).toByte()
        )

        if (braceletManager.isConnected()) {
            braceletManager.writeRawCommand(command)
        }
    }
    private fun updateBottomNavBackground(page: Int) {

        val menuView = bottomNav.getChildAt(0) as ViewGroup

        val home = menuView.getChildAt(0)
        val camera = menuView.getChildAt(1)
        val setting = menuView.getChildAt(2)

        val rectangle = ContextCompat.getDrawable(this, R.drawable.bottom_rectangle)
        val rectangleSelected = ContextCompat.getDrawable(this, R.drawable.bottom_rectangle_selected)
        val topLeft = ContextCompat.getDrawable(this, R.drawable.bottom_top_left)
        val topLeftSelected = ContextCompat.getDrawable(this, R.drawable.bottom_top_left_selected)
        val topRight = ContextCompat.getDrawable(this, R.drawable.bottom_top_right)
        val topRightSelected = ContextCompat.getDrawable(this, R.drawable.bottom_top_right_selected)

        when (page) {
            // HOME
            R.id.menu_home -> {

                home.background = rectangleSelected
                camera.background = topLeft
                setting.background = rectangle
            }

            // CAMERA
            R.id.menu_camera -> {

                home.background = topRight
                camera.background = rectangleSelected
                setting.background = topLeft
            }

            // SETTINGS
            R.id.menu_setting -> {

                home.background = rectangle
                camera.background = topRight
                setting.background = rectangleSelected
            }
        }
    }
}