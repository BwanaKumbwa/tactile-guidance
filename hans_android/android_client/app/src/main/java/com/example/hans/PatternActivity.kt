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
    private lateinit var singleMotor: RelativeLayout
    private lateinit var multiMotor: RelativeLayout
    private lateinit var sequenceMotor: RelativeLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_pattern)

        singleMotor = findViewById(R.id.button_single)
        multiMotor = findViewById(R.id.button_multi)
        sequenceMotor = findViewById(R.id.button_sequence)

        val selectButton = findViewById<Button>(R.id.button_select_pattern)

        val prefs = getSharedPreferences(PATTERN_PREFS, MODE_PRIVATE)

        // Load pattern yang tersimpan
        selectedIndex = prefs.getInt("PATTERN_INDEX", 0)

        updateSelection()

        // Pilih Single
        singleMotor.setOnClickListener {
            selectedIndex = 0
            updateSelection()
        }

        // Pilih Multi Continuous
        multiMotor.setOnClickListener {
            selectedIndex = 1
            updateSelection()
        }

        // Pilih Sequence
        sequenceMotor.setOnClickListener {
            selectedIndex = 2
            updateSelection()
        }

        // Simpan pilihan
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
        singleMotor.setBackgroundResource(R.drawable.bg_outline)
        multiMotor.setBackgroundResource(R.drawable.bg_outline)
        sequenceMotor.setBackgroundResource(R.drawable.bg_outline)

        when (selectedIndex) {
            0 -> singleMotor.setBackgroundResource(R.drawable.bg_green)
            1 -> multiMotor.setBackgroundResource(R.drawable.bg_green)
            2 -> sequenceMotor.setBackgroundResource(R.drawable.bg_green)
        }
    }
}