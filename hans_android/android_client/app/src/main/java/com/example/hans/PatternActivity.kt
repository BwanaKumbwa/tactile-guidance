package com.example.hans

import com.google.android.material.bottomnavigation.BottomNavigationView
import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.NumberPicker
import android.widget.EditText
import android.graphics.Paint
import android.widget.ImageView
import androidx.appcompat.app.AppCompatActivity

class PatternActivity : AppCompatActivity() {

    private val PATTERN_PREFS = "PatternPrefs"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_pattern)

        val patterns = arrayOf(
            "Single Motor Continuous",
            "Multi Motor Continuous",
            "Multi Motor Sequence")

        val picker = findViewById<NumberPicker>(R.id.patternPicker)
        val selectButton = findViewById<Button>(R.id.button_select_pattern)

        picker.minValue = 0
        picker.maxValue = patterns.size - 1
        picker.displayedValues = patterns
        picker.wrapSelectorWheel = false
        picker.scaleX = 2.5f
        picker.scaleY = 2.5f

        // Load saved pattern index
        val prefs = getSharedPreferences(PATTERN_PREFS, MODE_PRIVATE)
        val savedIndex = prefs.getInt("PATTERN_INDEX", 0).coerceIn(0, patterns.size - 1)
        picker.value = savedIndex

        // Save selected pattern
        selectButton.setOnClickListener {

            val selectedIndex = picker.value

            val patternCode = when(selectedIndex) {
                0 -> "VIB_PATTERN_SINGLE"
                1 -> "VIB_PATTERN_MULTI"
                2 -> "VIB_PATTERN_SEQ"
                else -> "VIB_PATTERN_SINGLE"
            }

            prefs.edit()
                .putInt("PATTERN_INDEX", selectedIndex)
                .putString("PATTERN_CODE", patternCode)
                .apply()

            finish()
        }

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
    }
}