package com.example.hans

import com.google.android.material.bottomnavigation.BottomNavigationView
import android.content.Intent
import android.os.Bundle
import android.widget.Button

import androidx.appcompat.app.AppCompatActivity
import android.widget.RelativeLayout
class PatternActivity : AppCompatActivity() {

    private val PATTERN_PREFS = "PatternPrefs"
    private var selectedIndex = 0
    private lateinit var VIB_PATTERN_SINGLE: RelativeLayout
    private lateinit var VIB_PATTERN_MULTI: RelativeLayout
    private lateinit var VIB_PATTERN_SEQ: RelativeLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_pattern)

        VIB_PATTERN_SINGLE = findViewById(R.id.button_single)
        VIB_PATTERN_MULTI = findViewById(R.id.button_multi)
        VIB_PATTERN_SEQ = findViewById(R.id.button_sequence)

        val selectButton = findViewById<Button>(R.id.button_select_pattern)

        val prefs = getSharedPreferences(PATTERN_PREFS, MODE_PRIVATE)

        // Load pattern that is saved
        selectedIndex = prefs.getInt("PATTERN_INDEX", 0)

        updateSelection()

        // Single Pattern
        VIB_PATTERN_SINGLE.setOnClickListener {
            selectedIndex = 0
            updateSelection()
        }

        // Multimotor Continuous Pattern
        VIB_PATTERN_MULTI.setOnClickListener {
            selectedIndex = 1
            updateSelection()
        }

        // Multimotor Sequence Pattern
        VIB_PATTERN_SEQ.setOnClickListener {
            selectedIndex = 2
            updateSelection()
        }

        // Save option
        selectButton.setOnClickListener {

            val patternCode = when (selectedIndex) {
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

    private fun updateSelection() {
        VIB_PATTERN_SINGLE.setBackgroundResource(R.drawable.bg_outline)
        VIB_PATTERN_MULTI.setBackgroundResource(R.drawable.bg_outline)
        VIB_PATTERN_SEQ.setBackgroundResource(R.drawable.bg_outline)

        when (selectedIndex) {
            0 -> VIB_PATTERN_SINGLE.setBackgroundResource(R.drawable.bg_green)
            1 -> VIB_PATTERN_MULTI.setBackgroundResource(R.drawable.bg_green)
            2 -> VIB_PATTERN_SEQ.setBackgroundResource(R.drawable.bg_green)
        }
    }
}