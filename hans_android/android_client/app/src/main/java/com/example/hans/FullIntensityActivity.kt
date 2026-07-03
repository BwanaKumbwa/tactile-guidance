package com.example.hans

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.TextView
import android.widget.Button
import android.widget.LinearLayout
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.bottomnavigation.BottomNavigationView
import android.widget.ProgressBar

class FullIntensityActivity : AppCompatActivity() {

    private val PREFS_NAME = "FullIntensityPrefs"

    private lateinit var leftValue: TextView
    private lateinit var rightValue: TextView
    private lateinit var topValue: TextView
    private lateinit var bottomValue: TextView
    private lateinit var topFrontValue: TextView
    private lateinit var topBackValue: TextView
    private lateinit var leftProgress: ProgressBar
    private lateinit var rightProgress: ProgressBar
    private lateinit var topProgress: ProgressBar
    private lateinit var bottomProgress: ProgressBar
    private lateinit var topFrontProgress: ProgressBar
    private lateinit var topBackProgress: ProgressBar

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_fullintensity)

        // bind value text only
        leftValue = findViewById(R.id.leftValue)
        rightValue = findViewById(R.id.rightValue)
        topValue = findViewById(R.id.topValue)
        bottomValue = findViewById(R.id.bottomValue)
        topFrontValue = findViewById(R.id.topFrontValue)
        topBackValue = findViewById(R.id.topBackValue)
        leftProgress = findViewById(R.id.leftProgress)
        rightProgress = findViewById(R.id.rightProgress)
        topProgress = findViewById(R.id.topProgress)
        bottomProgress = findViewById(R.id.downProgress)
        topFrontProgress = findViewById(R.id.topFrontProgress)
        topBackProgress = findViewById(R.id.topBackProgress)

        val bottomNav = findViewById<BottomNavigationView>(R.id.bottomNavigation)

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

        // Intensity default for initialization = 50
        initializeDefaultValues()

        // CLICK DIRECTION -> open calibration page
        findViewById<LinearLayout>(R.id.leftLabel).setOnClickListener { openCalibration("left") }
        findViewById<LinearLayout>(R.id.rightLabel).setOnClickListener { openCalibration("right") }
        findViewById<LinearLayout>(R.id.topLabel).setOnClickListener { openCalibration("top") }
        findViewById<LinearLayout>(R.id.bottomLabel).setOnClickListener { openCalibration("down") }
        findViewById<LinearLayout>(R.id.topFrontLabel).setOnClickListener { openCalibration("topFront") }
        findViewById<LinearLayout>(R.id.topBackLabel).setOnClickListener { openCalibration("topBack") }

        loadValues()

        val selectButton = findViewById<Button>(R.id.button_select_intensity)

        selectButton.setOnClickListener {

            saveIntensity("leftIntensity", getValue("left"))
            saveIntensity("bottomIntensity", getValue("down"))
            saveIntensity("rightIntensity", getValue("right"))
            saveIntensity("topIntensity", getValue("top"))
            saveIntensity("topFrontIntensity", getValue("topFront"))
            saveIntensity("topBackIntensity", getValue("topBack"))

            finish()
        }
    }

    override fun onResume() {
        super.onResume()
        loadValues()
    }

    private fun initializeDefaultValues() {

        val prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val editor = prefs.edit()

        if (!prefs.contains("left")) {
            editor.putInt("left", 50)}

        if (!prefs.contains("right")) {
            editor.putInt("right", 50)}

        if (!prefs.contains("top")) {
            editor.putInt("top", 50)}

        if (!prefs.contains("down")) {
            editor.putInt("down", 50)}

        if (!prefs.contains("topFront")) {
            editor.putInt("topFront", 50)}

        if (!prefs.contains("topBack")) {
            editor.putInt("topBack", 50)}

        editor.apply()
    }

    private fun openCalibration(direction: String) {
        val currentValue = getValue(direction)

        val intent = Intent(this, CalibrationActivity::class.java)
        intent.putExtra("direction", direction)
        intent.putExtra("value", currentValue)

        startActivity(intent)
    }

    private fun loadValues() {
        updateItem(leftValue, leftProgress, getValue("left"))
        updateItem(rightValue, rightProgress, getValue("right"))
        updateItem(topValue, topProgress, getValue("top"))
        updateItem(bottomValue, bottomProgress, getValue("down"))
        updateItem(topFrontValue, topFrontProgress, getValue("topFront"))
        updateItem(topBackValue, topBackProgress, getValue("topBack"))
    }

    private fun updateItem(
        valueView: TextView,
        progressBar: ProgressBar,
        value: Int
    ) {
        valueView.text = value.toString()
        progressBar.progress = value
    }

    private fun getValue(key: String): Int {
        val prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        return prefs.getInt(key, 50)
    }

    private fun saveIntensity(key: String, value: Int) {

        val prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

        prefs.edit()
            .putInt(key, value)
            .apply()
    }

}